"""
LINE Webhook Handler
รับ event จาก LINE platform แล้วจัดการ conversation flow:
  - ผู้ใช้ทัก → ส่งเมนูหลัก
  - ผู้ใช้กด "ตรวจสอบแปลง" → ส่งลิงก์ LIFF แผนที่
  - ผู้ใช้กด "ประวัติการตรวจ" → ดึงผลล่าสุดจาก DB
"""

import hashlib
import hmac
import json
import logging
import os
import base64

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from database import (get_latest_report, upsert_user, set_notify, set_notify_digest,
                      set_notify_hour, PRESET_NOTIFY_HOURS)
from flex_messages import build_result_flex

logger = logging.getLogger(__name__)
router = APIRouter()

LINE_API    = "https://api.line.me/v2/bot/message/reply"

_DB_DOWN = ("⚠️ ระบบฐานข้อมูลขัดข้องชั่วคราว ยังใช้งานส่วนอื่นได้ตามปกติ\n"
            "กรุณาลองใหม่อีกครั้งภายหลังครับ 🙏")


# ─────────────────────────────────────────────────────
# Signature verification (ป้องกัน request ปลอม)
# ─────────────────────────────────────────────────────

def _verify_signature(body: bytes, signature: str) -> bool:
    secret = os.environ.get("LINE_CHANNEL_SECRET", "").encode()
    if not secret:
        logger.warning("LINE_CHANNEL_SECRET not set — skipping signature verification (dev mode)")
        return True
    digest = hmac.new(secret, body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


# ─────────────────────────────────────────────────────
# Webhook endpoint
# ─────────────────────────────────────────────────────

@router.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    """
    ตอบ 200 กลับ LINE ทันที แล้วค่อยประมวลผล event เบื้องหลัง

    LINE รอ response ไม่เกิน ~10 วินาที ถ้าเกินจะถือว่า fail
    และหยุดส่ง event ต่อ (บอทเงียบ) — จึงต้องไม่ทำงานหนักใน request นี้
    """
    body      = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not _verify_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        logger.warning("Webhook received invalid JSON body")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    events = payload.get("events", [])
    if events:
        background.add_task(_process_events, events)

    return {"status": "ok"}


async def _process_events(events: list):
    """ประมวลผล event เบื้องหลัง — error ของ event หนึ่งต้องไม่ล้มทั้งชุด"""
    for event in events:
        event_type = event.get("type")
        try:
            if event_type == "message":
                await _handle_message(event)
            elif event_type == "postback":
                await _handle_postback(event)
            elif event_type == "follow":
                await _handle_follow(event)
        except Exception:
            logger.exception(f"Error handling {event_type} event")


async def _safe(fn, *args) -> bool:
    """
    เรียกงานที่ 'ไม่จำเป็น' (เช่นเขียน DB) แบบไม่ให้ล้มการตอบข้อความ

    ถ้า Supabase ล่มหรือ resolve ไม่ได้ บอทต้องยังตอบเกษตรกรได้ตามปกติ
    คืน True เมื่อสำเร็จ, False เมื่อพัง (log ไว้แล้ว)
    """
    try:
        await fn(*args)
        return True
    except Exception:
        logger.exception(f"non-fatal: {fn.__name__} failed")
        return False


# ─────────────────────────────────────────────────────
# Event handlers
# ─────────────────────────────────────────────────────

async def _handle_follow(event: dict):
    """ผู้ใช้ Add LINE OA → ส่ง welcome message ก่อน แล้วค่อยบันทึก user"""
    reply_token = event["replyToken"]
    user_id     = event["source"]["userId"]

    await _reply(reply_token, [_welcome_message()])   # ตอบก่อนเสมอ

    profile = None
    try:
        profile = await _get_profile(user_id)
    except Exception:
        logger.exception("non-fatal: _get_profile failed")
    display_name = profile.get("displayName", "") if profile else ""
    await _safe(upsert_user, user_id, display_name)


async def _handle_message(event: dict):
    """ผู้ใช้ส่งข้อความ → ส่ง main menu ก่อน แล้วค่อย upsert user"""
    reply_token = event["replyToken"]
    user_id     = event["source"]["userId"]

    await _reply(reply_token, [_main_menu()])         # ตอบก่อนเสมอ
    await _safe(upsert_user, user_id)


async def _handle_postback(event: dict):
    """ผู้ใช้กดปุ่ม postback"""
    reply_token = event["replyToken"]
    data        = event.get("postback", {}).get("data", "")
    user_id     = event["source"]["userId"]

    if data == "action=check":
        await _reply(reply_token, [_liff_button()])

    elif data == "action=history":
        try:
            report = await get_latest_report(user_id)
        except Exception:
            logger.exception("get_latest_report failed")
            await _reply(reply_token, [{"type": "text", "text": _DB_DOWN}])
            return
        if report:
            from rule_engine import format_message
            plain = format_message(report, report["lat"], report["lng"])
            flex  = build_result_flex(report, report["lat"], report["lng"], plain)
            await _reply(reply_token, [flex])
        else:
            await _reply(reply_token, [{"type": "text",
                "text": "ยังไม่มีประวัติการตรวจสอบ กรุณากด 'ตรวจสอบแปลงใหม่' ก่อนครับ 🌿"}])

    elif data == "action=settings":
        await _reply(reply_token, [_settings_menu()])

    elif data == "action=notify_on":
        ok = await _safe(set_notify, user_id, True)
        await _reply(reply_token, [{
            "type": "text",
            "text": "🔔 เปิดการแจ้งเตือนประจำวันแล้ว\nคุณจะได้รับรายงานทุกวัน 07:00 น. หากตรวจพบความเสี่ยง 🌿"
                    if ok else _DB_DOWN
        }])

    elif data == "action=notify_off":
        ok = await _safe(set_notify, user_id, False)
        await _reply(reply_token, [{
            "type": "text",
            "text": "🔕 ปิดการแจ้งเตือนประจำวันแล้ว\nคุณยังสามารถตรวจสอบแปลงเองตลอดเวลา ✅"
                    if ok else _DB_DOWN
        }])

    elif data == "action=digest_on":
        ok = await _safe(set_notify_digest, user_id, True)
        await _reply(reply_token, [{
            "type": "text",
            "text": "📋 เปิดสรุปแปลงประจำวันแล้ว\nทุกเช้า 07:00 น. คุณจะได้รับสรุปสถานะทุกแปลงที่ปักหมุดไว้ ไม่ว่าจะเสี่ยงหรือไม่ 🌿"
                    if ok else _DB_DOWN
        }])

    elif data == "action=digest_off":
        ok = await _safe(set_notify_digest, user_id, False)
        await _reply(reply_token, [{
            "type": "text",
            "text": "🔕 ปิดสรุปแปลงประจำวันแล้ว\nยังสามารถตรวจสอบแปลงเองตลอดเวลาได้ตามปกติ ✅"
                    if ok else _DB_DOWN
        }])

    elif data == "action=time_menu":
        await _reply(reply_token, [_time_picker_menu()])

    elif data.startswith("action=set_hour_"):
        try:
            hour = int(data.rsplit("_", 1)[-1])
        except ValueError:
            hour = -1
        if hour not in PRESET_NOTIFY_HOURS:
            await _reply(reply_token, [{"type": "text", "text": "เวลาที่เลือกไม่ถูกต้อง กรุณาลองใหม่"}])
            return
        ok = await _safe(set_notify_hour, user_id, hour)
        await _reply(reply_token, [{
            "type": "text",
            "text": f"⏰ ตั้งเวลาแจ้งเตือนเป็น {hour:02d}:00 น. แล้ว\n"
                    f"ใช้กับทั้งการแจ้งเตือนเฉพาะตอนเสี่ยง และสรุปแปลงประจำวัน 🌿"
                    if ok else _DB_DOWN
        }])

    elif data == "action=help":
        await _reply(reply_token, [_help_message()])


# ─────────────────────────────────────────────────────
# Message templates
# ─────────────────────────────────────────────────────

def _welcome_message() -> dict:
    return {
        "type": "flex",
        "altText": "ยินดีต้อนรับสู่ GEOai 🌿",
        "contents": {
            "type": "bubble",
            "hero": {
                "type": "box",
                "layout": "vertical",
                "contents": [],
                "backgroundColor": "#1a7a3c",
                "paddingAll": "20px",
                "height": "80px",
                "justifyContent": "center",
                "alignItems": "center"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "🌿 ยินดีต้อนรับสู่ GEOai",
                        "weight": "bold",
                        "size": "xl",
                        "color": "#1a7a3c"
                    },
                    {
                        "type": "text",
                        "text": "ระบบตรวจวิเคราะห์สวนทุเรียนด้วยดาวเทียม\nเพียงปักหมุดแปลงของคุณ เราวิเคราะห์ให้ทันที",
                        "wrap": True,
                        "margin": "md",
                        "size": "sm",
                        "color": "#555555"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "🔍 ตรวจสอบแปลงของฉัน",
                            "data": "action=check"
                        },
                        "style": "primary",
                        "color": "#1a7a3c"
                    }
                ]
            }
        }
    }


