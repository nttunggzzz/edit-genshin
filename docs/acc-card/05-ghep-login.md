# /ghep bằng tài khoản/mật khẩu — tích hợp vào bot cũ, giữ nguyên bộ edit cũ

Lệnh mới: `/ghep <tài khoản>-<mật khẩu> [ma <số>] [nen <tên>]`

## Cam kết: bộ edit cũ không đổi

Bản tích hợp này CHỈ thêm một nhánh đăng nhập phía trước. Các file vẽ ảnh
không hề bị sửa: `render.py`, `compose.py`, `layout.py`, `characters.py`,
`bannerbox.py`, `backgrounds.py`, `uidmode.py` (hàm vẽ mảnh) và `hoyolab.py`
(hàm `build_sheet_from_data`). Vì vậy ảnh ra vẫn đúng kiểu cũ: 1920×1080,
chọn hết nhân vật 5★ + 4★ mạnh, vũ khí 5★ + 4★ mạnh với hàng cuối luôn đủ,
thẻ khám phá, ô hồ sơ, ô banner `‹ N ›`, nền chọn ngẫu nhiên trong nhân vật
của acc. Các lệnh cũ `/ghep <UID>`, `/uid`, `/xong` chạy y như trước.

## Luồng A đến Z

1. Người dùng đã được cấp quyền gửi `/ghep <tk>-<mk>` trong chat riêng.
   Bot xoá tin đó ngay (nếu xoá được), rồi mở HoYoLAB bằng Playwright.
2. Bot điền tài khoản/mật khẩu vào khung đăng nhập HoYoverse, bấm Đăng Nhập.
   Mật khẩu chỉ sống trong bộ nhớ của lần này, không ghi file/log/session.
3. Nếu HoYoLAB hiện slider captcha: bot DỪNG và chờ người dùng tự kéo.
   - `local` (mặc định): kéo trực tiếp trong cửa sổ trình duyệt trên máy chạy bot.
   - `web`: bot nhắn link trang web tạm; người dùng kéo trên ảnh, thao tác
     kéo được chuyển tiếp về Playwright. Telegram không kéo slider được.
   - Tối đa 5 phút (`GACARD_LOGIN_TIMEOUT`), hết thì báo và dừng.
4. Đăng nhập xong, bot lấy cookie từ trình duyệt (chỉ trong bộ nhớ) rồi:
   - suy UID Genshin từ tài khoản (bước 5),
   - gọi đúng client Battle Chronicle cũ + đúng hàm vẽ cũ (bước 6).
5. Suy UID: `GET bbs-api-os.hoyolab.com/game_record/card/wapi/getGameRecordCard?uid=<ltuid>`
   (header DS như `hoyolab.py`), lọc `game_id == 2` và `has_role` đúng, lấy
   `game_role_id`. Nhiều acc Genshin -> bot báo danh sách UID, không tự đoán.
6. Vẽ ảnh: xem bảng đối chiếu API dưới đây. Xong, bot gửi ảnh + file JPG như
   mọi lệnh /ghep khác, nút "Đổi nền" / "Làm lại" vẫn hoạt động.

## Bảng đối chiếu API — tránh thiếu, tránh xung đột với code cũ

| Dữ liệu trên ảnh | API / hàm cũ được tái dùng | Ghi chú chống thiếu/xung đột |
|---|---|---|
| Nhân vật, cung mệnh, cấp, vũ khí đang trang bị | `POST sg-act-public-api.hoyolab.com/event/game_record/genshin/api/character/list` qua `HoyolabClient.character_list` + `parse_character_list` | Không viết client mới. Vũ khí chỉ đếm vũ khí đang trang bị (API không trả kho vũ khí chưa gắn) — giống báo cáo cũ. |
| Tổng nhân vật, hồ sơ | `GET .../genshin/api/index` qua `HoyolabClient.index` | `stats.avatar_number` dùng để đối chiếu, không lấy tổng từ thẻ game đã liên kết. |
| Dữ liệu map (thám hiểm) | `index.data.world_explorations` qua `parse_explorations` + `select_explorations` | Vẫn đúng luật cũ: chỉ vùng cha (`parent_id = 0`), vùng cha báo 0 mà có vùng con thì lấy trung bình vùng con, chỉ vẽ vùng < 40 %, % tăng dần, thiếu chỗ thì bỏ từ cuối. Không có endpoint map riêng nào khác được bịa thêm. |
| Lịch sử roll, pity, ô banner `‹ N ›` | `wish.fetch_wish_history` (authkey qua `cookie_token_v2` -> `getGachaLog`, các `gacha_type` 301/302/200/500) + `banner_counter` | Login chỉ khác ở chỗ cookie_token lấy từ trình duyệt vừa đăng nhập, truyền trực tiếp qua tham số — không ghi `.env`, không đổi cache `data/acc-card/wish/<uid>.json` (gộp/khử trùng theo id như cũ). Thiếu `cookie_token_v2` thì chỉ bỏ ô banner kèm lý do, các phần khác vẫn vẽ đủ. |
| Cấp Thế Giới, namecard ô hồ sơ | `hoyolab.fetch_enka_extras` (Enka, cùng UID) | HoYoLAB `index` không trả Cấp Thế Giới — không bịa từ API khác. Không có thì ô hồ sơ chỉ còn AR, như cũ. |
| Chi tiết từng nhân vật (`character/detail`) | KHÔNG dùng cho ảnh acc | Code cũ cũng không dùng cho ảnh acc: `character/list` đã đủ cấp/cung mệnh/vũ khí cần vẽ; thánh di vật/kỹ năng không vẽ lên thẻ. Gọi detail cho từng nhân vật chỉ thêm lượt gọi và nguy cơ bị giới hạn, nên cố tình không thêm. |

## An toàn và giới hạn

- Chỉ dùng trong chat riêng với bot đã cấp quyền; đừng dùng lệnh này trong
  nhóm (tin trong nhóm bot có thể không xoá được, và mọi người trong nhóm
  đều thấy trước khi xoá).
- Bot không tự giải captcha, không dùng solver, không lưu tài khoản/mật khẩu.
  Mỗi lần `/ghep <tk>-<mk>` là một lần đăng nhập mới (phiên trình duyệt tạm,
  không lưu profile lên đĩa).
- Tài khoản có mật khẩu chứa dấu `-`: mật khẩu vẫn được giữ nguyên vì bot
  tách ở dấu ngăn cách đầu tiên; tài khoản chứa `-` thì dùng `:` hoặc `|`
  làm dấu ngăn cách, ví dụ `/ghep my-user:Pass123`.
- Chưa cài Playwright thì lệnh login báo rõ cách cài, các lệnh cũ vẫn chạy.
