"""
Chạy tất cả connector đã cấu hình và gộp vào notifications.json.

Nền tảng nào chưa có file cấu hình sẽ được bỏ qua.
Nền tảng nào lỗi sẽ được báo lỗi, các nền tảng khác vẫn chạy tiếp.

    python run_all.py                 # tất cả
    python run_all.py gmail telegram  # chỉ một số nền tảng
"""

import importlib.util
import json
import sys
import traceback

from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT_FILE = ROOT / "notifications.json"

# Nền tảng -> file cần có để coi là "đã cấu hình"
PLATFORMS = {
    "gmail": "credentials.json",
    "outlook": "config.json",
    "telegram": "config.json",
    "shopee": "config.json",
    "zalo": "config.json",
    "messenger": "config.json",
}


def load_connector(name):
    path = ROOT / name / "main.py"

    spec = importlib.util.spec_from_file_location(f"{name}_connector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def main():
    selected = sys.argv[1:] or list(PLATFORMS)

    unknown = [n for n in selected if n not in PLATFORMS]
    if unknown:
        raise SystemExit(f"Không biết nền tảng: {', '.join(unknown)}")

    all_notifications = []
    summary = {}

    for name in selected:
        if not (ROOT / name / PLATFORMS[name]).exists():
            summary[name] = "bỏ qua (chưa cấu hình)"
            continue

        print(f"\n===== {name.upper()} =====")

        try:
            items = load_connector(name).fetch_notifications()
        except Exception as error:
            traceback.print_exc()
            summary[name] = f"LỖI: {error}"
            continue

        for item in items:
            item.setdefault("source", name)

        all_notifications.extend(items)
        summary[name] = f"{len(items)} thông báo"

    all_notifications.sort(
        key=lambda n: n.get("receivedAt") or "",
        reverse=True
    )

    OUTPUT_FILE.write_text(
        json.dumps(all_notifications, ensure_ascii=False, indent=4),
        encoding="utf-8"
    )

    print("\n===== TỔNG KẾT =====")
    for name, status in summary.items():
        print(f"{name:10} {status}")

    print(f"\nTổng: {len(all_notifications)} -> {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
