"""
Thu thập thông báo hiện trên Windows (Action Center) - bao gồm thông báo
iPhone được chuyển sang qua ứng dụng "Liên kết điện thoại" (Phone Link).

Nguồn có thể lấy:
- iPhone (qua Phone Link): Zalo, Messenger, Shopee, ngân hàng, SMS, ...
- App trên PC: Zalo PC, Telegram Desktop, Messenger, Discord, ...
- Thông báo web từ Chrome/Edge (messenger.com, chat.zalo.me, ...)

Chuẩn bị iPhone + Phone Link (1 lần):
1. Mở "Liên kết điện thoại" trên Windows -> chọn iPhone -> quét QR, ghép Bluetooth.
2. iPhone: Cài đặt -> Bluetooth -> (i) cạnh tên PC -> bật "Chia sẻ thông báo hệ thống".
3. iPhone: Cài đặt -> Thông báo -> Hiển thị bản xem trước -> "Luôn luôn"
   (nếu không, nội dung chỉ là "Thông báo").
4. Windows: Cài đặt -> Hệ thống -> Thông báo -> bật cho "Liên kết điện thoại",
   tắt "Không làm phiền" khi đang thu thập.

Chạy:
    python windows_notifications/main.py          # theo dõi liên tục (Ctrl+C dừng)
    python windows_notifications/main.py --once   # lấy những gì đang có
    python windows_notifications/main.py --raw    # in cấu trúc XML để debug

Giới hạn: Windows xoá thông báo khỏi DB khi bạn bấm tắt/xoá hoặc hết hạn,
nên cần để script chạy liên tục. Script chỉ ĐỌC bản sao của DB.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import xml.etree.ElementTree as ET

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "notifications.json"

DB_DIR = Path(os.environ["LOCALAPPDATA"]) / "Microsoft/Windows/Notifications"
DB_FILE = DB_DIR / "wpndatabase.db"

FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)

# Nhận diện nguồn theo chuỗi con (không phân biệt hoa thường).
# Kiểm tra trên: tên app (handler), dòng attribution, header, nội dung.
SOURCE_HINTS = [
    ("zalo", "zalo"),
    ("messenger", "messenger"),
    ("facebook", "facebook"),
    ("instagram", "instagram"),
    ("telegram", "telegram"),
    ("shopee", "shopee"),
    ("lazada", "lazada"),
    ("tiktok", "tiktok"),
    ("momo", "momo"),
    ("gmail", "gmail"),
    ("outlook", "outlook"),
    ("discord", "discord"),
    ("teams", "teams"),
]

PHONE_LINK_HINTS = ("yourphone", "phonelink", "crossdevice")
BROWSER_HINTS = ("chrome", "msedge", "edge", "firefox", "brave", "coccoc")

# Thông báo hệ thống - bỏ qua
IGNORED_PREFIXES = (
    "windows.",
    "microsoft.windows.",
    "microsoft.windowsstore",
)


PHONE_TOAST_MARK = "yourphonenotifications_"
INVISIBLE = re.compile("[‎‏⁨⁩​]")


def clean(text):
    return INVISIBLE.sub("", text or "").strip()


def phone_notification(app, title, body, received, labels, payload, bundle_id=None):
    """
    Thông báo iPhone qua Phone Link. Dùng chung cho main.py (banner realtime)
    và phonelink_history.py (đọc danh sách) -> cùng thông báo ra cùng id.
    """

    app, title, body = clean(app), clean(title), clean(body)
    received_iso = received.astimezone().isoformat()  # giờ máy
    minute = received_iso[:16]

    digest = hashlib.sha1(
        f"phonelink|{app}|{title}|{body}|{minute}".encode()
    ).hexdigest()[:16]

    return {
        "id": digest,
        "threadId": f"phonelink|{app}|{title}",
        "source": app.lower().replace(" ", "_") or "iphone",

        "from": title,
        "to": "me",
        "subject": title,
        "date": received_iso,

        "receivedAt": received_iso,
        "snippet": body[:200],

        "labelIds": ["WINDOWS", "IPHONE", *labels],

        "payload": {"via": "iphone", "app": app, "bundleId": bundle_id, **payload},

        "body": body,
        "bodyType": "text/plain"
    }


def filetime_to_datetime(value):
    return FILETIME_EPOCH + timedelta(microseconds=int(value) / 10)


def filetime_to_iso(value):
    if not value:
        return None
    return (FILETIME_EPOCH + timedelta(microseconds=int(value) / 10)).isoformat()


def snapshot_db():
    """Copy DB + WAL sang thư mục tạm để đọc an toàn (Windows đang giữ file)."""

    if not DB_FILE.exists():
        raise FileNotFoundError(f"Không thấy {DB_FILE}")

    tmp = Path(tempfile.mkdtemp(prefix="wpn_"))
    for suffix in ("", "-wal", "-shm"):
        src = DB_DIR / f"wpndatabase.db{suffix}"
        if src.exists():
            shutil.copy2(src, tmp / src.name)

    return tmp


def read_toasts():
    tmp = snapshot_db()

    try:
        conn = sqlite3.connect(tmp / "wpndatabase.db")
        rows = conn.execute("""
            SELECT n.Id, h.PrimaryId, n.Payload, n.ArrivalTime, n.Tag, n.[Group]
            FROM Notification n
            JOIN NotificationHandler h ON h.RecordId = n.HandlerId
            WHERE n.Type = 'toast'
        """).fetchall()
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return rows


def parse_payload(payload):
    text = payload.decode("utf-8", errors="replace") \
        if isinstance(payload, bytes) else payload

    root = ET.fromstring(text)

    texts = []
    attribution = None

    for element in root.iter("text"):
        value = "".join(element.itertext()).strip()
        if not value:
            continue

        if element.get("placement") == "attribution":
            attribution = value
        else:
            texts.append(value)

    header = root.find("header")
    header_title = header.get("title") if header is not None else None

    # Phone Link ghi tên app iPhone trong tham số của nút "Mute ..."
    app_name = None
    for action in root.iter("action"):
        match = re.search(r"ToastNotificationAppName=([^&]+)", action.get("arguments", ""))
        if match:
            app_name = unquote(match.group(1))
            break

    return {
        "texts": texts,
        "attribution": attribution,
        "header": header_title,
        "launch": root.get("launch"),
        "appName": app_name,
    }


def detect_source(handler, parsed):
    handler_l = handler.lower()

    via = "pc"
    if any(h in handler_l for h in PHONE_LINK_HINTS):
        via = "iphone"
    elif any(h in handler_l for h in BROWSER_HINTS):
        via = "browser"

    haystack = " ".join(filter(None, [
        handler, parsed["attribution"], parsed["header"], parsed["launch"],
        # Phone Link thường để tên app iPhone ở dòng đầu
        parsed["texts"][0] if via == "iphone" and parsed["texts"] else None,
    ])).lower()

    for hint, source in SOURCE_HINTS:
        if hint in haystack:
            return source, via

    if via == "iphone":
        return "iphone", via

    # Không nhận ra -> dùng tên app gọn (bỏ phần sau '_' / '!')
    return handler.split("!")[0].split("_")[0], via


def to_notification(row):
    notif_id, handler, payload, arrival, tag, group = row

    if handler.lower().startswith(IGNORED_PREFIXES):
        return None

    try:
        parsed = parse_payload(payload)
    except ET.ParseError:
        return None

    texts = parsed["texts"]
    if not texts:
        return None

    # Thông báo iPhone qua Phone Link: text1 = app, text2 = tiêu đề, text3.. = nội dung
    if PHONE_TOAST_MARK in handler.lower():
        bundle_id = handler[handler.lower().index(PHONE_TOAST_MARK) + len(PHONE_TOAST_MARK):]
        app = parsed["appName"] or texts[0]
        title = texts[1] if len(texts) > 1 else ""
        body = " ".join(texts[2:]) or title

        return phone_notification(
            app, title, body,
            filetime_to_datetime(arrival),
            labels=["PHONELINK_TOAST"],
            payload={"handler": handler, "texts": texts},
            bundle_id=bundle_id,
        )

    source, via = detect_source(handler, parsed)

    title = texts[0]
    body = "\n".join(texts[1:]) or texts[0]
    received = filetime_to_iso(arrival)

    # id ổn định theo nội dung -> chạy lại không bị trùng
    digest = hashlib.sha1(
        f"{handler}|{arrival}|{'|'.join(texts)}".encode()
    ).hexdigest()[:16]

    return {
        "id": digest,
        "threadId": f"{handler}|{group or ''}|{tag or ''}",
        "source": source,

        "from": title,
        "to": "me",
        "subject": parsed["header"] or title,
        "date": received,

        "receivedAt": received,
        "snippet": body[:200],

        "labelIds": ["WINDOWS", via.upper()],

        "payload": {
            "handler": handler,
            "via": via,
            "texts": texts,
            "attribution": parsed["attribution"],
            "tag": tag,
            "group": group,
        },

        "body": body,
        "bodyType": "text/plain"
    }


def load_existing():
    if OUTPUT_FILE.exists():
        return json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    return []


def save(items):
    items.sort(key=lambda n: n["receivedAt"] or "", reverse=True)
    OUTPUT_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )


def collect_once(items, seen):
    new = []

    for row in read_toasts():
        item = to_notification(row)

        if item and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
            new.append(item)

    for item in sorted(new, key=lambda n: n["receivedAt"] or ""):
        via = item["payload"].get("via", "pc")
        print(f"[{item['source']}/{via}] {item['from']}: {item['snippet'][:80]}")

    if new:
        save(items)

    return new


def print_raw():
    """In cấu trúc XML (đã che nội dung) để chỉnh parser cho Phone Link."""

    for _, handler, payload, arrival, _, _ in read_toasts():
        text = payload.decode("utf-8", errors="replace") \
            if isinstance(payload, bytes) else payload
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue

        for element in root.iter():
            if element.text and element.text.strip():
                element.text = f"<{len(element.text.strip())} ký tự>"

        print(f"\n{handler}  {filetime_to_iso(arrival)}")
        print(ET.tostring(root, encoding="unicode"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="lấy thông báo hiện có rồi thoát")
    parser.add_argument("--interval", type=float, default=5,
                        help="số giây giữa 2 lần quét (mặc định 5)")
    parser.add_argument("--raw", action="store_true",
                        help="in cấu trúc XML (đã che nội dung) để debug")
    args = parser.parse_args()

    if args.raw:
        print_raw()
        return

    items = load_existing()
    seen = {item["id"] for item in items}

    print(f"Đã có {len(items)} thông báo trong file.")

    if args.once:
        new = collect_once(items, seen)
        print(f"\nThêm {len(new)} thông báo -> {OUTPUT_FILE}")
        return

    print("Đang theo dõi thông báo Windows... (Ctrl+C để dừng)\n")
    total = 0

    try:
        while True:
            total += len(collect_once(items, seen))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass

    print(f"\nĐã thêm {total} thông báo. Tổng {len(items)} -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
