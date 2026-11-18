"""
Lấy tin nhắn người dùng gửi tới Zalo Official Account (Zalo OA API).

CHỈ DÀNH CHO OA (tài khoản doanh nghiệp). Zalo cá nhân không có API chính thức.

Chuẩn bị:
1. Tạo app tại https://developers.zalo.me, liên kết với OA,
   khai báo Callback URL, lấy app_id và secret_key.
2. Copy config.example.json -> config.json và điền thông tin.
3. Cấp quyền OA (1 lần; làm lại nếu refresh token hết hạn ~3 tháng):
       python zalo/main.py auth
4. Lấy dữ liệu:
       python zalo/main.py
"""

import base64
import hashlib
import json
import secrets
import sys
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests


OAUTH_URL = "https://oauth.zaloapp.com/v4/oa"
OPENAPI_URL = "https://openapi.zalo.me/v2.0/oa"

DAYS = 7
PAGE_SIZE = 10      # Zalo giới hạn count tối đa 10
MAX_PAGES = 100     # chặn vòng lặp vô hạn

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
TOKEN_FILE = BASE_DIR / "token.json"
OUTPUT_FILE = BASE_DIR / "notifications.json"


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            "Không tìm thấy zalo/config.json "
            "(copy từ config.example.json)"
        )

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    for key in ("app_id", "secret_key", "redirect_uri"):
        if not config.get(key):
            raise ValueError(f"config.json thiếu {key}")

    return config


# ---------------------------------------------------------------- Auth

def request_token(config, data):
    response = requests.post(
        f"{OAUTH_URL}/access_token",
        headers={"secret_key": config["secret_key"]},
        data={"app_id": config["app_id"], **data},
        timeout=30
    )

    result = response.json()

    if "access_token" not in result:
        raise RuntimeError(f"Zalo OAuth lỗi: {result}")

    token = {
        "access_token": result["access_token"],
        # Refresh token chỉ dùng được 1 lần -> luôn lưu token mới
        "refresh_token": result["refresh_token"],
        "expires_at": int(time.time()) + int(result.get("expires_in", 3600))
    }

    TOKEN_FILE.write_text(json.dumps(token, indent=4), encoding="utf-8")

    return token


def authorize():
    """OAuth v4 + PKCE: mở link, cấp quyền OA, dán lại URL redirect."""

    config = load_config()

    code_verifier = secrets.token_urlsafe(32)[:43]
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")
    state = secrets.token_urlsafe(16)

    params = {
        "app_id": config["app_id"],
        "redirect_uri": config["redirect_uri"],
        "code_challenge": code_challenge,
        "state": state
    }

    print("1. Mở link sau, đăng nhập Zalo quản trị OA và đồng ý cấp quyền:\n")
    print(f"{OAUTH_URL}/permission?{urlencode(params)}")
    print("\n2. Sau khi đồng ý, trình duyệt chuyển tới redirect_uri.")

    redirect = input("   Dán toàn bộ URL đó vào đây: ").strip()
    query = parse_qs(urlparse(redirect).query)

    if query.get("state", [None])[0] != state:
        raise ValueError("state không khớp - có thể URL không đúng phiên")

    code = query.get("code", [None])[0]
    if not code:
        raise ValueError("URL không có code")

    request_token(config, {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier
    })

    print("Đã lưu token Zalo OA.")


def get_access_token(config):
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            "Chưa cấp quyền OA. Chạy: python zalo/main.py auth"
        )

    token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))

    if token["expires_at"] - 60 > time.time():
        return token["access_token"]

    token = request_token(config, {
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"]
    })

    print("Zalo: Access Token refreshed successfully.")

    return token["access_token"]


# ---------------------------------------------------------------- OA API

def oa_get(token, endpoint, data=None):
    params = {"data": json.dumps(data)} if data is not None else None

    response = requests.get(
        f"{OPENAPI_URL}/{endpoint}",
        headers={"access_token": token},
        params=params,
        timeout=30
    )

    result = response.json()

    if result.get("error", 0) != 0:
        raise RuntimeError(
            f"Zalo API lỗi {result.get('error')}: {result.get('message')}"
        )

    return result.get("data")


def paged(token, endpoint, base, since_ms):
    """Duyệt trang (count tối đa 10), dừng khi gặp tin cũ hơn since."""

    for page in range(MAX_PAGES):
        items = oa_get(token, endpoint, {
            **base,
            "offset": page * PAGE_SIZE,
            "count": PAGE_SIZE
        }) or []

        for item in items:
            yield item

        if len(items) < PAGE_SIZE or items[-1].get("time", 0) < since_ms:
            return


def to_notification(message, oa_id, oa_name):
    incoming = str(message.get("from_id")) != str(oa_id)
    received_at = datetime.fromtimestamp(
        message.get("time", 0) / 1000, tz=timezone.utc
    ).isoformat()

    user_id = message.get("from_id") if incoming else message.get("to_id")

    text = message.get("message") or f"[{message.get('type', 'unknown')}]"

    labels = ["ZALO_OA", (message.get("type") or "unknown").upper()]
    if not incoming:
        labels.append("OUTGOING")

    return {
        "id": str(message.get("message_id")),
        "threadId": str(user_id),
        "source": "zalo",

        "from": message.get("from_display_name")
        or (oa_name if not incoming else str(message.get("from_id"))),
        "to": message.get("to_display_name")
        or (oa_name if incoming else str(message.get("to_id"))),
        "subject": f"Zalo OA - {message.get('from_display_name') if incoming else message.get('to_display_name')}",
        "date": received_at,

        "receivedAt": received_at,
        "snippet": text[:200],

        "labelIds": labels,

        "payload": {"message": message},

        "body": text,
        "bodyType": "text/plain"
    }


def fetch_notifications(days=DAYS):
    config = load_config()
    token = get_access_token(config)

    include_outgoing = config.get("include_outgoing", False)
    since_ms = int(
        (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000
    )

    oa = oa_get(token, "getoa") or {}
    oa_id = oa.get("oa_id")
    oa_name = oa.get("name", "OA")

    # 1. Danh sách hội thoại gần đây -> lấy user_id
    user_ids = []
    for message in paged(token, "listrecentchat", {}, since_ms):
        if message.get("time", 0) < since_ms:
            continue

        incoming = str(message.get("from_id")) != str(oa_id)
        user_id = message.get("from_id") if incoming else message.get("to_id")

        if user_id and user_id not in user_ids:
            user_ids.append(user_id)

    print(f"Zalo: {len(user_ids)} hội thoại có tin trong {days} ngày.")

    # 2. Lấy tin nhắn từng hội thoại
    notifications = []
    seen = set()

    for user_id in user_ids:
        for message in paged(
            token, "conversation", {"user_id": int(user_id)}, since_ms
        ):
            if message.get("time", 0) < since_ms:
                continue

            if message.get("message_id") in seen:
                continue
            seen.add(message.get("message_id"))

            incoming = str(message.get("from_id")) != str(oa_id)
            if not incoming and not include_outgoing:
                continue

            notifications.append(to_notification(message, oa_id, oa_name))

    notifications.sort(key=lambda n: n["receivedAt"], reverse=True)

    return notifications


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "auth":
        authorize()
        return

    notifications = fetch_notifications()

    OUTPUT_FILE.write_text(
        json.dumps(notifications, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )

    print(f"\nExported {len(notifications)} messages.")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