def _main_menu() -> dict:
    return {
        "type": "flex",
        "altText": "เมนูหลัก GEOai",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "🌿 GEOai — เมนูหลัก",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#1a7a3c"
                    },
                    {
                        "type": "text",
                        "text": "เลือกสิ่งที่ต้องการด้านล่าง",
                        "size": "sm",
                        "color": "#888888",
                        "margin": "sm"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "🔍 ตรวจสอบแปลงใหม่",
                            "data": "action=check"
                        },
                        "style": "primary",
                        "color": "#1a7a3c"
                    },
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "📋 ดูผลวิเคราะห์ล่าสุด",
                            "data": "action=history"
                        },
                        "style": "secondary"
                    },
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "⚙️ ตั้งค่าการแจ้งเตือน",
                            "data": "action=settings"
                        },
                        "style": "secondary"
                    },
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "❓ วิธีใช้งาน",
                            "data": "action=help"
                        },
                        "style": "secondary"
                    }
                ]
            }
        }
    }


def _liff_button() -> dict:
    liff_url = os.environ.get("LIFF_URL") or "https://liff.line.me/2010580115-di08GvXS"
    return {
        "type": "flex",
        "altText": "กดเพื่อปักหมุดแปลงของคุณ",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "text",
                        "text": "📍 ปักหมุดแปลงทุเรียน",
                        "weight": "bold",
                        "size": "lg",
                        "color": "#1a7a3c"
                    },
                    {
                        "type": "text",
                        "text": "กดปุ่มด้านล่างเพื่อเปิดแผนที่\nจากนั้นจิ้มตำแหน่งแปลงของคุณ\nระบบจะวิเคราะห์ข้อมูลดาวเทียมให้ทันที 🛰️",
                        "wrap": True,
                        "margin": "md",
                        "size": "sm",
                        "color": "#555555"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {
                        "type": "button",
                        "action": {
                            "type": "uri",
                            "label": "🗺️ เปิดแผนที่ปักหมุด",
                            "uri": liff_url
                        },
                        "style": "primary",
                        "color": "#1a7a3c"
                    }
                ]
            }
        }
    }


