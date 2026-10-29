# Bot "Ảnh mô tả acc" (gacard) — Plan master v0

Ngày: 2026-10-21 · Trạng thái: **v1, đang làm P1 (phần không cần ảnh gốc)**

> **Cập nhật v1 (sau khi có 21 ảnh mẫu đã chỉnh):**
> - Bố cục **không cố định**. Bot tự xếp theo nội dung: 3 kiểu `right` / `left` / `wide`, màu khung theo nền.
>   Chi tiết và hợp đồng module ở `docs/acc-card/02-hop-dong-module.md`. `classic-v0.json` chỉ còn là số đo tham khảo.
> - Chủ dự án xác nhận: panel = **cung mệnh**; ô `< N >` = **số lượt roll banner**; trăng lưỡi liềm = **nhựa**;
>   số nhỏ trên thẻ nhân vật và trên panel = **số cung mệnh do người dùng tự điền**.
> - Ảnh gốc sẽ bổ sung sau. Trong lúc chờ, sheet giả lập được cắt từ ảnh mẫu đã chỉnh để chạy toàn bộ đường đi.
> - Cách làm: agent chính lên plan và review. Code do subagent làm theo hợp đồng (A: ghép/bố cục/xuất, B: tách thẻ + sheet giả lập, C: bot).

## 1. Mục tiêu

Người dùng gửi cho bot Telegram **6–7 ảnh chụp màn hình game** của một account Genshin.
Bot tự **nhận diện từng ảnh là màn hình gì**, **cắt các chi tiết cần** (nhân vật, vũ khí, số lần quay,
Hạng Mạo Hiểm, cung mệnh…), **ghép lên ảnh nền nhân vật** theo một bố cục cố định và trả về
**một ảnh JPG 1920×1080** (cho phép 1280×720) mô tả account.

Ảnh mẫu đầu ra: `data/acc-card/reference/mau-1043.jpg` (git bỏ qua vì có UID và watermark của người khác).

Ngoài phạm vi v1: định giá account, đăng bài tự động, đọc dữ liệu từ API/Enka của HoYoverse,
web/dashboard. Bot này **tách biệt với GAIS** (bot cảnh báo bài bán acc đang chạy live): khác token,
khác tiến trình, chỉ chung repo.

## 2. Phân tích ảnh mẫu (mau-1043.jpg)

Kích thước gốc 2000×1125 (16:9). Quy về khung chuẩn 1920×1080 (×0,96). Bản đo thô nằm ở
`assets/acc-card/layouts/classic-v0.json`.

| Vùng | Vị trí (1920×1080) | Nội dung | Nguồn (màn hình game, cần xác nhận) |
|---|---|---|---|
| Mã acc `#1043` | góc trên trái | chữ trắng viền xanh | nhập tay / tự tăng |
| 7 panel trái | x 85, y 284→1009 | icon nguyên tố + tên + avatar nhân vật | **chưa rõ** (xem câu hỏi Q2) |
| 4 panel giữa‑phải | x 1006, y 300→732 | cùng kiểu, có **số đè to** (3, 2, 2, 2) | **chưa rõ**; số đè có vẻ là cung mệnh |
| "CHECK BUILD UID …" | giữa ảnh | chữ trang trí | nhập tay |
| Ô nhân vật `< 4 >` | dưới chữ UID | ô chọn có số 4 | **chưa rõ** (trang phục?) |
| 2 ô vật phẩm | cạnh ô trên | Mối Duyên Vương Vấn ×11, vật phẩm 5★ ×1 | Túi đồ |
| Lưới vũ khí 2×8 | đáy giữa | thẻ vũ khí: tinh luyện, khóa, Lv, avatar chủ | Túi đồ → Vũ khí |
| Hồ sơ | góc trên phải | Hạng Mạo Hiểm 60, EXP, Cấp Thế Giới 9, Sinh Nhật | Thẻ nhà lữ hành / Hồ sơ |
| Lưới nhân vật 6×5 | cột phải | 30 thẻ nhân vật: nguyên tố, sao, Lv | Danh sách nhân vật (có thể 2 ảnh cuộn) |
| Watermark `@sora kiwi` | đáy giữa | chữ mờ | cấu hình |
| Nền | toàn ảnh | splash art nhân vật | thư viện nền |

