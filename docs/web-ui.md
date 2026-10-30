# Hướng Dẫn Sử Dụng Giao Diện Web Local (Edit Genshin)

Giao diện Web Local giúp tạo ảnh thẻ account Genshin Impact độ phân giải cao (**1920×1080**) trực tiếp trên trình duyệt máy tính, thay thế việc phải gửi tài khoản/mật khẩu qua Telegram bot.

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

*(Lưu ý: Web sử dụng cổng 8001 và 8766, tuyệt đối không xung đột với các cổng 5500 và 7850 trên máy).*

---

## 🛡️ Kiến trúc & Quy trình vận hành an toàn

1. **Context ẩn danh cô lập hoàn toàn cho từng tài khoản:**
   * Mỗi lần ghép ảnh (dù là Ghép 1 Acc hay từng hàng trong Ghép Hàng Loạt), Playwright khởi tạo một Browser Context hoàn toàn mới (`browser.new_context()`).
   * Phiên chạy hoàn toàn trong bộ nhớ RAM (Incognito / In-memory), không lưu cache, không chia sẻ cookie giữa các tài khoản.
   * Cấu hình fingerprint cố định và nhất quán: Viewport `1280×800`, User-Agent Chrome chuẩn, ngôn ngữ `vi-VN`, múi giờ `Asia/Ho_Chi_Minh`.
   * Trình duyệt và Context được đóng ngay lập tức (`context.close()`, `browser.close()`) ngay khi nhận đủ cookie phiên.

2. **Thanh kiểm soát nhịp độ đăng nhập (Pacing Stats):**
   * Hiển thị trực quan ở đầu trang: *Lần đăng nhập trước*, *Lần đăng nhập hiện tại*, và *Số acc đã chạy trong 1 giờ qua*.
   * Giúp người dùng dễ dàng tự canh khoảng cách thời gian giữa các tài khoản để bảo vệ IP và tránh bị HoYoLAB nghi ngờ.

3. **Giãn cách cào dữ liệu (Delay 1–5 giây):**
   * Sau khi đăng nhập thành công, bot tự động áp dụng khoảng nghỉ ngẫu nhiên `1.0 – 5.0 giây` giữa các bước gọi API HoYoLAB (Tổng quan, Danh sách nhân vật, Enka stats, Lịch sử roll).
   * Không bao giờ bắn dồn dập các request API trong thời gian ngắn.

4. **Lưu trữ dữ liệu sạch dạng JSON (`acc-<mã>-<uid>.json`):**
   * Sau khi cào xong, dữ liệu được làm sạch và lưu ngay vào `web-out/acc-<mã>-<uid>.json` trước khi vẽ ảnh.
   * File JSON **tuyệt đối không chứa** mật khẩu, cookie, ltoken, ltuid hay authkey.

5. **Nút "Đổi nền khác" vẽ trực tiếp từ JSON đã lưu:**
   * Khi bấm **Đổi Nền Khác**, hệ thống đọc trực tiếp dữ liệu từ file JSON sạch đã lưu trong `web-out/`, tự động chọn một hình nền ngẫu nhiên khác trong số nhân vật sở hữu (loại trừ nền vừa dùng) và vẽ lại ảnh mới.
   * **Tuyệt đối không đăng nhập lại, không gửi bất kỳ request nào đến máy chủ HoYoLAB.**

6. **Tự động ngắt khi gặp giới hạn tần suất (Too many requests):**
   * Nếu HoYoLAB phản hồi thông báo quá giới hạn yêu cầu, hệ thống sẽ dừng ngay lập tức và gắn cờ `stopped` cho toàn bộ hàng đợi.
   * Giao diện web hiển thị cảnh báo đỏ yêu cầu người dùng tạm dừng ít nhất 15–30 phút trước khi tiếp tục để tránh khóa IP.

---

## 📌 Các chức năng trên giao diện Web

