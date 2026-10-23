"""
auth.py — ยืนยันตัวตนผู้ใช้ LIFF ฝั่ง server ด้วย token ของ LINE

ก่อนหน้านี้ทุก endpoint เชื่อ user_id ที่ client ส่งมาตรงๆ (ใน URL หรือ body) ใครรู้
LINE userId ของคนอื่นก็ดู/ลบแปลง หรือสั่งให้ระบบ push ข้อความไปหาคนนั้นได้ — ดู
docs/v2/phase0-report.md หัวข้อ "ไม่มีการยืนยันตัวตนผู้ใช้เลย"

ตอนนี้ client ต้องแนบ token ที่ได้จาก LIFF SDK มาทุกคำขอ:
    Authorization: Bearer <token>
    X-Line-Token-Type: id_token | access_token

- id_token (liff.getIDToken()) — ทางหลักตาม spec ตรวจกับ LINE ที่
  POST /oauth2/v2.1/verify ต้องเปิด scope "openid" ในหน้าตั้งค่า LIFF ถึงจะได้ token นี้
- access_token (liff.getAccessToken()) — ทางสำรองสำหรับ LIFF ที่ยังไม่ได้เปิด openid
  หรือ ID token หมดอายุระหว่างเปิดแอปค้างไว้ ตรวจว่าออกให้ channel เราจริง
  (GET /oauth2/v2.1/verify) แล้วดึง userId จาก GET /v2/profile

ผลตรวจถูก cache ในแรม (key = sha256 ของ token ไม่เก็บ token ดิบ) ไม่เกินอายุของ
token และไม่เกิน _CACHE_MAX_TTL_S กันยิง LINE API ทุกคำขอ
"""

import hashlib
import logging
import os
import time

import httpx
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

LINE_VERIFY_URL  = "https://api.line.me/oauth2/v2.1/verify"
LINE_PROFILE_URL = "https://api.line.me/v2/profile"

# ต้องตรงกับค่า fallback ใน webhook.py::_liff_button() — ดูเหตุผลที่ main.py::liff_config
DEFAULT_LIFF_URL = "https://liff.line.me/2010580115-di08GvXS"

_CACHE_MAX_TTL_S  = 600
_CACHE_MAX_SIZE   = 5000
_VERIFY_TIMEOUT_S = 8

_token_cache: dict[str, tuple[str, float]] = {}   # sha256(token) → (user_id, expires_at monotonic)


def liff_id() -> str:
    liff_url = os.environ.get("LIFF_URL") or DEFAULT_LIFF_URL
    return liff_url.rstrip("/").rsplit("/", 1)[-1]


def login_channel_id() -> str:
    """
    Channel ID ของ LINE Login channel ที่ LIFF app นี้สังกัด — ใช้เป็น client_id ตอนตรวจ
    token ตั้งเองได้ด้วย LINE_LOGIN_CHANNEL_ID ถ้าไม่ตั้ง ใช้ส่วนหน้าของ LIFF ID
    (รูปแบบ LIFF ID คือ "<channelId>-<random>")
    """
    explicit = os.environ.get("LINE_LOGIN_CHANNEL_ID", "").strip()
    if explicit:
        return explicit
    lid = liff_id()
    head = lid.split("-", 1)[0]
    return head if "-" in lid and head.isdigit() else ""


def dev_auth_user() -> str | None:
    """
    ผู้ใช้จำลองสำหรับรันในเครื่อง (ไม่มี LIFF จริงให้ขอ token) — เปิดด้วย
    IAMROOT_DEV_AUTH_USER=<id> เท่านั้น และถูกปิดเสมอบน Render (Render ตั้ง env
    RENDER=true ให้ทุก service) กันเผลอตั้งค้างไว้บน production
    """
    uid = os.environ.get("IAMROOT_DEV_AUTH_USER", "").strip()
    if not uid or os.environ.get("RENDER"):
        return None
    return uid


def _cache_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _cache_get(token: str) -> str | None:
    entry = _token_cache.get(_cache_key(token))
    if not entry:
        return None
    user_id, expires_at = entry
    if time.monotonic() >= expires_at:
        _token_cache.pop(_cache_key(token), None)
        return None
    return user_id


