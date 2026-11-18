
import base64
import json
import sys

from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path

from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly"
]

BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
PLAYGROUND_TOKEN_FILE = BASE_DIR / "playground_token.json"
TOKEN_FILE = BASE_DIR / "token.json"
OUTPUT_FILE = BASE_DIR / "notifications.json"



def authenticate():
    """Xác thực Gmail bằng Refresh Token lấy từ OAuth Playground."""

    # 1. Đọc OAuth Client ID và Client Secret
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError("Không tìm thấy credentials.json")

    client_config = json.loads(
        CREDENTIALS_FILE.read_text(encoding="utf-8")
    )

    # Hỗ trợ cấu trúc Web application
    client_config = client_config.get("web", client_config)

    client_id = client_config["client_id"]
    client_secret = client_config["client_secret"]

    creds = None

    # 2. Nếu đã có token.json thì sử dụng lại,
    #    trừ khi nó thuộc client khác hoặc cũ hơn token Playground
    if TOKEN_FILE.exists():
        saved = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))

        stale = saved.get("client_id") != client_id or (
            PLAYGROUND_TOKEN_FILE.exists()
            and PLAYGROUND_TOKEN_FILE.stat().st_mtime
            > TOKEN_FILE.stat().st_mtime
        )

        if stale:
            print("token.json đã cũ hoặc khác client -> tạo lại từ Playground.")
        else:
            creds = Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                SCOPES
            )

    # 3. Nếu chưa có token.json hợp lệ thì khởi tạo từ Playground
    if creds is None:
        if not PLAYGROUND_TOKEN_FILE.exists():
            raise FileNotFoundError(
                "Không tìm thấy playground_token.json"
            )

        token_data = json.loads(
            PLAYGROUND_TOKEN_FILE.read_text(encoding="utf-8")
        )

        if not token_data.get("access_token"):
            raise ValueError("Playground không trả về Access Token")

        if not token_data.get("refresh_token"):
            raise ValueError(
                "Không tìm thấy Refresh Token. "
                "Hãy cấp quyền Offline trong OAuth Playground."
            )

        # google-auth yêu cầu expiry là UTC naive (không tzinfo)
        expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(
            seconds=token_data.get("expires_in", 3600)
        )

        creds = Credentials(
            token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=SCOPES,
            expiry=expiry
        )

        # Lưu credentials lần đầu
        TOKEN_FILE.write_text(
            creds.to_json(),
            encoding="utf-8"
        )

    # 4. Refresh Access Token nếu hết hạn
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

        TOKEN_FILE.write_text(
            creds.to_json(),
            encoding="utf-8"
        )

        print("Access Token refreshed successfully.")

    elif not creds.valid:
        raise ValueError(
            "Credentials không hợp lệ. "
            "Vui lòng xác thực lại bằng OAuth Playground."
        )

    return creds


def get_header(headers, name):
    """Lấy giá trị header theo tên."""

    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")

    return ""


def decode_body(data, content_type):
    """Decode nội dung Base64URL của Gmail."""

    if not data:
        return ""

    # Chuyển Base64URL về bytes
    raw_data = base64.urlsafe_b64decode(
        data + "=" * (-len(data) % 4)
    )

    # Xác định charset từ Content-Type
    content_type_header = Message()
    content_type_header["Content-Type"] = content_type

    charset = content_type_header.get_content_charset() or "utf-8"

    return raw_data.decode(charset, errors="replace")


def extract_bodies(payload):
    """
    Duyệt MIME parts đệ quy.
    Ưu tiên text/plain, nếu không có thì dùng text/html.
    """

    plain_bodies = []
    html_bodies = []

    def walk(part):
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")

        headers = part.get("headers", [])
        content_type = get_header(headers, "Content-Type")

        if not content_type:
            content_type = mime_type or "text/plain"

        # Bỏ qua attachment
        filename = part.get("filename", "")
        if filename:
            return

        if body_data:
            content = decode_body(body_data, content_type)

            if mime_type == "text/plain":
                plain_bodies.append(content)

            elif mime_type == "text/html":
                html_bodies.append(content)

        # Duyệt các MIME parts con
        for child in part.get("parts", []):
            walk(child)

    walk(payload)

    # Ưu tiên nội dung text/plain
    if plain_bodies:
        return "\n".join(plain_bodies), "text/plain"

    # Fallback sang HTML
    if html_bodies:
        return "\n".join(html_bodies), "text/html"

    return "", ""



def get_message_detail(service, message_id):
    """Lấy thông tin chi tiết một email."""

    message = service.users().messages().get(
        userId="me",
        id=message_id,
        format="full"
    ).execute()

    payload = message.get("payload", {})
    headers = payload.get("headers", [])

    # Lấy nội dung email
    body, body_type = extract_bodies(payload)

    # Chuyển timestamp milliseconds thành ISO datetime
    internal_date = message.get("internalDate")

    received_at = None

    if internal_date:
        received_at = datetime.fromtimestamp(
            int(internal_date) / 1000,
            tz=timezone.utc
        ).isoformat()

    return {
        "id": message.get("id"),
        "threadId": message.get("threadId"),
        "source": "gmail",

        # Các trường header đã trích xuất
        "from": get_header(headers, "From"),
        "to": get_header(headers, "To"),
        "subject": get_header(headers, "Subject"),
        "date": get_header(headers, "Date"),

        "receivedAt": received_at,
        "snippet": message.get("snippet", ""),

        # Bổ sung labelIds
        "labelIds": message.get("labelIds", []),

        # Bổ sung toàn bộ payload.headers
        "payload": {
            "headers": headers
        },

        "body": body,
        "bodyType": body_type
    }


def get_recent_messages(service, query="newer_than:7d"):
    """Lấy tất cả ID email khớp query (mặc định 7 ngày gần nhất)."""

    messages = []
    page_token = None

    while True:
        response = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=100,
            pageToken=page_token
        ).execute()

        messages.extend(response.get("messages", []))

        # Kiểm tra còn trang tiếp theo không
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    return messages


def fetch_notifications(query="newer_than:7d"):
    # 1. Xác thực Gmail
    creds = authenticate()

    service = build(
        "gmail",
        "v1",
        credentials=creds
    )

    # 2. Lấy danh sách email 7 ngày gần nhất
    messages = get_recent_messages(service, query)

    print(f"Found {len(messages)} emails.")

    # 3. Loop lấy chi tiết từng email
    notifications = []

    for index, message in enumerate(messages, start=1):
        message_id = message["id"]

        try:
            notification = get_message_detail(
                service,
                message_id
            )

            notifications.append(notification)

            print(
                f"[{index}/{len(messages)}] "
                f"Processed: {message_id}"
            )

        except Exception as error:
            print(f"Error processing {message_id}: {error}")

    return notifications


def main():
    # Ví dụ: python gmail/main.py "newer_than:30d from:shopee"
    query = " ".join(sys.argv[1:]) or "newer_than:7d"
    notifications = fetch_notifications(query)

    # 4. Xuất kết quả JSON
    OUTPUT_FILE.write_text(
        json.dumps(
            notifications,
            ensure_ascii=False,
            indent=4
        ),
        encoding="utf-8"
    )

    print(f"\nExported {len(notifications)} emails.")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
