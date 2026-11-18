# Thu thập thông báo iPhone và Windows

Bộ script này gom thông báo trên máy Windows vào một file JSON. Có hai nguồn:

- **iPhone**, qua ứng dụng **Phone Link** (Liên kết điện thoại): Zalo, Messenger, Shopee, ngân hàng, Discord, YouTube…
- **Ứng dụng trên PC**: Discord, Teams, Zalo PC, Telegram Desktop, thông báo web của Chrome/Edge…

Kết quả nằm trong `windows_notifications/notifications.json`, cùng định dạng với các connector khác của project (Gmail, Outlook…).

| Script | Dùng để | Khi nào chạy |
|---|---|---|
| `phonelink_history.py` | Lấy các thông báo iPhone **đã có sẵn** trong cửa sổ Phone Link | Một lần lúc bắt đầu, hoặc khi muốn bổ sung |
| `main.py` | Theo dõi thông báo **mới** theo thời gian thực | Để chạy liên tục trong lúc thu thập |

Hai script ghi chung một file và **không tạo bản trùng**: cùng một thông báo dù bắt được bởi script nào cũng ra cùng một `id`.

---

## 1. Yêu cầu

- Windows 10/11 đã cài **Phone Link** (có sẵn trên Windows 11).
- iPhone chạy iOS 14 trở lên, PC có Bluetooth.
- Python 3.10+ và các thư viện trong `requirements.txt`:

```bash
# tại thư mục gốc của project
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

`phonelink_history.py` cần `pywinauto`. `main.py` chỉ dùng thư viện chuẩn của Python.

---

## 2. Cài đặt một lần

### 2.1. Ghép iPhone với Phone Link
1. Mở **Phone Link** trên PC → chọn **iPhone** → quét mã QR bằng camera iPhone.
2. Đồng ý ghép nối Bluetooth trên cả hai máy.

### 2.2. Trên iPhone
1. **Cài đặt → Bluetooth** → bấm (i) cạnh tên PC → bật **Chia sẻ thông báo hệ thống** và **Hiển thị thông báo**.
2. **Cài đặt → Thông báo → Hiển thị bản xem trước** → chọn **Luôn luôn**.
   Nếu không đổi, nội dung thông báo chỉ hiện là "Thông báo".

### 2.3. Trên PC: bật banner (bắt buộc cho phần realtime)
Phone Link **không lưu thông báo iPhone ra ổ đĩa**. Thông báo chỉ vào kho thông báo của Windows (nơi `main.py` đọc) khi bật banner:

1. Phone Link → **Settings** (bánh răng) → **Features** → **Notifications** → bật **Show notification banners**.
2. Windows **Cài đặt → Hệ thống → Thông báo**:
   - bật thông báo chung;
   - tìm **Phone Link** → bật, và bật **Hiển thị trong trung tâm thông báo**;
   - tắt **Không làm phiền** trong lúc thu thập.

**Kiểm tra:** nhờ ai đó nhắn cho bạn một tin Zalo hoặc Messenger. Nếu banner hiện ở góc dưới bên phải màn hình là đã ổn.

---

## 3. Bước 1: Gom thông báo cũ

```bash
python windows_notifications/phonelink_history.py
```

Trước khi chạy:
- **Mở cửa sổ Phone Link** và không thu nhỏ nó.
- Nếu Phone Link đang mở Settings hoặc Messages, cột Notifications bị thu gọn (chỉ hiện biểu tượng và nhãn kiểu "+53"). Script sẽ tự mở rộng cột này ra.

Script dùng Windows UI Automation để đọc chữ trong danh sách thông báo của Phone Link. Nó tự cuộn từ đầu tới cuối danh sách, rồi cuộn về đầu khi xong. Trong lúc chạy, bạn đừng thao tác trên cửa sổ Phone Link.

Kết quả mẫu:
```
Đang đọc danh sách thông báo trong Phone Link...
Đọc được 56 mục.
[mb_bank] 🎁 Voucher lãi suất 8.0%/năm: ...
[youtube] MixiGaming: Siêu nhân nene 😤
...
Thêm 56 thông báo. Tổng 59 -> ...\windows_notifications\notifications.json
```

Chạy lại bao nhiêu lần cũng được, script chỉ thêm những thông báo chưa có.

**Giới hạn:**
- Chỉ lấy được thông báo **còn hiển thị** trong Phone Link. Thông báo đã bị xoá hoặc đã bấm tắt thì mất.
- Thời gian chỉ chính xác tới **phút**, vì Phone Link hiển thị dạng `11/16/2026 at 17:15`.

---

## 4. Bước 2: Theo dõi realtime

```bash
python windows_notifications/main.py
```

Script quét kho thông báo của Windows **5 giây một lần** và in ra mỗi thông báo mới:
```
Đã có 59 thông báo trong file.
Đang theo dõi thông báo Windows... (Ctrl+C để dừng)

