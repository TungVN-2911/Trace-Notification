"""
Lấy tin nhắn Telegram của tài khoản cá nhân (MTProto client API, Telethon).

Chuẩn bị:
1. Vào https://my.telegram.org -> API development tools -> tạo app,
   lấy api_id và api_hash.
2. Copy config.example.json -> config.json, điền api_id, api_hash, phone.
3. Chạy: python telegram/main.py
   Lần đầu Telegram gửi mã đăng nhập (và hỏi mật khẩu 2FA nếu có).

CẢNH BÁO: file telegram.session = TOÀN QUYỀN tài khoản Telegram.
Không chia sẻ, không commit. Thu hồi tại Settings -> Devices nếu lộ.
Script chỉ ĐỌC, không đánh dấu đã đọc.
"""

import json

from datetime import datetime, timedelta, timezone
from pathlib import Path

from telethon import utils
from telethon.sync import TelegramClient


DAYS = 7

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
SESSION_FILE = BASE_DIR / "telegram"  # Telethon tự thêm đuôi .session
OUTPUT_FILE = BASE_DIR / "notifications.json"


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            "Không tìm thấy telegram/config.json "
            "(copy từ config.example.json)"
        )

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    if not config.get("api_id") or not config.get("api_hash"):
        raise ValueError("config.json thiếu api_id hoặc api_hash")

    return config


def dialog_type(dialog):
    if dialog.is_user:
        return "USER"
    if dialog.is_group:
        return "GROUP"
    return "CHANNEL"


def message_text(message):
    text = getattr(message, "message", None) or ""

    if not text and getattr(message, "media", None):
        text = f"[{type(message.media).__name__}]"

    if not text and getattr(message, "action", None):
        text = f"[{type(message.action).__name__}]"

    return text


def to_notification(dialog, message):
    kind = dialog_type(dialog)

    sender = message.sender
    sender_name = utils.get_display_name(sender) if sender else dialog.name

    if message.out:
        sender_name = "me"

    labels = ["TELEGRAM", kind]

    if message.out:
        labels.append("OUTGOING")
    elif message.id > getattr(dialog.dialog, "read_inbox_max_id", 0):
        labels.append("UNREAD")

    text = message_text(message)
    received_at = message.date.astimezone(timezone.utc)

    return {
        "id": f"{dialog.id}:{message.id}",
        "threadId": str(dialog.id),
        "source": "telegram",

        "from": sender_name,
        "to": dialog.name if (message.out or kind != "USER") else "me",
        "subject": dialog.name,
        "date": received_at.isoformat(),

        "receivedAt": received_at.isoformat(),
        "snippet": text[:200],

        "labelIds": labels,

        "payload": {
            "chatId": dialog.id,
            "messageId": message.id,
            "senderId": message.sender_id
        },

        "body": text,
        "bodyType": "text/plain"
    }


def fetch_notifications(days=DAYS):
    config = load_config()

    include_groups = config.get("include_groups", True)
    include_channels = config.get("include_channels", True)
    include_outgoing = config.get("include_outgoing", False)

    since = datetime.now(timezone.utc) - timedelta(days=days)

    client = TelegramClient(
        str(SESSION_FILE),
        int(config["api_id"]),
        config["api_hash"]
    )

    # Lần đầu sẽ hỏi mã đăng nhập; lần sau dùng lại session
    if config.get("phone"):
        client.start(phone=config["phone"])
    else:
        client.start()

    notifications = []

    try:
        for dialog in client.iter_dialogs():
            # Hội thoại không có tin mới trong khoảng thời gian -> bỏ qua
            if dialog.date is None or dialog.date < since:
                continue

            kind = dialog_type(dialog)

            if kind == "GROUP" and not include_groups:
                continue
            if kind == "CHANNEL" and not include_channels:
                continue

            count = 0

            # iter_messages trả về tin mới nhất trước
            for message in client.iter_messages(dialog.entity):
                if message.date < since:
                    break

                if message.out and not include_outgoing:
                    continue

                notifications.append(to_notification(dialog, message))
                count += 1

            if count:
                print(f"Telegram: {dialog.name} -> {count} tin")

    finally:
        client.disconnect()

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
