"""
Lấy thông báo đơn hàng Shopee (Shopee Open Platform v2) - CHỈ DÀNH CHO NGƯỜI BÁN.

Shopee không có API cho người mua. Với người mua, hãy lọc email Shopee
từ Gmail/Outlook thay thế.

Chuẩn bị:
1. Đăng ký tại https://open.shopee.com -> tạo app -> lấy partner_id, partner_key.
   Khai báo redirect URL trong app.
2. Copy config.example.json -> config.json và điền thông tin.
3. Cấp quyền shop (1 lần, phải làm lại khi hết hạn uỷ quyền):
       python shopee/main.py auth
4. Lấy dữ liệu:
       python shopee/main.py
"""

import hashlib
import hmac
import json
import sys
import time

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests


HOSTS = {
    "live": "https://partner.shopeemobile.com",
    "test": "https://partner.test-stable.shopeemobile.com"
}

DAYS = 7  # get_order_list cho phép tối đa 15 ngày mỗi lần

ORDER_STATUS_VI = {
    "UNPAID": "Chờ thanh toán",
    "READY_TO_SHIP": "Chờ lấy hàng",
    "PROCESSED": "Đã xử lý",
    "RETRY_SHIP": "Giao lại",
    "SHIPPED": "Đang giao",
    "TO_CONFIRM_RECEIVE": "Chờ xác nhận đã nhận",
    "IN_CANCEL": "Đang huỷ",
    "CANCELLED": "Đã huỷ",
    "TO_RETURN": "Trả hàng",
    "COMPLETED": "Hoàn thành",
    "INVOICE_PENDING": "Chờ hoá đơn"
}

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
TOKEN_FILE = BASE_DIR / "token.json"
OUTPUT_FILE = BASE_DIR / "notifications.json"


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            "Không tìm thấy shopee/config.json "
            "(copy từ config.example.json)"
        )

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    for key in ("partner_id", "partner_key", "redirect_url"):
        if not config.get(key):
            raise ValueError(f"config.json thiếu {key}")

    config["partner_id"] = int(config["partner_id"])
    config["host"] = HOSTS[config.get("env", "live")]

    return config


def sign(partner_key, *parts):
    """HMAC-SHA256 theo chuẩn Shopee: nối các phần rồi ký bằng partner_key."""

    base = "".join(str(p) for p in parts)

    return hmac.new(
        partner_key.encode(),
        base.encode(),
        hashlib.sha256
    ).hexdigest()


def check(data):
    if data.get("error"):
        raise RuntimeError(
            f"Shopee API lỗi: {data.get('error')}: {data.get('message')}"
        )
    return data


# ---------------------------------------------------------------- Auth

def build_auth_url(config):
    path = "/api/v2/shop/auth_partner"
    ts = int(time.time())

    params = {
        "partner_id": config["partner_id"],
        "timestamp": ts,
        "sign": sign(config["partner_key"], config["partner_id"], path, ts),
        "redirect": config["redirect_url"]
    }

    return f"{config['host']}{path}?{urlencode(params)}"


def call_auth(config, path, body):
    ts = int(time.time())

    params = {
        "partner_id": config["partner_id"],
        "timestamp": ts,
        "sign": sign(config["partner_key"], config["partner_id"], path, ts)
    }

    response = requests.post(
        f"{config['host']}{path}",
        params=params,
        json=body,
        timeout=30
    )

    return check(response.json())


def save_token(data, shop_id):
    token = {
        "shop_id": int(shop_id),
        "access_token": data["access_token"],
        # Shopee trả refresh_token MỚI mỗi lần refresh -> phải lưu lại
        "refresh_token": data["refresh_token"],
        "expires_at": int(time.time()) + int(data.get("expire_in", 14400))
    }

    TOKEN_FILE.write_text(json.dumps(token, indent=4), encoding="utf-8")

    return token


def authorize():
    """Cấp quyền shop: mở link, đăng nhập shop, dán lại URL redirect."""

    config = load_config()

    print("1. Mở link sau và đăng nhập tài khoản shop:\n")
    print(build_auth_url(config))
    print("\n2. Sau khi đồng ý, trình duyệt chuyển tới redirect URL.")

    redirect = input("   Dán toàn bộ URL đó vào đây: ").strip()
    query = parse_qs(urlparse(redirect).query)

    code = query.get("code", [None])[0]
    shop_id = query.get("shop_id", [None])[0]

    if not code or not shop_id:
        raise ValueError("URL không có code hoặc shop_id")

    data = call_auth(config, "/api/v2/auth/token/get", {
        "code": code,
        "shop_id": int(shop_id),
        "partner_id": config["partner_id"]
    })

    save_token(data, shop_id)
    print(f"Đã lưu token cho shop {shop_id}.")


