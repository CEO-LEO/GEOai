"""
rich_menu.py — สร้างและลงทะเบียน LINE Rich Menu
Rich Menu คือปุ่มถาวรที่ด้านล่าง chat ของ LINE OA
ทำให้เกษตรกรกดเมนูได้โดยไม่ต้องพิมพ์

วิธีใช้:
  python rich_menu.py          → สร้าง + upload image + set default
  python rich_menu.py --delete → ลบ rich menu ทั้งหมด
"""

import os
import sys
import asyncio
import json
import httpx
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

LINE_API  = "https://api.line.me/v2/bot"
TOKEN     = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
HEADERS   = lambda: {"Authorization": f"Bearer {TOKEN}",
                     "Content-Type": "application/json"}


# ─────────────────────────────────────────────────────
# Rich Menu definition
# ─────────────────────────────────────────────────────

RICH_MENU_BODY = {
    "size": {"width": 2500, "height": 843},
    "selected": True,
    "name": "GEOai Main Menu",
    "chatBarText": "🌿 เมนู GEOai",
    "areas": [
        # ── ซ้าย: ตรวจสอบแปลง ──────────────────────────
        {
            "bounds": {"x": 0, "y": 0, "width": 833, "height": 843},
            "action": {
                "type": "postback",
                "label": "ตรวจสอบแปลง",
                "data": "action=check",
                "displayText": "🔍 ตรวจสอบแปลงของฉัน"
            }
        },
        # ── กลาง: ประวัติ ────────────────────────────────
        {
            "bounds": {"x": 833, "y": 0, "width": 834, "height": 843},
            "action": {
                "type": "postback",
                "label": "ผลล่าสุด",
                "data": "action=history",
                "displayText": "📋 ดูผลวิเคราะห์ล่าสุด"
            }
        },
        # ── ขวา: วิธีใช้ ─────────────────────────────────
        {
            "bounds": {"x": 1667, "y": 0, "width": 833, "height": 843},
            "action": {
                "type": "postback",
                "label": "วิธีใช้",
                "data": "action=help",
                "displayText": "❓ วิธีใช้งาน"
            }
        }
    ]
}


# ─────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────

async def create_rich_menu(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{LINE_API}/richmenu",
        headers=HEADERS(),
        json=RICH_MENU_BODY
    )
    resp.raise_for_status()
    menu_id = resp.json()["richMenuId"]
    print(f"  ✅ Rich menu created: {menu_id}")
    return menu_id


async def upload_image(client: httpx.AsyncClient, menu_id: str, image_path: str):
    """อัพโหลดรูป Rich Menu (PNG 2500x843)"""
    img_bytes = Path(image_path).read_bytes()
    resp = await client.post(
        f"https://api-data.line.me/v2/bot/richmenu/{menu_id}/content",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "image/png"
        },
        content=img_bytes
    )
    resp.raise_for_status()
    print(f"  ✅ Image uploaded to {menu_id}")


async def set_default_rich_menu(client: httpx.AsyncClient, menu_id: str):
    resp = await client.post(
        f"{LINE_API}/user/all/richmenu/{menu_id}",
        headers=HEADERS()
    )
    resp.raise_for_status()
    print(f"  ✅ Set as default rich menu for all users")


async def delete_all_rich_menus(client: httpx.AsyncClient):
    resp = await client.get(f"{LINE_API}/richmenu/list", headers=HEADERS())
    menus = resp.json().get("richmenus", [])
    for m in menus:
        mid = m["richMenuId"]
        await client.delete(f"{LINE_API}/richmenu/{mid}", headers=HEADERS())
        print(f"  🗑️  Deleted: {mid}")
    print(f"  ✅ Deleted {len(menus)} rich menu(s)")


async def save_menu_id(menu_id: str):
    """บันทึก menu_id ลงไฟล์ เพื่อ reference ภายหลัง"""
    Path("../.richmenu_id").write_text(menu_id)
    print(f"  💾 Saved menu ID to .richmenu_id")


# ─────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────

async def main():
    if not TOKEN:
        print("❌ LINE_CHANNEL_ACCESS_TOKEN ไม่ได้ตั้งค่า")
        sys.exit(1)

    async with httpx.AsyncClient(timeout=30) as client:

        if "--delete" in sys.argv:
            print("กำลังลบ Rich Menu ทั้งหมด...")
            await delete_all_rich_menus(client)
            return

        print("กำลังสร้าง Rich Menu...")
        menu_id = await create_rich_menu(client)

        # ถ้ามีไฟล์รูป ให้อัพโหลด
        img_path = "rich_menu_image.png"
        if Path(img_path).exists():
            print(f"กำลัง upload รูป {img_path}...")
            await upload_image(client, menu_id, img_path)
        else:
            print(f"  ⚠️  ไม่พบไฟล์ {img_path} — ข้ามการ upload รูป")
            print(f"      สร้างรูป PNG ขนาด 2500x843 px แล้วรันใหม่")

        print("กำลัง set เป็น default...")
        await set_default_rich_menu(client, menu_id)
        await save_menu_id(menu_id)

        print(f"\n✅ Rich Menu พร้อมใช้งาน!")
        print(f"   Menu ID: {menu_id}")
        print(f"\n💡 วิธีสร้างรูป Rich Menu:")
        print(f"   canva.com → Custom size 2500x843 px")
        print(f"   3 ช่อง: 🔍 ตรวจสอบแปลง | 📋 ผลล่าสุด | ❓ วิธีใช้")


if __name__ == "__main__":
    asyncio.run(main())
