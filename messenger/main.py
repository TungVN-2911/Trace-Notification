"""
Lấy tin nhắn Messenger gửi tới Facebook Page (Meta Graph API - Conversations).

CHỈ DÀNH CHO FANPAGE. Meta không cho đọc hộp thư Messenger cá nhân.

Chuẩn bị:
1. Tạo app tại https://developers.facebook.com, thêm sản phẩm Messenger.
2. Lấy Page Access Token có quyền: pages_messaging, pages_manage_metadata,
   pages_read_engagement (app public cho người khác dùng cần qua App Review).
   Nên dùng token dài hạn (System User token trong Business Settings
   hoặc Page token đổi từ long-lived User token).
3. Copy config.example.json -> config.json và điền page_id, page_access_token.
4. Chạy: python messenger/main.py

Lưu ý: Graph API chỉ trả chi tiết khoảng 20 tin gần nhất mỗi hội thoại.
Muốn đầy đủ / realtime cần dùng Webhook (cần server HTTPS công khai).
"""

import json
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


DAYS = 7

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
OUTPUT_FILE = BASE_DIR / "notifications.json"


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            "Không tìm thấy messenger/config.json "
            "(copy từ config.example.json)"
        )

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    for key in ("page_id", "page_access_token"):
        if not config.get(key):
            raise ValueError(f"config.json thiếu {key}")

    config.setdefault("graph_version", "v24.0")

    return config


def graph_get(url, params=None, max_retries=5):
    for _ in range(max_retries):
        response = requests.get(url, params=params, timeout=30)
        data = response.json()

        error = data.get("error")
        if not error:
            return data

        # 4, 17, 32, 613: lỗi giới hạn tốc độ -> chờ rồi thử lại
        if error.get("code") in (4, 17, 32, 613):
            print("Messenger: bị giới hạn tốc độ, chờ 60s...")
            time.sleep(60)
            continue

        # 190: token hết hạn / bị thu hồi
        raise RuntimeError(
            f"Graph API lỗi {error.get('code')}: {error.get('message')}"
        )

    raise RuntimeError("Graph API bị giới hạn tốc độ quá nhiều lần")


def parse_time(value):
    # Định dạng của Graph: 2026-11-18T03:25:28+0000
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")


def iter_paged(url, params, stop):
    """Duyệt paging.next; dừng khi stop(item) trả True."""

    while url:
        data = graph_get(url, params)

        for item in data.get("data", []):
            if stop(item):
                return
            yield item

        url = data.get("paging", {}).get("next")
        params = None  # next đã chứa sẵn query


def message_text(message):
    text = message.get("message") or ""

    if not text and message.get("sticker"):
        text = "[sticker]"

    attachments = message.get("attachments", {}).get("data", [])
    if attachments:
        names = ", ".join(
            a.get("name") or a.get("mime_type", "file") for a in attachments
        )
        text = f"{text}\n[Đính kèm: {names}]".strip()

    return text


def to_notification(conversation, message, page_id):
    sender = message.get("from", {})
    incoming = str(sender.get("id")) != str(page_id)

    recipients = message.get("to", {}).get("data", [])
    received_at = parse_time(message["created_time"]) \
        .astimezone(timezone.utc).isoformat()

    text = message_text(message)

    labels = ["MESSENGER"]
    if not incoming:
        labels.append("OUTGOING")
    elif conversation.get("unread_count"):
        labels.append("UNREAD")

    other = next(
        (p.get("name") for p in conversation.get("participants", {})
         .get("data", []) if str(p.get("id")) != str(page_id)),
        ""
    )

    return {
        "id": message.get("id"),
        "threadId": conversation.get("id"),
        "source": "messenger",

        "from": sender.get("name", ""),
        "to": ", ".join(r.get("name", "") for r in recipients),
        "subject": f"Messenger - {other}",
        "date": received_at,

        "receivedAt": received_at,
        "snippet": text[:200],

        "labelIds": labels,

        "payload": {
            "senderId": sender.get("id"),
            "conversationLink": conversation.get("link")
        },

        "body": text,
        "bodyType": "text/plain"
    }


def fetch_notifications(days=DAYS):
    config = load_config()

    page_id = config["page_id"]
    token = config["page_access_token"]
    include_outgoing = config.get("include_outgoing", False)
    base = f"https://graph.facebook.com/{config['graph_version']}"

    since = datetime.now(timezone.utc) - timedelta(days=days)

    # Hội thoại sắp xếp theo updated_time giảm dần
    conversations = iter_paged(
        f"{base}/{page_id}/conversations",
        {
            "platform": "messenger",
            "fields": "id,updated_time,unread_count,link,participants",
            "limit": 50,
            "access_token": token
        },
        stop=lambda c: parse_time(c["updated_time"]) < since
    )

    notifications = []

    for conversation in conversations:
        messages = iter_paged(
            f"{base}/{conversation['id']}/messages",
            {
                "fields": "id,created_time,from,to,message,sticker,"
                          "attachments{name,mime_type}",
                "limit": 25,
                "access_token": token
            },
            stop=lambda m: parse_time(m["created_time"]) < since
        )

        count = 0
        for message in messages:
            incoming = str(message.get("from", {}).get("id")) != str(page_id)
            if not incoming and not include_outgoing:
                continue

            notifications.append(
                to_notification(conversation, message, page_id)
            )
            count += 1

        print(f"Messenger: hội thoại {conversation['id']} -> {count} tin")

    notifications.sort(key=lambda n: n["receivedAt"], reverse=True)

    return notifications


def main():
    notifications = fetch_notifications()

    OUTPUT_FILE.write_text(
        json.dumps(notifications, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )

    print(f"\nExported {len(notifications)} messages.")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