def get_access_token(config):
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            "Chưa cấp quyền shop. Chạy: python shopee/main.py auth"
        )

    token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))

    # Còn hạn (chừa 60s) thì dùng luôn
    if token["expires_at"] - 60 > time.time():
        return token

    data = call_auth(config, "/api/v2/auth/access_token/get", {
        "refresh_token": token["refresh_token"],
        "shop_id": token["shop_id"],
        "partner_id": config["partner_id"]
    })

    print("Shopee: Access Token refreshed successfully.")

    return save_token(data, token["shop_id"])


# ---------------------------------------------------------------- Shop API

def call_shop(config, token, path, params):
    ts = int(time.time())

    common = {
        "partner_id": config["partner_id"],
        "timestamp": ts,
        "access_token": token["access_token"],
        "shop_id": token["shop_id"],
        "sign": sign(
            config["partner_key"],
            config["partner_id"], path, ts,
            token["access_token"], token["shop_id"]
        )
    }

    response = requests.get(
        f"{config['host']}{path}",
        params={**common, **params},
        timeout=30
    )

    return check(response.json()).get("response", {})


def get_order_sns(config, token, days):
    now = int(time.time())
    since = now - days * 86400

    order_sns = []
    cursor = ""

    while True:
        data = call_shop(config, token, "/api/v2/order/get_order_list", {
            "time_range_field": "update_time",
            "time_from": since,
            "time_to": now,
            "page_size": 100,
            "cursor": cursor
        })

        order_sns.extend(o["order_sn"] for o in data.get("order_list", []))

        if not data.get("more"):
            break

        cursor = data.get("next_cursor", "")

    return order_sns


def get_order_details(config, token, order_sns):
    details = []

    # Tối đa 50 order_sn mỗi lần gọi
    for i in range(0, len(order_sns), 50):
        data = call_shop(config, token, "/api/v2/order/get_order_detail", {
            "order_sn_list": ",".join(order_sns[i:i + 50]),
            "response_optional_fields": (
                "buyer_username,item_list,total_amount,"
                "pay_time,shipping_carrier"
            )
        })

        details.extend(data.get("order_list", []))

    return details


def to_iso(timestamp):
    if not timestamp:
        return None

    return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).isoformat()


def to_notification(order, shop_id):
    status = order.get("order_status", "")
    status_vi = ORDER_STATUS_VI.get(status, status)

    items = order.get("item_list", [])
    lines = [
        f"- {item.get('item_name', '')} "
        f"x{item.get('model_quantity_purchased', 1)}"
        for item in items
    ]

    body = "\n".join([
        f"Đơn hàng: {order.get('order_sn')}",
        f"Trạng thái: {status_vi}",
        f"Người mua: {order.get('buyer_username', '')}",
        f"Tổng tiền: {order.get('total_amount', '')} {order.get('currency', '')}",
        f"Vận chuyển: {order.get('shipping_carrier', '')}",
        "Sản phẩm:",
        *lines
    ])

    return {
        "id": order.get("order_sn"),
        "threadId": order.get("order_sn"),
        "source": "shopee",

        "from": "Shopee",
        "to": f"shop:{shop_id}",
        "subject": f"Đơn {order.get('order_sn')}: {status_vi}",
        "date": to_iso(order.get("update_time")),

        "receivedAt": to_iso(order.get("update_time")),
        "snippet": f"{status_vi} - {order.get('buyer_username', '')} - "
                   f"{len(items)} sản phẩm",

        "labelIds": ["SHOPEE", "ORDER", status],

        "payload": {
            "createdAt": to_iso(order.get("create_time")),
            "payTime": to_iso(order.get("pay_time")),
            "order": order
        },

        "body": body,
        "bodyType": "text/plain"
    }


def fetch_notifications(days=DAYS):
    config = load_config()
    token = get_access_token(config)

    order_sns = get_order_sns(config, token, days)
    print(f"Shopee: tìm thấy {len(order_sns)} đơn cập nhật trong {days} ngày.")

    orders = get_order_details(config, token, order_sns)

    notifications = [to_notification(o, token["shop_id"]) for o in orders]
    notifications.sort(key=lambda n: n["receivedAt"] or "", reverse=True)

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

    print(f"\nExported {len(notifications)} orders.")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
