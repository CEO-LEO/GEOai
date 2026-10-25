"""
report_service.py — รายงานแปลงสำหรับแชร์ใน LINE และดาวน์โหลด PDF (Phase 1f)

- ใช้ Pillow ที่มีอยู่แล้ว (ไม่เพิ่ม dependency): วาดหน้า A4 เป็นรูปแล้วบันทึกเป็น PDF
- ไฟล์อัปโหลดเข้า Supabase Storage โฟลเดอร์ตามวัน → ถูกลบอัตโนมัติหลัง 7 วัน (delete_old_plot_images)
  ชื่อไฟล์สุ่ม (uuid) เดาไม่ได้ แบบเดียวกับรูปแผนที่ที่ส่งเข้า LINE
- ข้อความในรายงานและการ์ดแชร์ไม่มีพิกัด/LINE userId — ผู้ใช้เลือกแชร์เอง
- คำแนะนำทุกข้อมีป้ายสถานะ (ร่าง/ตรวจแล้ว) เหมือนในแอป
"""

import io
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.concurrency import run_in_threadpool

import plot_summary
import risk_display

logger = logging.getLogger(__name__)

TH_MONTHS = ["", "ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
PAGE_W, PAGE_H, MARGIN = 1240, 1754, 80          # A4 ที่ 150 dpi
DISCLAIMER_TH = ("ข้อมูลจากภาพถ่ายดาวเทียม (Sentinel-1/2) ใช้ประกอบการตัดสินใจ ควรตรวจดูในแปลงจริงทุกครั้ง · "
                 "คำแนะนำที่มีป้าย \"ร่าง\" ยังไม่ได้ผ่านการตรวจของเจ้าหน้าที่เกษตร")


def thai_date(d: datetime | None = None) -> str:
    d = (d or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=7)))
    return f"{d.day} {TH_MONTHS[d.month]} {d.year + 543}"


# ── จัดบรรทัดภาษาไทย (ไม่มีช่องว่างระหว่างคำ) ─────────────────
_COMBINING = set("ัิีึืฺุู็่้๊๋์ํ๎")


def wrap_text(text: str, measure, width: float) -> list[str]:
    """
    ตัดบรรทัดตามความกว้างพิกเซล — measure(str) -> px · ตัดที่ช่องว่างถ้ามีในบรรทัด ไม่งั้นตัดที่ตัวอักษร
    (ไม่แยกสระบน/ล่าง/วรรณยุกต์ออกจากพยัญชนะ) — ฟังก์ชันล้วน ทดสอบได้
    """
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if measure(cur + ch) <= width or not cur or ch in _COMBINING:
                cur += ch
                continue
            cut = cur.rfind(" ")
            if cut > len(cur) * 0.5:
                lines.append(cur[:cut].rstrip())
                cur = cur[cut + 1:] + ch
            else:
                lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


# ฟอนต์ Kanit ไม่มี glyph อีโมจิ (ขึ้นเป็นกล่อง) — ตัดออกจากข้อความในไฟล์ PDF (การ์ด LINE/แอปยังมีอีโมจิ)
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u2B50\u2B55]")


def strip_emoji(s: str) -> str:
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub("", s)).strip()