[zalo/iphone] Nguyễn Văn A: Tối nay đi ăn không?
[discord/pc] general: ...
```
Bấm **Ctrl+C** để dừng. Những gì đã bắt được đều đã lưu vào file.

Tuỳ chọn:

| Lệnh | Ý nghĩa |
|---|---|
| `python windows_notifications/main.py` | Theo dõi liên tục |
| `python windows_notifications/main.py --once` | Lấy những thông báo đang có rồi thoát |
| `python windows_notifications/main.py --interval 2` | Quét 2 giây một lần (mặc định 5) |
| `python windows_notifications/main.py --raw` | In cấu trúc XML của thông báo để debug. Nội dung được thay bằng số ký tự, không in chữ thật. |

**Lưu ý:**
- Windows **xoá thông báo khỏi kho** khi bạn bấm tắt nó hoặc khi nó hết hạn. Script phải đang chạy thì mới kịp bắt.
- Script chỉ **đọc bản sao** của DB (`%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db`), không sửa gì của Windows.
- Đừng chạy hai bản `main.py` cùng lúc, vì cả hai sẽ ghi đè cùng một file.

---

## 5. Quy trình gợi ý

```
1. Cài đặt một lần (mục 2)
2. python windows_notifications/phonelink_history.py   -> gom thông báo cũ
3. python windows_notifications/main.py                -> để chạy liên tục
4. (tuỳ chọn) chạy lại phonelink_history.py định kỳ    -> bù những thông báo
   realtime bị sót, ví dụ khi script không chạy hoặc banner bị tắt
```

Bước 4 an toàn vì có cơ chế khử trùng (xem mục 7).

---

## 6. Định dạng dữ liệu

Mỗi phần tử trong `notifications.json`:

```json
{
    "id": "a1b2c3d4e5f6a7b8",
    "threadId": "phonelink|MB Bank|🎁 Voucher lãi suất 8.0%/năm",
    "source": "mb_bank",
    "from": "🎁 Voucher lãi suất 8.0%/năm",
    "to": "me",
    "subject": "🎁 Voucher lãi suất 8.0%/năm",
    "date": "2026-11-18T09:30:00+07:00",
    "receivedAt": "2026-11-18T09:30:00+07:00",
    "snippet": "200 ký tự đầu của nội dung",
    "labelIds": ["WINDOWS", "IPHONE", "PHONELINK_HISTORY"],
    "payload": { "via": "iphone", "app": "MB Bank", "bundleId": null, "...": "..." },
    "body": "Nội dung đầy đủ",
    "bodyType": "text/plain"
}
```

| Trường | Ý nghĩa |
|---|---|
| `source` | Tên app viết thường, khoảng trắng đổi thành `_`: `zalo`, `mb_bank`, `viettel_money`, `discord`… Nếu không xác định được app thì là `iphone`. |
| `from` / `subject` | Tiêu đề thông báo, thường là người gửi hoặc tên nhóm |
| `body` | Nội dung thông báo |
| `receivedAt` | Thời gian theo giờ máy (+07:00) |
| `payload.via` | `iphone` (qua Phone Link), `pc` (app trên máy tính), `browser` (thông báo web) |
| `payload.bundleId` | Bundle ID của app trên iPhone, ví dụ `com.hammerandchisel.discord`. Chỉ có ở thông báo bắt bằng realtime. |

Ý nghĩa của các nhãn trong `labelIds`:

| Nhãn | Nguồn |
|---|---|
| `PHONELINK_HISTORY` | Đọc từ danh sách Phone Link (`phonelink_history.py`) |
| `PHONELINK_TOAST` | Bắt từ banner realtime (`main.py`) |
| `WINDOWS` + `PC` / `BROWSER` | Thông báo của app trên PC hoặc của trình duyệt |

---

## 7. Cách khử trùng

Với thông báo iPhone, `id` được tính như sau:

```
sha1("phonelink|{app}|{tiêu đề}|{nội dung}|{thời gian tới phút}")
```

Hai script dùng chung hàm `phone_notification()` trong `main.py`, nên cùng một thông báo luôn ra cùng một `id`, dù bắt được từ banner hay từ danh sách. Thông báo của app trên PC thì dùng `id` tính từ app gửi, thời gian đến và nội dung.

**Trường hợp vẫn có thể bị trùng:** banner tới lúc 14:44:59 nhưng Phone Link hiển thị 14:45. Khi đó thông báo bị lưu thành 2 bản, và bước xử lý sau có thể gộp lại theo app, tiêu đề và nội dung.

---

## 8. Xử lý sự cố

| Hiện tượng | Nguyên nhân và cách xử lý |
|---|---|
| Phone Link có 50+ thông báo nhưng `main.py` chỉ thấy vài cái | Chưa bật banner (mục 2.3). Thông báo cũ thì lấy bằng `phonelink_history.py`. |
| `Không thấy cửa sổ Phone Link` | Mở Phone Link và đừng thu nhỏ cửa sổ. |
| `Không thấy danh sách Notifications` | Đóng Settings trong Phone Link và quay về màn hình chính rồi chạy lại. |
| `phonelink_history.py` đọc được ít hơn số trên Phone Link | Đừng chạm vào cửa sổ Phone Link trong lúc script cuộn, rồi chạy lại. Lần chạy sau chỉ thêm phần còn thiếu. |
| Nội dung chỉ là "Thông báo" | Trên iPhone, chỉnh **Hiển thị bản xem trước** thành **Luôn luôn**. |
| `source` là `iphone` thay vì tên app | Tên app không có trong danh sách app của Phone Link. Thêm tên app vào `DEFAULT_APPS` trong `phonelink_history.py`. |
| Không có thông báo realtime mới | Phone Link mất kết nối Bluetooth (mở lại Phone Link), hoặc đang bật Không làm phiền. |
| Phone Link cập nhật xong thì script lỗi | Cấu trúc giao diện hoặc XML đã đổi. Chạy `main.py --raw` để xem cấu trúc mới rồi chỉnh parser. |

---

## 9. Quyền riêng tư

- `notifications.json` chứa **nội dung tin nhắn, giao dịch ngân hàng, OTP…** Đừng commit hay chia sẻ file này. File đã có sẵn trong `.gitignore`.
- Trước khi đưa dữ liệu cho AI hoặc dịch vụ bên ngoài để thử nghiệm, nên che OTP, số tài khoản, số dư và số điện thoại.
