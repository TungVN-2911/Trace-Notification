"""
Chuyển dữ liệu Messenger/Instagram tải từ "Tải thông tin của bạn"
(Download Your Information) sang định dạng notifications.json.

Lấy file export (1 lần, Facebook chuẩn bị mất vài phút - vài giờ):
1. Facebook -> Cài đặt -> Trung tâm tài khoản -> Thông tin và quyền của bạn
   -> Tải thông tin của bạn -> chọn "Tin nhắn"
   -> Định dạng: JSON, khoảng thời gian tuỳ chọn.
2. Tải file .zip về, giải nén.

Lưu ý: đoạn chat mã hoá đầu-cuối (E2EE, mặc định từ cuối 2023) có thể
không nằm trong file này. Khi đó: Messenger -> Cài đặt -> Quyền riêng tư
-> Đoạn chat được mã hoá đầu cuối -> Bộ nhớ tin nhắn -> Tải dữ liệu xuống.
Nếu định dạng file đó khác, gửi 1 file mẫu để chỉnh parser.

Chạy:
    python facebook_export/main.py <thư mục đã giải nén> [--days 30]
"""

import argparse
import json

from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "notifications.json"


def fix_text(value):
    """
    Facebook ghi UTF-8 dưới dạng escape Latin-1 ("Xin chÃ o").
    Encode lại latin-1 rồi decode utf-8 để ra tiếng Việt đúng.
    """

    if not isinstance(value, str):
        return value

    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def attachment_text(message):
    parts = []

    for field, label in (
        ("photos", "Ảnh"), ("videos", "Video"), ("audio_files", "Âm thanh"),
        ("files", "File"), ("gifs", "GIF")
    ):
        if message.get(field):
            parts.append(f"[{label} x{len(message[field])}]")

    if message.get("sticker"):
        parts.append("[Sticker]")

    if message.get("share", {}).get("link"):
        parts.append(f"[Link: {message['share']['link']}]")

    return " ".join(parts)


def to_notification(thread, message, source, me):
    sender = fix_text(message.get("sender_name", ""))
    participants = [fix_text(p.get("name", "")) for p in thread.get("participants", [])]
    title = fix_text(thread.get("title")) or ", ".join(participants)

    text = fix_text(message.get("content", "")) or ""
    extra = attachment_text(message)
    body = f"{text} {extra}".strip() or "[Tin nhắn không có nội dung]"

    received = datetime.fromtimestamp(
        message["timestamp_ms"] / 1000, tz=timezone.utc
    ).isoformat()

    thread_path = thread.get("thread_path", title)
    is_group = len(participants) > 2

    labels = [source.upper(), "GROUP" if is_group else "USER"]
    if me and sender == me:
        labels.append("OUTGOING")

    return {
        "id": f"{thread_path}:{message['timestamp_ms']}:{sender}",
        "threadId": thread_path,
        "source": source,

        "from": sender,
        "to": title if (is_group or sender == me) else (me or "me"),
        "subject": title,
        "date": received,

        "receivedAt": received,
        "snippet": body[:200],

        "labelIds": labels,

        "payload": {
            "participants": participants,
            "reactions": [
                {"actor": fix_text(r.get("actor")), "reaction": fix_text(r.get("reaction"))}
                for r in message.get("reactions", [])
            ]
        },

        "body": body,
        "bodyType": "text/plain"
    }


def guess_me(threads):
    """Người xuất hiện trong mọi hội thoại = chủ tài khoản."""

    if len(threads) < 2:
        return None

    counts = {}
    for thread in threads:
        for name in {fix_text(p.get("name")) for p in thread.get("participants", [])}:
            counts[name] = counts.get(name, 0) + 1

    if not counts:
        return None

    name, count = max(counts.items(), key=lambda kv: kv[1])
    return name if count == len(threads) else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("export_dir", help="thư mục đã giải nén")
    parser.add_argument("--days", type=int, default=None,
                        help="chỉ lấy N ngày gần nhất (mặc định: tất cả)")
    parser.add_argument("--include-outgoing", action="store_true",
                        help="lấy cả tin bạn gửi đi")
    parser.add_argument("--me", default=None,
                        help="tên Facebook của bạn (mặc định: tự đoán)")
    args = parser.parse_args()

    root = Path(args.export_dir)
    files = sorted(root.rglob("message_*.json"))

    if not files:
        raise SystemExit(f"Không tìm thấy message_*.json trong {root}")

    threads = []
    for file in files:
        data = json.loads(file.read_text(encoding="utf-8"))
        source = "instagram" if "instagram" in str(file).lower() else "messenger"
        threads.append((source, data))

    me = args.me or guess_me([t for _, t in threads])
    print(f"Chủ tài khoản: {me or 'không xác định (dùng --me)'}")

    since_ms = None
    if args.days:
        since_ms = (datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000

    notifications = []
    for source, thread in threads:
        for message in thread.get("messages", []):
            if "timestamp_ms" not in message:
                continue
            if since_ms and message["timestamp_ms"] < since_ms:
                continue

            sender = fix_text(message.get("sender_name", ""))
            if me and sender == me and not args.include_outgoing:
                continue

            notifications.append(to_notification(thread, message, source, me))

    notifications.sort(key=lambda n: n["receivedAt"], reverse=True)

    OUTPUT_FILE.write_text(
        json.dumps(notifications, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )

    print(f"Đọc {len(files)} file, {len(threads)} hội thoại.")
    print(f"Exported {len(notifications)} messages -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
