"""
Lấy email Outlook (Microsoft Graph) trong N ngày gần nhất.

Chuẩn bị:
1. Đăng ký app tại https://entra.microsoft.com -> App registrations
   - Authentication: bật "Allow public client flows"
   - API permissions: Microsoft Graph -> Delegated -> Mail.Read
2. Copy config.example.json -> config.json, điền client_id.
3. Chạy: python outlook/main.py
   Lần đầu sẽ in link + mã đăng nhập (Device Code Flow).
"""

import json
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path

import msal
import requests


SCOPES = ["Mail.Read"]
GRAPH_URL = "https://graph.microsoft.com/v1.0"
DAYS = 7

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
CACHE_FILE = BASE_DIR / "token_cache.json"
OUTPUT_FILE = BASE_DIR / "notifications.json"


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            "Không tìm thấy outlook/config.json "
            "(copy từ config.example.json)"
        )

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    if not config.get("client_id"):
        raise ValueError("config.json thiếu client_id")

    return config


def authenticate():
    """Xác thực bằng MSAL Device Code Flow, cache token để tự refresh."""

    config = load_config()

    cache = msal.SerializableTokenCache()
    if CACHE_FILE.exists():
        cache.deserialize(CACHE_FILE.read_text(encoding="utf-8"))

    app = msal.PublicClientApplication(
        config["client_id"],
        authority=config.get(
            "authority",
            "https://login.microsoftonline.com/consumers"
        ),
        token_cache=cache
    )

    result = None

    # 1. Thử lấy token từ cache (MSAL tự refresh nếu hết hạn)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])

    # 2. Chưa có / refresh token hết hạn -> đăng nhập lại
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)

        if "user_code" not in flow:
            raise ValueError(f"Không tạo được device flow: {flow}")

        print(flow["message"])
        result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise ValueError(
            f"Xác thực Outlook thất bại: "
            f"{result.get('error')}: {result.get('error_description')}"
        )

    if cache.has_state_changed:
        CACHE_FILE.write_text(cache.serialize(), encoding="utf-8")

    return result["access_token"]


def graph_get(url, token, params=None, max_retries=5):
    """GET Graph API, tự chờ khi bị throttling (429/503)."""

    headers = {
        "Authorization": f"Bearer {token}",
        # Yêu cầu body dạng text thay vì HTML
        "Prefer": 'outlook.body-content-type="text"'
    }

    for _ in range(max_retries):
        response = requests.get(url, headers=headers, params=params, timeout=30)

        if response.status_code in (429, 503):
            wait = int(response.headers.get("Retry-After", 5))
            print(f"Bị giới hạn tốc độ, chờ {wait}s...")
            time.sleep(wait)
            continue

        response.raise_for_status()
        return response.json()

    raise RuntimeError("Graph API throttling quá nhiều lần")


def format_address(recipient):
    email = (recipient or {}).get("emailAddress", {})
    name = email.get("name", "")
    address = email.get("address", "")

    return f"{name} <{address}>" if name else address


def to_iso(value):
    if not value:
        return None

    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()


def to_notification(message):
    body = message.get("body", {})

    labels = list(message.get("categories", []))
    if not message.get("isRead", True):
        labels.append("UNREAD")

    return {
        "id": message.get("id"),
        "threadId": message.get("conversationId"),
        "source": "outlook",

        "from": format_address(message.get("from")),
        "to": ", ".join(
            format_address(r) for r in message.get("toRecipients", [])
        ),
        "subject": message.get("subject", ""),
        "date": message.get("receivedDateTime", ""),

        "receivedAt": to_iso(message.get("receivedDateTime")),
        "snippet": message.get("bodyPreview", ""),

        "labelIds": labels,

        "payload": {
            "headers": message.get("internetMessageHeaders", [])
        },

        "body": body.get("content", ""),
        "bodyType": "text/plain"
        if body.get("contentType") == "text" else "text/html"
    }


def fetch_notifications(days=DAYS):
    token = authenticate()

    since = (datetime.now(timezone.utc) - timedelta(days=days)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")

    url = f"{GRAPH_URL}/me/messages"
    params = {
        "$filter": f"receivedDateTime ge {since}",
        "$orderby": "receivedDateTime desc",
        "$top": 100,
        "$select": (
            "id,conversationId,from,toRecipients,subject,"
            "receivedDateTime,bodyPreview,body,categories,isRead,"
            "internetMessageHeaders"
        )
    }

    notifications = []

    while url:
        data = graph_get(url, token, params)

        for message in data.get("value", []):
            notifications.append(to_notification(message))

        print(f"Outlook: đã lấy {len(notifications)} email...")

        # nextLink đã chứa sẵn toàn bộ query
        url = data.get("@odata.nextLink")
        params = None

    return notifications


def main():
    notifications = fetch_notifications()

    OUTPUT_FILE.write_text(
        json.dumps(notifications, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )

    print(f"\nExported {len(notifications)} emails.")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