### 1. Ghép 1 tài khoản (`/single` hoặc trang chủ) — Luồng CDP Trực Quan
* **Tài khoản HoYoLAB:** Ô nhập text hiển thị rõ ràng để dễ dàng phát hiện lỗi gõ nhầm.
* **Mật khẩu HoYoLAB:** Ô nhập dạng password có nút vector SVG để ẩn/hiện mật khẩu khi cần kiểm tra lại. Mật khẩu được xóa khỏi RAM ngay sau khi đăng nhập xong.
* **Mã acc:** Ví dụ `11171`, `CN#1076` (1–16 ký tự: chữ, số, `-`, `_`, `#`).
* **Chế độ chọn nền:**
  * *Tự động (Mặc định):* Chỉ chọn ngẫu nhiên trong danh sách các nhân vật mà tài khoản **thực sự sở hữu**. Tuyệt đối không chọn nhân vật ngoài acc.
  * *Theo tên nhân vật:* Nhập tên nhân vật (ví dụ `columbina`, `flins`, `raiden`).
  * *Chọn ID nền cụ thể:* Chọn trực tiếp từ thư viện ảnh nền.
* **Luồng chạy qua trình duyệt do người dùng mở (CDP Port 9222):**
  1. Sau khi bấm **Bắt đầu Ghép Ảnh**, web hiển thị trạng thái chờ tối đa 120 giây kèm đồng hồ đếm ngược.
  2. Người dùng bấm đúp file `scripts/launch_cdp_browser.bat` trên máy (tiến trình foreground hiển thị trực tiếp trước màn hình, không bị Windows chặn).
  3. **Lưu ý quan trọng:** Tuyệt đối không tự gõ tài khoản/mật khẩu vào trình duyệt; bot sẽ tự động kết nối qua cổng 9222 và điền form cho bạn.
  4. Nếu HoYoLAB yêu cầu kéo Captcha, bạn chỉ việc kéo trực tiếp thanh trượt trên cửa sổ trình duyệt đó. Kéo xong bot sẽ tự động nhận diện và tiếp tục.
  5. Xong việc, bot đóng hoàn toàn trình duyệt CDP, xóa sạch profile tạm và giải phóng mật khẩu khỏi bộ nhớ.
* **Kết quả:**
  * Xem trực tiếp ảnh thẻ kích thước chuẩn 1920×1080 (hoặc 720p).
  * Nút **Tải Ảnh Về Máy** tải file với tên `acc-<mã>-<uid>.jpg`.
  * Nút **Đổi Nền Khác**: Đổi nền và render lại ngay từ JSON mà không cần đăng nhập lại.

---

### 2. Ghép hàng loạt bằng file Excel / CSV (`/batch`)
* **Định dạng file:** Hỗ trợ `.xlsx` và `.csv`.
* **Thứ tự các cột (bỏ qua dòng tiêu đề):**
  1. **Cột 1:** Mã acc
  2. **Cột 2:** Tài khoản HoYoLAB
  3. **Cột 3:** Mật khẩu HoYoLAB
  4. **Cột 4 (tùy chọn):** Tên nhân vật nền hoặc ID nền (để trống = Tự động theo nhân vật trong acc)
  5. **Cột 5 (tùy chọn):** Màu khung (cyan, red, pink, purple, green, blue...)
* **Quy tắc chạy lô:**
  * Xử lý tuần tự từng tài khoản một trên Context ẩn danh độc lập.
  * File Excel/CSV gốc được xóa ngay khỏi đĩa sau khi đọc danh sách vào bộ nhớ.
  * Nếu gặp lỗi `Too many requests`, tiến trình lập tức dừng lại để bảo vệ tài khoản và IP.
  * Sau khi hoàn tất: Có nút **"Tải Tất Cả Ảnh (ZIP)"** để tải toàn bộ ảnh thẻ thành công.

---

### 3. Thư viện ảnh nền (`/backgrounds`)
* Quản lý kho ảnh nền tại `data/acc-card/backgrounds/`.
* Xem danh sách thumbnail, kích thước (tối thiểu 1920×1080), vị trí nhân vật (left, center, right), số lần sử dụng.
* Hỗ trợ thêm ảnh nền mới theo tên nhân vật hoặc xóa hình nền.

---

### 4. Cài đặt kiểu Edit (`/settings`)
* Cấu hình mặc định cho các lần ghép ảnh:
  * Màu khung viền mặc định.
  * Chiều cao ảnh mặc định: 1080p (1920×1080) hoặc 720p (1280×720).
  * Chữ Watermark mờ mặc định.
* Lưu trữ riêng tại `data/acc-card/web-settings.json`, không ảnh hưởng tới file môi trường hệ thống.