def render_report_pdf(plot_name: str, date_th: str, crops_line: str, map_png: bytes | None,
                      summary_lines: list[str], recs: list[dict], status_label: str | None) -> bytes:
    """รายงาน A4 (หลายหน้าได้) — คืน bytes ของไฟล์ PDF"""
    from PIL import Image, ImageDraw
    from map_image import _font

    f_title, f_h2 = _font("Kanit-Bold.ttf", 44), _font("Kanit-Bold.ttf", 30)
    f_body, f_small = _font("Kanit-Regular.ttf", 26), _font("Kanit-Regular.ttf", 21)
    pages: list = []
    state = {"img": None, "draw": None, "y": 0}

    def new_page():
        img = Image.new("RGB", (PAGE_W, PAGE_H), (255, 255, 255))
        pages.append(img)
        state.update(img=img, draw=ImageDraw.Draw(img), y=MARGIN)

    def ensure(h: int):
        if state["y"] + h > PAGE_H - MARGIN - 60:
            new_page()

    def text(s: str, font, color=(33, 33, 33), indent: int = 0, gap: int = 8):
        s = strip_emoji(s)
        width = PAGE_W - 2 * MARGIN - indent
        for line in wrap_text(s, lambda t: state["draw"].textlength(t, font=font), width):
            h = int(font.size * 1.45)
            ensure(h)
            state["draw"].text((MARGIN + indent, state["y"]), line, font=font, fill=color)
            state["y"] += h
        state["y"] += gap

    new_page()
    text(f"รายงานแปลง: {plot_name}", f_title, (20, 83, 45), gap=4)
    text(f"วันที่ {date_th}" + (f" · {crops_line}" if crops_line else ""), f_small, (90, 100, 95), gap=18)

    if map_png:
        m = Image.open(io.BytesIO(map_png)).convert("RGB")
        max_w, max_h = PAGE_W - 2 * MARGIN, 680
        scale = min(max_w / m.width, max_h / m.height)
        m = m.resize((int(m.width * scale), int(m.height * scale)))
        ensure(m.height + 20)
        state["img"].paste(m, (MARGIN + (max_w - m.width) // 2, state["y"]))
        state["y"] += m.height + 24
    else:
        text("แปลงนี้ปักไว้จุดเดียว — วาดขอบเขตแปลงในแอปเพื่อดูแผนที่ความชื้นรายจุด", f_body, (90, 100, 95))

    text("สรุปแปลงนี้", f_h2, (20, 83, 45), gap=4)
    if status_label:
        text(f"ความชื้นตอนนี้เทียบกับช่วงเดียวกันของปีก่อน ๆ: {status_label}", f_body)
    for i, ln in enumerate(summary_lines):
        text(ln if i == 0 else f"• {strip_emoji(ln)}", f_body, indent=0 if i == 0 else 10, gap=2)
    text(plot_summary.COLOR_NOTE_TH, f_small, (84, 110, 122), gap=18)

    text("สิ่งที่ควรทำ", f_h2, (20, 83, 45), gap=4)
    if not recs:
        text("ตอนนี้ยังไม่พบเรื่องที่ต้องทำเป็นพิเศษทั้งแปลง — ดูคำแนะนำรายจุดได้โดยแตะจุดสีบนแผนที่ในแอป", f_body)
    for r in recs:
        text(r["title"], _font("Kanit-SemiBold.ttf", 27), gap=2)
        for a in r["actions"]:
            text(f"• {a}", f_body, indent=24, gap=0)
        text(f"[{r['status_th']}]", f_small, (141, 75, 0) if r["status"] != "reviewed" else (27, 94, 32), indent=24)

    if risk_display.interim():
        text(risk_display.INTERIM_NOTE, f_small, (84, 110, 122))
    text(DISCLAIMER_TH, f_small, (120, 120, 120), gap=4)

    for i, page in enumerate(pages, 1):
        d = ImageDraw.Draw(page)
        d.text((MARGIN, PAGE_H - MARGIN), f"IAM ROOT · หน้า {i}/{len(pages)}", font=f_small, fill=(150, 150, 150))
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=150.0)
    return buf.getvalue()


def build_share_flex(plot_name: str, image_url: str | None, summary_lines: list[str], liff_url: str) -> dict:
    """การ์ดสำหรับ liff.shareTargetPicker — ไม่มีพิกัด/ชื่อผู้ใช้"""
    body = [
        {"type": "text", "text": f"🌿 {plot_name}", "weight": "bold", "size": "md", "wrap": True},
        {"type": "text", "text": f"รายงานจาก IAM ROOT · {thai_date()}", "size": "xxs", "color": "#78909C"},
        *[{"type": "text", "text": ln, "size": "xs", "wrap": True, "color": "#333333",
           "margin": "sm" if i == 0 else "none"} for i, ln in enumerate(summary_lines[:4])],
        {"type": "text", "text": plot_summary.COLOR_NOTE_TH, "size": "xxs", "wrap": True,
         "color": "#90A4AE", "margin": "md"},
    ]
    bubble = {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "spacing": "xs", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "contents": [
            {"type": "button", "style": "primary", "color": "#15803d", "height": "sm",
             "action": {"type": "uri", "label": "เปิด IAM ROOT", "uri": liff_url}}]},
    }
    if image_url:
        bubble["hero"] = {"type": "image", "url": image_url, "size": "full", "aspectRatio": "4:3",
                          "aspectMode": "fit", "backgroundColor": "#1E2633"}
    return {"type": "flex", "altText": f"รายงานแปลง {plot_name} จาก IAM ROOT"[:400], "contents": bubble}


async def download(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
        return resp.content if resp.status_code == 200 else None
    except Exception as e:
        logger.warning(f"report: download map image failed ({type(e).__name__})")
        return None


async def build_plot_report(plot: dict, crops_line: str, recs: dict, liff_url: str) -> dict:
    """สร้างรูปแผนที่ (ถ้ามีขอบเขต) + PDF + การ์ดแชร์ — ส่วนใดพังก็ยังคืนส่วนที่ทำได้"""
    from plot_image_service import build_plot_grid_image_url, upload_report_file

    name = plot.get("name") or "แปลง"
    polygon = plot.get("polygon")
    image_url, summary_lines, map_png = None, [], None
    if polygon and len(polygon) >= 3:
        img = await build_plot_grid_image_url(polygon, name, plot_id=plot.get("id"), plot_type=plot.get("plot_type"))
        image_url = img.get("url")
        summary_lines = [ln for ln in (img.get("summary_th") or "").split("\n") if ln.strip()]
        if image_url:
            map_png = await download(image_url)
    pdf_url = None
    try:
        pdf = await run_in_threadpool(render_report_pdf, name, thai_date(), crops_line, map_png,
                                      summary_lines, recs.get("plot") or [], recs.get("status_label_th"))
        pdf_url = await upload_report_file(pdf, "pdf", "application/pdf")
    except Exception as e:
        logger.error(f"report PDF failed ({type(e).__name__}): {e}")
    return {"pdf_url": pdf_url, "image_url": image_url, "summary_lines": summary_lines,
            "share": build_share_flex(name, image_url, summary_lines, liff_url)}
