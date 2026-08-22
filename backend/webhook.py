"""
LINE Webhook Handler
รับ event จาก LINE platform แล้วจัดการ conversation flow:
  - ผู้ใช้ทัก → ส่งเมนูหลัก
  - ผู้ใช้กด "ตรวจสอบแปลง" → ส่งลิงก์ LIFF แผนที่
  - ผู้ใช้กด "ประวัติการตรวจ" → ดึงผลล่าสุดจาก DB
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import base64

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from database import (get_latest_report_by_plot, get_user_plots,
                      upsert_user, set_notify, set_notify_digest,
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
        # v2 (ตามที่ผู้ใช้ขอ "ดูแปลงและดูการ์ดในปุ่มเดียว"): เดิมโชว์แค่ผลวิเคราะห์
        # ล่าสุดสุดของ user (ไม่สนว่าแปลงไหน) — ถ้ามีหลายแปลง ปุ่มนี้เห็นได้แค่แปลง
        # เดียว ต้องเปิด LIFF แยกไปดูแปลงอื่น ตอนนี้ดึงทุกแปลงของ user แล้วส่งเป็น
        # การ์ดเต็มรูปแบบ (เหมือนเดิมทุกอย่าง) ต่อแปลง รวมในข้อความเดียว — 1 แปลง
        # ส่งการ์ดเดี่ยวเหมือนเดิม, หลายแปลงส่งเป็น carousel (ปัดดูทีละใบ)
        try:
            plots = await get_user_plots(user_id)
        except Exception:
            logger.exception("get_user_plots failed")
            await _reply(reply_token, [{"type": "text", "text": _DB_DOWN}])
            return

        if not plots:
            await _reply(reply_token, [{"type": "text",
                "text": "ยังไม่มีแปลงที่บันทึกไว้ กรุณากด 'ตรวจสอบแปลงใหม่' ก่อนครับ 🌿"}])
            return

        from rule_engine import format_message
        bubbles = []
        for plot in plots:
            try:
                report = await get_latest_report_by_plot(plot["id"])
            except Exception:
                logger.exception(f"get_latest_report_by_plot failed for plot {plot.get('id')}")
                continue
            if not report:
                continue
            plain = format_message(report, report["lat"], report["lng"])
            flex = build_result_flex(report, report["lat"], report["lng"], plain,
                                     plot_name=plot.get("name") or "")
            bubbles.append(flex["contents"])   # เอาแค่ bubble ออกมา ไม่เอา {"type":"flex","altText":...} ทั้งก้อน
            if len(bubbles) >= 10:   # LINE carousel รับสูงสุด 10 bubble/ข้อความ
                break

        if not bubbles:
            await _reply(reply_token, [{"type": "text",
                "text": "มีแปลงที่บันทึกไว้แล้ว แต่ยังไม่มีผลวิเคราะห์ กรุณากด 'ตรวจสอบแปลงใหม่' ก่อนครับ 🌿"}])
        elif len(bubbles) == 1:
            await _reply(reply_token, [{
                "type": "flex", "altText": "📋 ผลวิเคราะห์แปลงของคุณ", "contents": bubbles[0],
            }])
        else:
            await _reply(reply_token, [{
                "type": "flex",
                "altText": f"📋 ผลวิเคราะห์ {len(bubbles)} แปลงของคุณ — ปัดดูทีละแปลง",
                "contents": {"type": "carousel", "contents": bubbles},
            }])

        # ── รูปแผนที่ความชื้นตามมาทีละแปลง (ที่วาดขอบเขตไว้เท่านั้น) ─────────────
        # ผู้ใช้ขอเพิ่ม "อย่าลืมรูป map ด้วย" — ส่งเป็น push ตามหลัง ไม่ใช่มัดรวมใน
        # reply() เดียวกัน เพราะ reply token ใช้ได้ครั้งเดียว+มีเวลาจำกัด ส่วนสร้างรูป
        # ต้องรอ GEE 2 ครั้ง/แปลง (grid + ภาพถ่ายดาวเทียม) หลายแปลงรวมกันอาจไม่ทันเวลา
        # reply — push ไม่มีข้อจำกัดนี้ สร้างรูปทุกแปลงพร้อมกัน (asyncio.gather) ลด
        # เวลารอ แล้วค่อยส่งเรียงตามลำดับแปลงให้อ่านง่าย
        plots_with_polygon = [p for p in plots if p.get("polygon") and len(p["polygon"]) >= 3]
        if plots_with_polygon:
            from plot_image_service import build_plot_grid_image_url
            from line_sender import send_line_message

            img_results = await asyncio.gather(*[
                build_plot_grid_image_url(p["polygon"], p.get("name") or "แปลงของคุณ",
                                          plot_id=p.get("id"))
                for p in plots_with_polygon
            ], return_exceptions=True)

            for plot, result in zip(plots_with_polygon, img_results):
                if isinstance(result, Exception) or not result.get("url"):
                    if isinstance(result, Exception):
                        logger.warning(f"map image failed for plot {plot.get('id')}: {result}")
                    continue
                plot_name = plot.get("name") or "แปลงของคุณ"
                caption = f"🗺️ แผนที่ความชื้น — {plot_name}"
                # การ์ดสรุป (ส่งไปก่อนหน้าแล้วใน reply) ใช้ข้อมูลแคชล่าสุด ยังไม่มี
                # จุดที่พบปัญหาเพราะต้องรอตารางจุดที่เพิ่งคำนวณเสร็จตรงนี้ — แนบสรุป
                # สั้นๆ ต่อท้ายรูปแทน ดีกว่าไม่มีเลย (ผู้ใช้ขอ "บอกจุดที่เกิดปัญหา")
                problem_points = result.get("problem_points") or []
                if problem_points:
                    lines = "\n".join(
                        f"• {p['grid_label']} {p['label_th']} — {p['position_desc']}"
                        for p in problem_points
                    )
                    caption += f"\n📍 จุดที่พบปัญหา:\n{lines}"
                await send_line_message(user_id, caption, image_url=result["url"])

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
                            "label": "📋 แปลงของฉัน",
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