def _settings_menu() -> dict:
    return {
        "type": "flex",
        "altText": "⚙️ ตั้งค่าการแจ้งเตือน GEOai",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#1a7a3c",
                "paddingAll": "14px",
                "contents": [{
                    "type": "text",
                    "text": "⚙️ ตั้งค่าการแจ้งเตือน",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md"
                }]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "14px",
                "contents": [
                    {
                        "type": "text",
                        "text": "🔔 แจ้งเตือนเฉพาะตอนเสี่ยง",
                        "weight": "bold",
                        "size": "sm",
                        "color": "#333333"
                    },
                    {
                        "type": "text",
                        "text": "ระบบสแกนแปลงของคุณตามเวลาที่ตั้งไว้ (ค่าเริ่มต้น 07:00 น.)\n— ส่งข้อความเฉพาะเมื่อพบความเสี่ยงหรือฝนหนักกำลังจะมา",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666",
                        "margin": "sm"
                    },
                    {"type": "separator", "margin": "lg"},
                    {
                        "type": "text",
                        "text": "📋 สรุปแปลงประจำวัน",
                        "weight": "bold",
                        "size": "sm",
                        "color": "#333333",
                        "margin": "lg"
                    },
                    {
                        "type": "text",
                        "text": "สรุปสถานะทุกแปลงของคุณตามเวลาที่ตั้งไว้\nไม่ว่าจะเสี่ยงหรือไม่ก็ได้รับทุกวัน",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666",
                        "margin": "sm"
                    },
                    {"type": "separator", "margin": "lg"},
                    {
                        "type": "text",
                        "text": "⏰ เวลาแจ้งเตือน",
                        "weight": "bold",
                        "size": "sm",
                        "color": "#333333",
                        "margin": "lg"
                    },
                    {
                        "type": "text",
                        "text": "ใช้เวลาเดียวกันทั้งสองแบบด้านบน — เลือกได้ที่ปุ่ม\n\"⏰ ตั้งเวลาแจ้งเตือน\" ด้านล่าง",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666",
                        "margin": "sm"
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "paddingAll": "12px",
                "contents": [
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": [
                            {
                                "type": "button",
                                "action": {
                                    "type": "postback",
                                    "label": "🔔 เปิด",
                                    "data": "action=notify_on"
                                },
                                "style": "primary",
                                "color": "#1a7a3c"
                            },
                            {
                                "type": "button",
                                "action": {
                                    "type": "postback",
                                    "label": "🔕 ปิด",
                                    "data": "action=notify_off"
                                },
                                "style": "secondary"
                            }
                        ]
                    },
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": [
                            {
                                "type": "button",
                                "action": {
                                    "type": "postback",
                                    "label": "📋 เปิดสรุปรายวัน",
                                    "data": "action=digest_on"
                                },
                                "style": "primary",
                                "color": "#1a7a3c"
                            },
                            {
                                "type": "button",
                                "action": {
                                    "type": "postback",
                                    "label": "🔕 ปิดสรุปรายวัน",
                                    "data": "action=digest_off"
                                },
                                "style": "secondary"
                            }
                        ]
                    },
                    {
                        "type": "button",
                        "action": {
                            "type": "postback",
                            "label": "⏰ ตั้งเวลาแจ้งเตือน",
                            "data": "action=time_menu"
                        },
                        "style": "secondary"
                    }
                ]
            }
        }
    }


