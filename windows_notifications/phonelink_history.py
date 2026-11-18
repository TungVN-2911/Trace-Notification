"""
Lấy các thông báo iPhone ĐANG HIỂN THỊ trong cửa sổ Phone Link
(Phone Link không lưu thông báo iPhone ra ổ đĩa, nên phải đọc từ giao diện
bằng Windows UI Automation).

Cách dùng:
1. Mở Phone Link, để ở màn hình chính có cột "Notifications"
   (không mở Settings, không thu nhỏ cửa sổ).
2. python windows_notifications/phonelink_history.py

Kết quả được gộp vào windows_notifications/notifications.json
(chung file với main.py, không bị trùng khi chạy lại).

Giới hạn: thời gian chỉ chính xác tới phút; Phone Link chỉ giữ thông báo
chưa bị xoá; giao diện Phone Link thay đổi theo phiên bản có thể làm
script cần chỉnh lại.
"""

import json
import os
import re
import shutil
import sqlite3
import tempfile
import time

from datetime import datetime
from pathlib import Path

from pywinauto import Desktop

from main import phone_notification  # windows_notifications/main.py


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = BASE_DIR / "notifications.json"

PHONE_LINK_DATA = (
    Path(os.environ["LOCALAPPDATA"])
    / "Packages/Microsoft.YourPhone_8wekyb3d8bbwe/LocalCache/Indexed"
)

# Tên app hay gặp - bổ sung cho danh sách đọc từ phoneapps.db
DEFAULT_APPS = [
    "Zalo", "Messenger", "Facebook", "Instagram", "Telegram", "Shopee",
    "Lazada", "TikTok", "MoMo", "Gmail", "Outlook", "Messages", "Tin nhắn",
    "Phone", "Điện thoại", "Mail", "YouTube", "Discord", "Teams",
]

# Câu Phone Link tự chèn vào thông báo chat nhóm
BOILERPLATE = [
    "Keep track of your group chat here. Grab your mobile device to reply.",
]

# UIA ScrollAmount
SCROLL_NO_AMOUNT = 2
SCROLL_LARGE_INCREMENT = 3

INVISIBLE = re.compile("[‎‏⁨⁩​]")
TIME_SUFFIX = re.compile(
    r"\s*(\d{1,2})/(\d{1,2})/(\d{4}) at (\d{1,2}):(\d{2})\s*(AM|PM)?\s*$"
)


def load_app_names():
    """Đọc tên app iPhone mà Phone Link đã đồng bộ (phoneapps.db)."""

    names = set(DEFAULT_APPS)

    for db in PHONE_LINK_DATA.glob("*/System/Database/phoneapps.db"):
        tmp = Path(tempfile.mkdtemp(prefix="pl_"))
        try:
            for f in db.parent.glob("phoneapps.db*"):
                shutil.copy2(f, tmp / f.name)

            conn = sqlite3.connect(tmp / "phoneapps.db")
            names.update(
                r[0] for r in conn.execute("SELECT app_name FROM phone_apps")
                if r[0]
            )
            conn.close()
        except sqlite3.Error:
            pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # Tên dài trước để "MB Bank" khớp trước "MB"
    return sorted(names, key=len, reverse=True)


def find_window():
    window = Desktop(backend="uia").window(
        class_name="WinUIDesktopWin32WindowClass",
        title_re="Phone Link|Liên kết điện thoại"
    )

    if not window.exists(timeout=3):
        raise RuntimeError("Không thấy cửa sổ Phone Link. Hãy mở Phone Link.")

    wrapper = window.wrapper_object()

    if wrapper.is_minimized():
        wrapper.restore()
        time.sleep(1)

    return wrapper


def visible_items(window):
    return [
        item for item in window.descendants(control_type="ListItem")
        if item.element_info.class_name == "RepeaterItemUserControl"
    ]


def find_by_id(window, automation_id):
    for element in window.descendants():
        if element.element_info.automation_id == automation_id:
            return element
    return None


def expand_pane(window):
    """
    Khi Phone Link đang mở Settings/Messages, cột Notifications bị thu gọn
    (chỉ hiện icon + "+N") và không cuộn được -> bấm nút mở rộng.
    """

    host = find_by_id(window, "NotificationsListScrollHost")
    if host is not None and host.rectangle().width() >= 150:
        return host

    button = find_by_id(window, "ExpandCollapsePaneButton")
    if button is not None:
        button.invoke()
        time.sleep(1.5)

    return find_by_id(window, "NotificationsListScrollHost")