Nhận xét kỹ thuật:
- Mọi khung có **viền cyan cùng màu** (~#46C3E1) → đây là viền do người chỉnh vẽ, bot tự vẽ lại.
- Thẻ nhân vật/vũ khí là **từng ô cắt riêng rồi xếp lại**, không phải cắt nguyên khối → cần tách ô.
- 30 nhân vật thường không vừa 1 màn hình → phải **ghép nhiều ảnh cuộn và bỏ ô trùng**.

## 3. Kiến trúc

```
Telegram ──► bot (aiogram 3, long polling, không mở cổng)
              │  gom ảnh theo phiên (chat_id), tải file gốc
              ▼
        pipeline thuần (không phụ thuộc Telegram, test được offline)
        1 normalize  : bỏ viền đen/tai thỏ, xác định vùng game, tỉ lệ khung
        2 classify   : ảnh này là màn hình gì (+ độ tin cậy)
        3 extract    : cắt vùng cố định + tách ô lưới + bỏ ô trùng
        4 sheet      : AccountSheet = các mảnh ảnh + metadata (pydantic)
        5 compose    : đặt mảnh vào layout JSON trên nền nhân vật
        6 export     : JPG 1920×1080, q≈90, ≤2 MB
              ▼
        trả về: ảnh xem trước (photo) + file JPG gốc (document)
```

Thành phần:

| Module (`src/gacard/`) | Việc |
|---|---|
| `models.py` | `ScreenShot`, `ScreenType`, `Crop`, `AccountSheet`, `RenderOptions` (pydantic) |
| `normalize.py` | cắt viền, quy chuẩn kích thước, phân lớp tỉ lệ (16:9, 16:10, ~20:9, 4:3) |
| `screens/` | mỗi loại màn hình 1 file: luật nhận diện + cách cắt (đặc tả ở `screens/*.json`) |
| `classify.py` | chấm điểm mọi loại màn hình, chọn cao nhất; dưới ngưỡng → hỏi người dùng |
| `grid.py` | tách ô thẻ theo màu nền độ hiếm (cam 5★, tím 4★…) + gom hàng/cột |
| `dedupe.py` | bỏ ô trùng giữa các ảnh cuộn (perceptual hash) |
| `compose.py` | đọc layout JSON, vẽ nền, khung, mảnh, chữ (Pillow) |
| `backgrounds.py` | thư viện nền: chọn theo tên nhân vật, điểm lấy nét, cover‑fit |
| `cli.py` | `gacard render <thư mục ảnh> [--bg …] [--code …] [--uid …] --out x.jpg`, `gacard calibrate`, `gacard debug` |
| `bot.py` | phiên, lệnh, nút, gửi kết quả |

Thư mục:
- `assets/acc-card/layouts/*.json` — bố cục (commit).
- `assets/acc-card/fonts/` — font OFL có đủ dấu tiếng Việt, ví dụ Be Vietnam Pro (commit).
- `data/acc-card/backgrounds/<slug>/…` — ảnh nền (git bỏ qua: nặng, bản quyền thuộc người vẽ/HoYoverse).
- `data/acc-card/samples/<bộ>/` — bộ ảnh gốc thật để hiệu chỉnh và test (git bỏ qua: có UID).
- `data/acc-card/debug/<phiên>/` — ảnh trung gian từng bước khi bật debug.
- `tests/acc_card/fixtures/` — ảnh tổng hợp nhỏ, không có UID thật (commit).

Thư viện: Pillow, opencv-python-headless, numpy, imagehash, aiogram 3, pydantic 2. Đưa vào
`pyproject.toml` dưới extra `[card]` để không làm nặng GAIS. OCR (Tesseract/PaddleOCR) **chưa dùng** ở v1;
chỉ cân nhắc ở P3 nếu cần đọc số (cung mệnh, AR) để kiểm tra chéo.

Chi phí vận hành: **0 USD/tháng** (chạy trên PC, Telegram miễn phí, không gọi API trả phí) — nằm trong ngân sách 10 USD.

## 4. Chiến lược cắt ảnh (phần khó nhất)

1. **Ưu tiên ảnh gốc**: Telegram nén ảnh gửi dạng "Ảnh" (giảm độ phân giải, thêm nhiễu JPEG). Bot nhận cả hai
   nhưng hướng dẫn gửi dạng **File** khi cần nét. Luôn lấy bản lớn nhất mà Telegram trả về.
2. **Chuẩn hóa**: tìm vùng game (bỏ viền đen, thanh trạng thái), xếp vào 1 trong các lớp tỉ lệ. Toạ độ vùng
   lưu dạng **tỉ lệ 0–1 so với một mốc** (cạnh trái/phải/giữa), không lưu pixel tuyệt đối, vì UI Genshin
   neo theo mép màn hình khi tỉ lệ khác 16:9.
3. **Nhận diện màn hình**: kết hợp (a) so khớp mẫu các "mỏ neo" UI nhỏ (icon tab, tiêu đề), (b) đặc trưng màu
   (mật độ thẻ cam/tím). Điểm thấp → bot hỏi bằng nút "Ảnh này là: Hồ sơ / Nhân vật / Vũ khí / …".
4. **Tách ô lưới** bằng màu nền độ hiếm + contour + gom theo hàng/cột, không dựa vào số cột cố định
   (số cột đổi theo tỉ lệ màn hình).
5. **Thứ tự & trùng**: giữ thứ tự đọc (hàng → cột) theo thứ tự ảnh; bỏ ô trùng bằng pHash.
6. **Lọc**: mặc định giữ đúng thứ người chỉnh ảnh giữ (vd. vũ khí 5★ + vũ khí 4★ tinh luyện 5). Luật lọc là cấu hình.
7. **Không đủ chỗ**: nếu số thẻ vượt số ô của layout → cắt bớt theo thứ tự ưu tiên và báo cho người dùng; thiếu → để ô trống mờ.

Mọi bước có ảnh debug để nhìn lại khi sai.

## 5. Luồng Telegram

1. Người dùng gửi 6–7 ảnh (album hoặc lẻ). Bot gom vào **phiên theo chat**, hết hạn sau 15 phút.
2. Sau khi album dừng ~2 giây, bot trả 1 tin tóm tắt: `Hồ sơ ✅ · Nhân vật 2 ảnh ✅ · Vũ khí ✅ · Túi đồ ✅ · Panel ⚠️ thiếu`
   kèm nút sửa từng ảnh nhận sai.
3. Tuỳ chọn qua caption hoặc lệnh: `/ma 1043`, `/uid 850363095`, `/nen <tên nhân vật>`, `/so 3 2 2 2`.
4. `/xong` (hoặc tự chạy khi đủ màn hình bắt buộc) → render → gửi ảnh xem trước + file JPG.
5. Nút sau khi trả: **Đổi nền**, **Sửa mã/UID**, **Làm lại**.

Lệnh khác: `/start` (hướng dẫn), `/huy`, `/mau` (gửi ảnh mẫu cách chụp), `/trangthai`.
Chỉ chat ID trong danh sách cho phép mới dùng được (`GACARD_ALLOWED_CHAT_IDS`).
**Dùng bot/token riêng** (tạo bot mới ở BotFather): hai tiến trình long polling cùng một token sẽ tranh nhau cập nhật (lỗi 409) và làm hỏng bot GAIS đang chạy.

## 6. Lộ trình

| Giai đoạn | Việc | Tiêu chí xong |
|---|---|---|
| **P0 – Mẫu & đặc tả** | Nhận 2–3 bộ ảnh gốc thật (mỗi bộ 6–7 ảnh) + trả lời mục 9. Viết `docs/acc-card/01-dac-ta-man-hinh.md` (mỗi màn hình: dấu hiệu nhận diện, vùng cắt). Hiệu chỉnh `classic-v0` → `classic-v1`. | Chủ dự án duyệt đặc tả + layout |
| **P1 – Lõi offline** | normalize, classify, extract, grid, dedupe, compose, export, CLI `gacard render`. Test tổng hợp + test trên bộ mẫu thật. | 3 bộ mẫu → ảnh giống mẫu khi đặt cạnh nhau; ≤3 giây/bộ; JPG 1920×1080 ≤2 MB |
| **P2 – Bot MVP** | aiogram, phiên, lệnh, whitelist, trả ảnh + file, log. | Gửi 7 ảnh → nhận kết quả ≤15 giây trên PC |
| **P3 – Bền vững** | Thêm lớp tỉ lệ (điện thoại 20:9, iPad), nút sửa phân loại, chỉnh lệch vùng cắt, nhiều layout, (tuỳ chọn) OCR kiểm tra số. | 90% bộ ảnh thật không cần sửa tay |
| **P4 – Vận hành** | Tự chạy khi khởi động Windows, tự khởi động lại, dọn file tạm, sao lưu cấu hình. | Bot chạy 24/7 trên PC, tắt/mở máy vẫn lên |

## 7. Kiểm thử

- **Đơn vị**: grid/dedupe/normalize trên ảnh tổng hợp vẽ bằng code (không cần ảnh thật).
- **Golden**: mỗi bộ mẫu thật có ảnh đầu ra đã duyệt; so sánh bằng SSIM/sai khác pixel có ngưỡng, xuất ảnh khác biệt khi trượt.
- **Bảng nhìn nhanh**: `gacard debug` xuất contact sheet mọi mảnh đã cắt để mắt người duyệt.
- Test **không bao giờ** gọi Telegram thật (token chỉ nằm ở `.env`; test dùng env riêng — bài học từ GAIS).
- Script in tiếng Việt phải đặt stdout UTF‑8 (Windows cp1252).

## 8. Rủi ro

| Rủi ro | Cách giảm |
|---|---|
| Game đổi giao diện theo phiên bản | Đặc tả màn hình tách thành JSON + `gacard calibrate`; golden test báo sớm |
| Ảnh từ nhiều thiết bị/tỉ lệ khác nhau | Toạ độ tương đối theo mốc + tách ô theo màu; lớp tỉ lệ thêm dần ở P3 |
| Telegram nén ảnh đầu vào | Hướng dẫn gửi dạng File; cảnh báo khi ảnh nhỏ hơn 1280 px |
| Nhận sai màn hình | Ngưỡng tin cậy + nút sửa; không bao giờ render im lặng khi thiếu màn hình bắt buộc |
| Bản quyền ảnh nền/splash art | Nền do chủ dự án cung cấp, không commit vào repo |
| Đụng bot GAIS đang live | Token, tiến trình, file log, khoá chạy riêng |

## 9. Câu hỏi cần chủ dự án trả lời (chặn P0)

1. **Gửi 2–3 bộ ảnh gốc** (6–7 ảnh chưa chỉnh của cùng một acc), tốt nhất kèm ảnh đã chỉnh tương ứng.
2. **7 panel trái và 4 panel có số đè** lấy từ màn hình nào trong game? Số đè (3, 2, 2, 2) đọc từ ảnh hay bạn tự nhập?
3. Ô `< 4 >` và vật phẩm 5★ ×1 cạnh Mối Duyên là gì?
4. Ảnh chụp từ thiết bị nào (PC / điện thoại / iPad)? Một loại hay nhiều loại?
5. Ảnh nền: bạn có sẵn thư viện nền không? Chọn nền theo nhân vật nào (nhân vật đầu lưới, hay bạn chỉ định)?
6. Mã `#1043`: tự tăng hay nhập mỗi lần? Watermark `@hoavu` cố định?
7. Bot chỉ bạn dùng hay cho cả khách/nhân viên?
8. Lọc vũ khí/nhân vật: giữ tất cả, chỉ 5★, hay theo luật khác?

## 10. Skill dự án

Đặt ở `.claude/skills/` (Claude Code tự nạp khi làm việc trong repo):

| Skill | Dùng khi |
|---|---|
| `acc-card-screens` | thêm/sửa loại màn hình, hiệu chỉnh vùng cắt, sửa lỗi nhận diện hoặc tách ô |
| `acc-card-layout` | sửa bố cục, khung, chữ, font, thêm ảnh nền, xuất JPG |
| `acc-card-bot` | sửa luồng Telegram: phiên, lệnh, nút, gửi ảnh, quyền |
| `acc-card-verify` | chạy bộ mẫu, golden test, contact sheet trước khi báo xong hoặc commit |
| `acc-card-enka` | tra Enka theo UID, bảng nhân vật, nhân vật mới ra, điểm hype để chọn nền |

Skill chung nên dùng thêm: `code-review` (trước khi commit), `simplify`, `run` (chạy thử bot),
`skill-creator` (khi muốn đo/chỉnh lại các skill trên).

Phân vai subagent (theo quy ước dự án): Haiku cho tra cứu/sửa máy móc, Sonnet cho code + test thường,
Opus cho kiến trúc, review và gỡ lỗi khó (nhận diện màn hình, tách ô).