def _time_picker_menu() -> dict:
    """เลือกเวลาแจ้งเตือน — ตัวเลือกที่ตั้งไว้ล่วงหน้าเท่านั้น (05:00-10:00) กันพิมพ์
    เวลาเองแล้วรูปแบบผิด ใช้ค่าเดียวกันทั้ง "แจ้งเตือนเฉพาะตอนเสี่ยง" และ "สรุปประจำวัน"
    """
    buttons = [
        {
            "type": "button",
            "action": {
                "type": "postback",
                "label": f"{h:02d}:00 น.",
                "data": f"action=set_hour_{h}"
            },
            "style": "secondary",
        }
        for h in PRESET_NOTIFY_HOURS
    ]
    # เรียงปุ่ม 2 คอลัมน์ต่อแถว
    rows = [
        {"type": "box", "layout": "horizontal", "spacing": "sm",
         "contents": buttons[i:i + 2]}
        for i in range(0, len(buttons), 2)
    ]
    return {
        "type": "flex",
        "altText": "⏰ ตั้งเวลาแจ้งเตือน GEOai",
        "contents": {
            "type": "bubble",
            "header": {
                "type": "box",
                "layout": "vertical",
                "backgroundColor": "#1a7a3c",
                "paddingAll": "14px",
                "contents": [{
                    "type": "text",
                    "text": "⏰ เลือกเวลาแจ้งเตือน",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md"
                }]
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "paddingAll": "14px",
                "contents": [
                    {
                        "type": "text",
                        "text": "ใช้กับทั้งการแจ้งเตือนเฉพาะตอนเสี่ยง และสรุปแปลงประจำวัน",
                        "wrap": True,
                        "size": "sm",
                        "color": "#666666",
                    },
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "paddingAll": "12px",
                "contents": rows,
            }
        }
    }


def _help_message() -> dict:
    return {
        "type": "text",
        "text": (
            "❓ วิธีใช้งาน GEOai v2\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "1️⃣  กด 'ตรวจสอบแปลงใหม่'\n"
            "2️⃣  แผนที่จะเปิดขึ้นมา\n"
            "3️⃣  จิ้มตำแหน่งแปลงทุเรียนของคุณ หรือกด 'ใช้ตำแหน่งของฉัน'\n"
            "4️⃣  กด 'ตรวจสอบแปลงนี้'\n"
            "5️⃣  รอรับผลวิเคราะห์ใน 1-2 นาที\n\n"
            "🛰️ ระบบใช้ข้อมูลดาวเทียม Sentinel-1/2 ย้อนหลัง 1 ปี\n"
            "วิเคราะห์:\n"
            "  🌿 ความสมบูรณ์พืช (NDVI)\n"
            "  💧 ความชื้นดิน\n"
            "  ⛰️ ระดับพื้นที่\n"
            "  🌍 การเปลี่ยนแปลงพื้นดิน\n"
            "  🧪 คำแนะนำปุ๋ย N-P-K\n"
            "  📊 ประเมินผลผลิต (กก./ไร่)\n\n"
            "📞 ติดต่อทีมงาน: @geoai_support"
        )
    }


# ─────────────────────────────────────────────────────
# LINE Profile API
# ─────────────────────────────────────────────────────

async def _get_profile(user_id: str) -> dict | None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        return None
    async with httpx.AsyncClient(timeout=5) as client:
        resp = await client.get(
            f"https://api.line.me/v2/bot/profile/{user_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    return resp.json() if resp.status_code == 200 else None


# ─────────────────────────────────────────────────────
# LINE Reply API
# ─────────────────────────────────────────────────────

async def _reply(reply_token: str, messages: list):
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        logger.warning("LINE_CHANNEL_ACCESS_TOKEN not set")
        return

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            LINE_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token, "messages": messages},
        )

    if resp.status_code != 200:
        logger.error(f"LINE reply failed: {resp.status_code} — {resp.text}")