def _cache_set(token: str, user_id: str, ttl_s: float) -> None:
    ttl_s = max(0.0, min(ttl_s, _CACHE_MAX_TTL_S))
    if ttl_s <= 0:
        return
    if len(_token_cache) >= _CACHE_MAX_SIZE:
        now = time.monotonic()
        for k in [k for k, (_, exp) in _token_cache.items() if exp <= now]:
            _token_cache.pop(k, None)
        if len(_token_cache) >= _CACHE_MAX_SIZE:
            _token_cache.clear()
    _token_cache[_cache_key(token)] = (user_id, time.monotonic() + ttl_s)


def clear_cache() -> None:
    _token_cache.clear()


async def _verify_id_token(client: httpx.AsyncClient, token: str, channel_id: str) -> tuple[str, float] | None:
    resp = await client.post(LINE_VERIFY_URL, data={"id_token": token, "client_id": channel_id})
    if resp.status_code != 200:
        return None
    body = resp.json()
    user_id = body.get("sub")
    exp = body.get("exp")
    if not user_id or not exp:
        return None
    return user_id, float(exp) - time.time()


async def _verify_access_token(client: httpx.AsyncClient, token: str, channel_id: str) -> tuple[str, float] | None:
    resp = await client.get(LINE_VERIFY_URL, params={"access_token": token})
    if resp.status_code != 200:
        return None
    body = resp.json()
    if str(body.get("client_id")) != channel_id or int(body.get("expires_in", 0)) <= 0:
        return None
    prof = await client.get(LINE_PROFILE_URL, headers={"Authorization": f"Bearer {token}"})
    if prof.status_code != 200:
        return None
    user_id = prof.json().get("userId")
    if not user_id:
        return None
    return user_id, float(body["expires_in"])


async def verify_line_token(token: str, token_type: str) -> str | None:
    """คืน LINE userId ถ้า token ถูกต้องและออกให้ channel ของเรา ไม่งั้นคืน None"""
    cached = _cache_get(token)
    if cached:
        return cached

    channel_id = login_channel_id()
    if not channel_id:
        logger.error("Cannot verify LINE tokens: LINE login channel ID unknown "
                     "(set LINE_LOGIN_CHANNEL_ID or a LIFF_URL with a real LIFF ID)")
        raise HTTPException(status_code=503, detail="ระบบยืนยันตัวตนยังไม่พร้อม กรุณาลองใหม่ภายหลัง")

    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_S) as client:
            if token_type == "access_token":
                result = await _verify_access_token(client, token, channel_id)
            else:
                result = await _verify_id_token(client, token, channel_id)
    except httpx.HTTPError as e:
        logger.warning(f"LINE token verification unavailable ({type(e).__name__})")
        raise HTTPException(status_code=503, detail="ยืนยันตัวตนกับ LINE ไม่สำเร็จชั่วคราว กรุณาลองใหม่")

    if not result:
        return None
    user_id, ttl_s = result
    _cache_set(token, user_id, ttl_s)
    return user_id


async def current_user(request: Request) -> str:
    """
    FastAPI dependency — คืน LINE userId ที่ยืนยันแล้ว หรือ 401
    ผลถูกเก็บใน request.state.user_id ให้ dependency อื่น (เช่น rate limit) ใช้ต่อ
    """
    cached = getattr(request.state, "user_id", None)
    if cached:
        return cached

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        dev_user = dev_auth_user()
        if dev_user:
            request.state.user_id = dev_user
            return dev_user
        raise HTTPException(status_code=401, detail="กรุณาเปิดแอปผ่าน LINE เพื่อยืนยันตัวตน")

    token = auth_header[len("Bearer "):].strip()
    token_type = request.headers.get("X-Line-Token-Type", "id_token").strip().lower()
    if token_type not in ("id_token", "access_token") or not token:
        raise HTTPException(status_code=401, detail="ข้อมูลยืนยันตัวตนไม่ถูกต้อง")

    user_id = await verify_line_token(token, token_type)
    if not user_id:
        raise HTTPException(status_code=401, detail="การยืนยันตัวตนหมดอายุ กรุณาปิดแล้วเปิดแอปใหม่")
    request.state.user_id = user_id
    return user_id
