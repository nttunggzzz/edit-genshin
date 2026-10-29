# Hướng Dẫn Sử Dụng Giao Diện Web Local (Edit Genshin)

Giao diện Web Local giúp bạn tạo ảnh thẻ account Genshin Impact độ phân giải cao (**1920×1080**) trực tiếp trên trình duyệt máy tính, thay thế việc phải gửi tài khoản/mật khẩu qua Telegram bot.

Toàn bộ logic cào nhân vật, vũ khí, thám hiểm, lịch sử roll và ghép ảnh thẻ kế thừa 100% từ bộ edit gốc, chất lượng và bố cục ảnh giữ nguyên hoàn toàn.

---

## 🚀 Cách khởi động máy chủ Web

Mở PowerShell tại thư mục dự án `E:\New folder` và chạy lệnh:

```powershell
# 1. Kích hoạt môi trường ảo
.\.venv\Scripts\Activate.ps1

# 2. Khởi chạy Web Local
python -m gacard.webapp --port 8001
```

* **Trang web chính:** [http://localhost:8001/](http://localhost:8001/)
* **Trang Captcha tương tác:** [http://localhost:8766/](http://localhost:8766/) *(tự động kích hoạt khi HoYoLAB yêu cầu)*
* **Dừng máy chủ:** Nhấn `Ctrl + C` trong cửa sổ PowerShell.

*(Lưu ý: Web sử dụng cổng 8001 và 8766, tuyệt đối không xung đột với các cổng 5500 và 7850 trên máy của bạn).*

---

## 📌 Các chức năng chính

### 1. Ghép 1 tài khoản (`/single` hoặc trang chủ)
* **Nhập thông tin:**
  * **Tài khoản HoYoLAB:** Email hoặc tên đăng nhập.
  * **Mật khẩu HoYoLAB:** Mật khẩu tài khoản (chỉ giữ trong RAM lúc đăng nhập và bị xoá ngay lập tức).
  * **Mã acc:** Ví dụ `11171`, `CN#1076` (1–16 ký tự: chữ, số, `-`, `_`, `#`).
* **Chọn nền:**
  * *Tự động (Mặc định):* Chỉ chọn ngẫu nhiên trong danh sách các nhân vật mà tài khoản **thực sự sở hữu**. Không bao giờ chọn nhân vật ngoài acc.
  * *Theo tên nhân vật:* Nhập tên nhân vật (ví dụ `columbina`, `flins`, `raiden`).
  * *Chọn ID nền cụ thể:* Chọn trực tiếp từ thư viện ảnh nền.
* **Xử lý Captcha (nếu có):**
  * Khi HoYoLAB yêu cầu kéo puzzle, trang web sẽ hiện thông báo màu vàng kèm nút **"Mở trang kéo Captcha"**.
  * Bấm vào nút để mở giao diện kéo slider trực tiếp trên trình duyệt, dùng chuột kéo thanh trượt puzzle. Kéo xong web sẽ tự động hoàn tất.
* **Kết quả:**
  * Xem trực tiếp ảnh thẻ 1920×1080.
  * Bấm **Tải Ảnh Về Máy** để lưu file với tên chuẩn `acc-<mã>-<uid>.jpg`.
  * Nút **Đổi Nền Khác**: Tự động chọn một hình nền khác trong pool nhân vật của acc và vẽ lại ngay mà không cần đăng nhập lại.

---

### 2. Ghép hàng loạt bằng file Excel / CSV (`/batch`)
* **Định dạng file:** Hỗ trợ `.xlsx` và `.csv`.
* **Thứ tự các cột:**
  1. **Cột 1:** Mã acc
  2. **Cột 2:** Tài khoản HoYoLAB
  3. **Cột 3:** Mật khẩu HoYoLAB
  4. **Cột 4 (tùy chọn):** Tên nhân vật nền hoặc ID nền (để trống = Tự động)
  5. **Cột 5 (tùy chọn):** Màu khung (cyan, red, pink, purple, green, blue...)
* **Đặc điểm vận hành:**
  * Tự động bỏ qua dòng tiêu đề.
  * Chạy tuần tự từng tài khoản một, không chạy song song để tránh xung đột trình duyệt.
  * Hàng thiếu thông tin hoặc sai mã sẽ báo lỗi riêng mà không làm dừng toàn bộ lô.
  * File gốc tải lên sẽ được xóa ngay sau khi nạp vào bộ nhớ để bảo mật mật khẩu.
  * Sau khi hoàn tất: Có nút **"Tải Tất Cả Ảnh (ZIP)"** để tải toàn bộ các ảnh thẻ thành công về máy dưới dạng 1 file nén zip.

---

### 3. Thư viện ảnh nền (`/backgrounds`)
* Quản lý kho ảnh nền tại `data/acc-card/backgrounds/`.
* Xem thumbnail, kích thước, số lần đã sử dụng và ngày thêm của từng hình nền.
* **Thêm nền mới:** Nhập slug nhân vật (ví dụ `columbina`) và chọn file ảnh (yêu cầu kích thước tối thiểu **1920×1080**).
* **Đổi vị trí nhân vật:** Chọn `left` (trái), `center` (giữa), hoặc `right` (phải) để tối ưu vị trí đè thông tin.
* **Xóa nền:** Xóa hình nền khỏi kho lưu trữ.

---

### 4. Cài đặt kiểu Edit (`/settings`)
* Thiết lập các giá trị mặc định cho form ghép ảnh:
  * Màu khung viền mặc định (cyan, red, pink, purple, green, blue...).
  * Chiều cao ảnh mặc định: 1080p (1920×1080) hoặc 720p (1280×720).
  * Chữ Watermark mờ mặc định.
  * Chế độ chọn nền mặc định.
* Cấu hình lưu trữ tại `data/acc-card/web-settings.json` (không đụng vào `.env` của bot).
