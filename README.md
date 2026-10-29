# Edit Genshin (Genshin Account Card Generator & Telegram Bot)

Bot Telegram và công cụ tự động tạo ảnh thẻ mô tả account Genshin Impact độ phân giải cao (**1920×1080**) theo khung thiết kế PSD chuyên nghiệp.

Dự án cung cấp giải pháp tự động hoàn toàn: nạp thông tin tài khoản qua **HoYoLAB Battle Chronicle** (đầy đủ nhân vật, vũ khí, thám hiểm, lịch sử roll), qua **UID (Enka.Network)** hoặc ghép từ **ảnh chụp màn hình game**.

---

## ✨ Tính năng chính

### 1. Nạp đầy đủ qua HoYoLAB Battle Chronicle (Khuyên dùng)
* Tự động lấy toàn bộ danh sách nhân vật sở hữu và cung mệnh.
* Lọc danh sách vũ khí 5★ và 4★ mạnh, dàn lưới 9 cột đều đặn.
* Bóc tách độ thám hiểm các khu vực (Mondstadt, Liyue, Inazuma, Sumeru, Fontaine, Natlan...).
* Tự động tính số lượt quay roll gacha pity nhân vật: ô banner `‹ N ›` (khung banner giới hạn hoặc lệch).
* Lệnh nhanh: `/ghep <UID> ma <MÃ_ACC> nen <TÊN_NỀN>`

### 2. Chế độ chỉ cần UID (Enka.Network)
* Không cần đăng nhập, chỉ cần mở công khai tủ trưng bày nhân vật trong game.
* Tự động lấy thông tin Level, Hạng Mạo Hiểm (AR), Cấp Thế Giới (WL) và nhân vật trưng bày.
* Lệnh nhanh: `/uid <UID> [ma <MÃ_ACC>] [nen <TÊN_NỀN>]`

### 3. Chế độ ghép từ ảnh chụp màn hình
* Gửi 4–7 ảnh chụp màn hình game (Hồ sơ, Túi đồ vũ khí, Danh sách nhân vật, Cung mệnh) dưới dạng File vào bot.
* Gõ `/xong`: Bot tự động phân loại từng màn hình, cắt tách từng ô nhân vật/vũ khí, khử trùng lặp và ghép thành 1 ảnh thẻ hoàn chỉnh 1920×1080.

### 4. Hệ thống hình nền (Backgrounds) tùy biến
* Tích hợp sẵn catalog ảnh nền các nhân vật nổi bật (Columbina, Flins, Nefer, Zibai...).
* Cho phép người dùng hoặc Admin thêm hình nền tùy ý qua lệnh: `/themnen <tên>` rồi gửi ảnh.

### 5. Telegram Bot tương tác & Phân quyền thông minh
* Quản lý truy cập: Chỉ những Chat ID hoặc `@username` được cấp quyền mới có thể dùng bot.
* Lệnh quản trị cho Admin:
  * `/moi @username` hoặc `/moi <chat_id>`: Mời người dùng mới.
  * `/bomoi @username`: Thu hồi quyền.
  * `/danhsach`: Xem danh sách những người được phép sử dụng.
* Hỗ trợ chỉnh sửa cung mệnh thủ công nếu cần: `/cm <tên_nhân_vật> <số_cung_mệnh 0-6>`.
* Xem trợ giúp: `/help` hoặc `/trogiup`.

---

## 🚀 Hướng dẫn cài đặt & Chạy trên máy cá nhân

### Yêu cầu
* Python 3.10 trở lên (khuyên dùng Python 3.12+).
* Môi trường Windows, Linux hoặc macOS.

### 1. Cài đặt môi trường

```powershell
# Di chuyển vào thư mục dự án
cd edit-genshin

# Tạo môi trường ảo Python
py -m venv .venv

# Kích hoạt môi trường ảo
.\.venv\Scripts\Activate.ps1

# Cài đặt các thư viện cần thiết
python -m pip install -U pip
pip install -e ".[dev]"
```

*(Trên Linux / macOS: dùng `python3 -m venv .venv` và `source .venv/bin/activate`)*

---

### 2. Thiết lập cấu hình (`.env`)

Sao chép file mẫu:
```powershell
Copy-Item .env.example .env
```

Mở `.env` và điền:
```ini
# Token lấy từ @BotFather trên Telegram
GACARD_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz

# Chat ID của bạn (lấy qua @userinfobot)
GACARD_ALLOWED_CHAT_IDS=123456789
GACARD_ADMIN_CHAT_IDS=123456789

# (Tùy chọn) Cookie HoYoLAB nếu muốn dùng chế độ lấy full nhân vật & lịch sử roll
GACARD_HOYOLAB_COOKIE=
```

---

### 3. Khởi chạy Bot Telegram

Chạy lệnh:
```powershell
python -m gacard.bot
```

Sau khi bot khởi động, vào Telegram tìm bot của bạn và gõ `/start` hoặc `/help` để bắt đầu!

---

### 4. Chạy thử nghiệm bằng dòng lệnh (CLI Offline)

Bạn cũng có thể render ảnh trực tiếp mà không cần bật Telegram Bot:

```powershell
# Chế độ tra cứu và render từ UID (Enka):
python -m gacard.uidmode 887630784 --code 1043 --bg columbina

# Chế độ render qua HoYoLAB Battle Chronicle:
python -m gacard.hoyolab 887630784 --code 1116 --bg columbina
```
Ảnh xuất ra sẽ nằm trong thư mục `output/`.

---

## 🧪 Chạy kiểm thử tự động (Unit Tests)

Dự án bao gồm hơn 700 bài kiểm thử tự động bao quát toàn bộ pipeline:

```powershell
python -m pytest tests/acc_card
```

---

## 📁 Cấu trúc thư mục

```
edit-genshin/
├── src/gacard/
│   ├── bot/                 # Ứng dụng Bot Telegram (handlers, services, session, access)
│   ├── hoyolab.py           # Client kết nối HoYoLAB Battle Chronicle
│   ├── wish.py              # Xử lý lịch sử roll gacha & tính toán số pity
│   ├── bannerbox.py         # Vẽ ô banner roll pity ‹ N ›
│   ├── backgrounds.py       # Quản lý và chọn hình nền
│   ├── uidmode.py           # Chế độ vẽ card chỉ từ UID qua Enka
│   ├── enka.py              # Client gọi Enka.Network API
│   ├── render.py            # Vẽ canvas, xếp layout các module thẻ ảnh
│   ├── compose.py           # Bộ ghép ảnh tổng thể
│   ├── layout.py            # Tính toán tọa độ và lưới các ô
│   ├── characters.py        # Dữ liệu nhân vật và phân loại sao
│   ├── theme.py             # Quản lý bảng màu và font chữ
│   └── cli.py               # Giao diện dòng lệnh gacard
├── assets/acc-card/         # Font chữ, dữ liệu JSON nhân vật và layout mẫu
├── data/acc-card/
│   ├── templates/banner/    # Template đồ họa ô banner roll (limited, lech)
│   └── backgrounds/         # Thư viện hình nền nhân vật
├── tests/acc_card/          # Toàn bộ test case tự động
├── docs/acc-card/           # Tài liệu kỹ thuật, phân tích PSD và đặc tả API
├── pyproject.toml           # Cấu hình dự án và dependencies
├── .env.example             # File mẫu biến môi trường
└── .gitignore               # Danh sách loại trừ file nhạy cảm và cache
```