def add_visible(window, names):
    for item in visible_items(window):
        name = item.element_info.name or ""
        names.setdefault(name, len(names))


def collect_names(window):
    """
    Danh sách bị ảo hoá (chỉ có mục gần vùng hiển thị) -> cuộn từng đoạn
    nhỏ hơn 1 màn hình từ trên xuống dưới, gom tất cả mục gặp được.
    """

    host = expand_pane(window)
    if host is None or not visible_items(window):
        raise RuntimeError(
            "Không thấy danh sách Notifications trong Phone Link. "
            "Mở Phone Link ở màn hình chính rồi chạy lại."
        )

    names = {}
    scroll = host.iface_scroll

    if not scroll.CurrentVerticallyScrollable:
        add_visible(window, names)
        return sorted(names, key=names.get)

    def safe(action):
        # Danh sách đang giãn ra khi nạp mục mới -> UIA có thể báo lỗi tạm
        for _ in range(5):
            try:
                return action()
            except Exception:
                time.sleep(0.5)
        return None

    safe(lambda: scroll.SetScrollPercent(-1, 0.0))
    time.sleep(0.4)

    # Cuộn từng trang (chiều cao nội dung thay đổi liên tục nên không
    # dùng % cố định); dừng khi ở cuối và không còn mục mới
    stale_rounds = 0
    for _ in range(500):
        before = len(names)
        add_visible(window, names)

        at_bottom = (safe(lambda: scroll.CurrentVerticalScrollPercent) or 0) >= 99.5
        stale_rounds = stale_rounds + 1 if len(names) == before else 0

        if at_bottom and stale_rounds >= 2:
            break

        safe(lambda: scroll.Scroll(SCROLL_NO_AMOUNT, SCROLL_LARGE_INCREMENT))
        time.sleep(0.4)

    safe(lambda: scroll.SetScrollPercent(-1, 0.0))  # trả về đầu danh sách

    # Giữ thứ tự xuất hiện (mới -> cũ)
    return sorted(names, key=names.get)


def parse_name(raw, app_names):
    text = INVISIBLE.sub("", raw)
    for phrase in BOILERPLATE:
        text = text.replace(phrase, " ")

    # 1. Thời gian ở cuối
    received = None
    match = TIME_SUFFIX.search(text)
    if match:
        month, day, year, hour, minute, ampm = match.groups()
        hour = int(hour)
        if ampm == "PM" and hour < 12:
            hour += 12
        if ampm == "AM" and hour == 12:
            hour = 0
        received = datetime(int(year), int(month), int(day), hour, int(minute)) \
            .astimezone()  # giờ máy -> có múi giờ
        text = text[:match.start()]

    text = text.strip()

    # 2. Tên app ở đầu
    app = ""
    for name in app_names:
        if text == name or text.startswith(name + " "):
            app = name
            text = text[len(name):].strip()
            break

    # 3. Tiêu đề / nội dung cách nhau bởi 2 dấu cách
    parts = [p.strip() for p in re.split(r"\s{2,}", text) if p.strip()]
    title = parts[0] if parts else ""
    body = " ".join(parts[1:]) if len(parts) > 1 else title

    return app, title, body, received


def to_notification(raw, app_names, scraped_at):
    app, title, body, received = parse_name(raw, app_names)

    if not title and not body:
        return None

    # Cùng hàm với main.py -> trùng id với banner realtime nếu là 1 thông báo
    return phone_notification(
        app, title, body,
        received or scraped_at,
        labels=["PHONELINK_HISTORY"],
        payload={
            "raw": INVISIBLE.sub("", raw),
            "timePrecision": "minute" if received else "unknown",
            "scrapedAt": scraped_at.isoformat(),
        },
    )


def main():
    app_names = load_app_names()
    window = find_window()

    print("Đang đọc danh sách thông báo trong Phone Link...")
    names = collect_names(window)
    print(f"Đọc được {len(names)} mục.")

    scraped_at = datetime.now().astimezone()

    items = json.loads(OUTPUT_FILE.read_text(encoding="utf-8")) \
        if OUTPUT_FILE.exists() else []
    seen = {item["id"] for item in items}

    added = 0
    for raw in names:
        item = to_notification(raw, app_names, scraped_at)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
            added += 1
            print(f"[{item['source']}] {item['from'][:40]}: {item['snippet'][:60]}")

    items.sort(key=lambda n: n["receivedAt"] or "", reverse=True)
    OUTPUT_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )

    print(f"\nThêm {added} thông báo. Tổng {len(items)} -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
