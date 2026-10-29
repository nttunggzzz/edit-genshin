"""Toàn bộ chuỗi tiếng Việt của bot (một chỗ để sửa lời)."""

from __future__ import annotations

REFUSAL = "Bot này chỉ dành cho người được cấp quyền. Chat ID của bạn: {chat_id} (gửi cho chủ bot nếu cần thêm quyền)."

# Cách dùng, tối đa 5 dòng (gửi khi /huongdan, và là phần đầu của /start).
HUONGDAN = (
    "1. Đủ nhân vật (HoYoLAB): /ghep 887630784 ma 1116 nen columbina — chủ acc cần bật công khai Sổ Tay Hành Trình.\n"
    "2. Đăng nhập rồi ghép: /ghep <tài khoản>-<mật khẩu> ma 1116 — chỉ dùng trong chat riêng, bot xoá tin ngay sau khi nhận.\n"
    "3. Chỉ UID (Enka, tủ trưng bày): /uid 887630784 ma 1116 nen columbina → tra Enka và vẽ ảnh luôn.\n"
    "4. Ảnh chụp: gửi 4–7 ảnh (dạng File), caption: ma 1116 uid 887630784, rồi /xong. Bấm \"Đổi nền\" / \"Làm lại\" nếu muốn.\n"
    "5. Thiếu cung mệnh: /cm columbina 2 zibai 0. Thêm nền: /themnen <ten> rồi gửi ảnh nền.\n"
    "6. /huy để làm lại. KHÔNG gửi tài khoản/mật khẩu ngoài lệnh /ghep ở mục 2. Ảnh lưu ở output: acc-<ma>-<uid>-hoyolab|uid|chup.jpg. Mọi lệnh: /help"
)

START = (
    "Chào! Mình ghép ảnh mô tả acc Genshin 1920x1080.\n\n"
    + HUONGDAN
    + "\n\nXem toàn bộ lệnh kèm ví dụ: /help (hoặc /trogiup)."
)

MAU = (
    "Cách chụp (ảnh mẫu sẽ bổ sung sau):\n"
    "- Chụp ở độ phân giải cao nhất, gửi dạng File (không phải ảnh nén).\n"
    "- Hồ sơ: màn hình hồ sơ có Hạng Mạo Hiểm / Cấp Thế Giới.\n"
    "- Nhân vật: danh sách nhân vật, cuộn từng trang, mỗi trang một ảnh.\n"
    "- Vũ khí: danh sách vũ khí đã lọc theo ý bạn.\n"
    "- Panel cung mệnh / túi đồ (nhựa, Mối Duyên...): chụp thêm nếu có.\n"
    "Tổng cộng khoảng 4–7 ảnh."
)

NEED_IMAGES = "Chưa có ảnh nào. Gửi 4–7 ảnh chụp màn hình (nên gửi dạng File), rồi gõ /xong."
NO_SESSION = "Chưa có phiên nào. Cứ gửi ảnh để bắt đầu, hoặc /start để xem hướng dẫn."
SESSION_EXPIRED = "Phiên đã hết hạn sau 15 phút không hoạt động nên mình đã xoá ảnh. Gửi lại ảnh để bắt đầu phiên mới."
SESSION_CANCELLED = "Đã huỷ và xoá phiên."
BUSY = "Mình đang xử lý ảnh trước đó, đợi chút nhé."
GENERIC_ERROR = "Có lỗi bất ngờ khi xử lý. Chi tiết đã ghi vào log; bạn thử lại hoặc /huy rồi gửi lại ảnh."

TOO_MANY = "Mỗi phiên tối đa {n} ảnh; ảnh vừa gửi bị bỏ qua. Gõ /xong để ghép hoặc /huy để làm lại."
TOO_BIG = "Ảnh quá lớn ({mb:.1f} MB), Telegram chỉ cho bot tải tối đa 20 MB mỗi ảnh. Hãy nén bớt hoặc chụp lại."
NOT_IMAGE = "File này không phải ảnh png/jpg/webp nên mình bỏ qua."
UNREADABLE = "Không đọc được ảnh vừa gửi (file hỏng?). Hãy gửi lại."
SMALL_IMAGE = "ảnh #{i} nhỏ ({w}x{h}, cạnh dài < {min}px) — nên gửi dạng File để giữ nguyên độ nét"

ENKA_OFF = "Tra Enka chưa được bật trên bot này nên bỏ qua. Dùng /cm để nhập cung mệnh tay."
ENKA_FETCHING = "Đang tra Enka cho UID {uid}..."
ENKA_FAIL = "Không tra được Enka cho UID {uid}: {why}\nBạn vẫn ghép được: nhập cung mệnh tay bằng /cm, hoặc gõ lại /uid."
UNKNOWN_WHY = "lỗi không xác định (đã ghi log)"
ENKA_WAIT_TIMEOUT = "Enka trả lời chậm quá, mình ghép luôn không có dữ liệu Enka (dùng /cm nếu cần cung mệnh)."

BAD_CODE = "Mã acc không hợp lệ. Dùng chữ, số, #, - (tối đa 16 ký tự), ví dụ /ma 1043"
BAD_UID = "UID không hợp lệ: cần 9–10 chữ số, ví dụ /uid 850363095"
CODE_SET = "Đã đặt mã acc: {code}"
UID_SET = "Đã đặt UID {uid}."
NEED_CM_ARGS = "Cú pháp: /cm <ten nhan vat> <so 0-6> [...]. Ví dụ: /cm columbina 2 zibai 0"
CM_SET = "Đã ghi cung mệnh tay: {items}"
CM_ERRORS = "Chưa ghi được:\n- {errors}"

NEED_NEN_ARGS = "Cú pháp: /nen <ten nhan vat>. Ví dụ /nen columbina"
UNKNOWN_CHARACTER = "Không nhận ra nhân vật '{name}'. Thử gõ tên tiếng Anh hoặc tiếng Việt khác."
NEN_SET = "Đã chọn nền theo nhân vật: {name} (ghi đè lựa chọn tự động)."
NEED_THEMNEN_ARGS = "Cú pháp: /themnen <ten nhan vat>, rồi gửi ảnh nền (dạng File, từ 1920x1080). Ví dụ /themnen columbina"
THEMNEN_WAIT = "Ok, gửi ảnh nền của {name} (nên gửi dạng File, tối thiểu 1920x1080). Gõ /huy để bỏ."
BG_ADDED = "Đã lưu nền cho {name} (id {id}). Kho hiện có {count} nền của nhân vật này."
BG_REJECTED = "Không lưu được nền: {why}"
BG_PHOTO_HINT = "Gợi ý: Telegram nén ảnh gửi dạng Ảnh xuống tối đa 1280px, hãy gửi lại dạng File/Document."
BG_EMPTY = "Kho nền đang trống. Dùng /themnen <ten> rồi gửi ảnh nền."
BG_LIST_HEAD = "Kho nền ({total} ảnh):"
NEED_XOANEN_ARGS = "Cú pháp: /xoanen <id>. Xem id bằng /dsnen."
BG_NOT_FOUND = "Không thấy nền có id '{id}'. Xem lại bằng /dsnen."
BG_REMOVED = "Đã xoá nền {id} ({name})."
HYPE_HEAD = (
    "Bảng điểm hype (chỉ để tham khảo, KHÔNG còn dùng để chọn nền: nền được chọn ngẫu nhiên trong các "
    "nhân vật có trong acc):"
)
HYPE_EMPTY = "Chưa có bảng điểm hype (assets/acc-card/hype.json)."
HYPE_FOOT = "Muốn đổi điểm: sửa file assets/acc-card/hype.json (nhờ chủ dự án)."

RENDERING = "Đang ghép ảnh..."
RENDER_DONE_DOC = "File JPG gốc (không nén). Bấm nút bên dưới để đổi nền hoặc làm lại."
CROP_FAIL = "Chưa tách được mảnh từ ảnh: {why}"
RENDER_FAIL = "Ghép ảnh thất bại: {why}\nKiểm tra lại ảnh rồi gõ /xong, hoặc /huy để làm lại."
NO_FIXTURE = (
    "Chưa có sheet mẫu để dùng thay cho bước cắt ảnh (data/acc-card/fixtures/<ma>/sheet.json). "
    "Báo chủ dự án hoặc đặt GACARD_FIXTURE."
)
MISSING_BADGES = "Chưa có cung mệnh cho: {names}. Nhập tay bằng /cm (vd /cm {example} 2) rồi bấm Làm lại."
UNMATCHED_BADGES = "Bạn đã /cm cho {names} nhưng không thấy thẻ nhân vật đó trong ảnh nên bỏ qua."
NO_CARD_CHARS = "Chưa nhận diện được nhân vật nào trên thẻ nên chưa đè được cung mệnh."
BADGE_SUMMARY = "Cung mệnh: {enka} từ Enka, {manual} nhập tay."
BTN_CHANGE_BG = "Đổi nền"
BTN_REDO = "Làm lại"
BG_CYCLE_ONLY_ONE = "Kho chưa có nền khác của nhân vật trong acc để đổi. Thêm bằng /themnen <ten>."
CALLBACK_EXPIRED = "Phiên đã hết hạn, hãy gửi lại ảnh."
CALLBACK_UNKNOWN = "Nút này không còn dùng được."

UID_RENDERING = "Đang tra Enka và vẽ ảnh cho UID {uid}… (khoảng 10–30 giây)"
UID_FAIL = "Không vẽ được ảnh cho UID {uid}: {why}\nThử lại /uid sau ít phút, hoặc dùng chế độ ảnh chụp (/huongdan)."
UID_CM_HINT = "Nhân vật thiếu cung mệnh: nhập tay bằng /cm <ten> <so> rồi bấm Làm lại."
SAVED_COPY = "Đã lưu bản sao: output/{name}"

# ---------------------------------------------------------------- /ghep (HoYoLAB Sổ Tay Hành Trình)
GHEP_RENDERING = "Đang tra HoYoLAB và ghép ảnh…"
GHEP_OFF = "Chế độ HoYoLAB (/ghep) chưa bật trên bot này. Dùng /uid hoặc gửi ảnh chụp."
NEED_GHEP_ARGS = "Cú pháp: /ghep <UID> [ma <so>] [nen <ten>]. Ví dụ /ghep 887630784 ma 1116 nen columbina"
GHEP_FAIL = "Không vẽ được ảnh HoYoLAB cho UID {uid}: {why}\nThử lại /ghep sau ít phút, hoặc dùng /uid (Enka)."
GHEP_WAIT_VERIFY = "Đang chờ xác minh, xong sẽ gửi ảnh."
GHEP_VERIFY_STILL = "HoYoLAB vẫn đòi xác minh nên chưa vẽ được ảnh UID {uid}. Bạn thử lại /ghep sau ít phút nhé."
GHEP_VERIFY_NO_ADMIN = "HoYoLAB đang đòi xác minh mà bot chưa có admin để xác minh. Thử lại /ghep sau nhé."
VERIFY_ADMIN = (
    "HoYoLAB cần xác minh. Mở link này trên trình duyệt đã đăng nhập HoYoLAB của bạn, tự kéo captcha, "
    "xong bấm nút bên dưới."
)
VERIFY_ADMIN_FOR = "(Yêu cầu /ghep UID {uid} từ chat {chat_id}.)"
VERIFY_ADMIN_STILL = "Thử lại UID {uid}: HoYoLAB vẫn đòi xác minh; đã báo người dùng thử lại sau."
BTN_VERIFIED = "Đã xác minh"
VERIFY_RETRYING = "Đang thử lại…"
VERIFY_GONE = "Yêu cầu xác minh này đã xử lý hoặc hết hạn."

# Lệnh /ghep <tk>-<mk> là ngoại lệ DUY NHẤT được nhận tài khoản/mật khẩu
# (bot xoá tin ngay, mật khẩu chỉ sống trong một lần đăng nhập, xem handlers).
# Mọi tin khác giống tài khoản/mật khẩu vẫn bị chặn. Không lặp lại nội dung tin.
CREDENTIALS_REFUSED = (
    "Không gửi tài khoản/mật khẩu vào bot như tin thường. Nếu muốn bot tự đăng nhập rồi ghép, "
    "dùng đúng lệnh /ghep <tài khoản>-<mật khẩu> trong chat riêng (bot sẽ xoá tin ngay), "
    "hoặc dùng /ghep <UID> và nhờ chủ acc bật công khai Sổ Tay Hành Trình."
)
GHEP_LOGIN_OFF = "Chế độ đăng nhập (/ghep <tài khoản>-<mật khẩu>) chưa bật trên bot này (thiếu Playwright). Dùng /ghep <UID>."
GHEP_LOGIN_BAD = "Cú pháp: /ghep <tài khoản>-<mật khẩu> [ma <so>] [nen <ten>]. Ví dụ: /ghep ten@gmail.com-MatKhau123 ma 1116"
GHEP_LOGIN_RENDERING = "Đã nhận lệnh đăng nhập, đang mở HoYoLAB… (bot đã xoá tin chứa mật khẩu của bạn)"
GHEP_LOGIN_CAPTCHA_LOCAL = (
    "HoYoLAB cần xác minh captcha. Trình duyệt đã mở trên máy chạy bot — hãy tự kéo slider "
    "trong cửa sổ đó, bot sẽ tự tiếp tục sau khi bạn kéo xong (tối đa 5 phút)."
)
GHEP_LOGIN_CAPTCHA_WEB = (
    "HoYoLAB cần xác minh captcha. Mở link này và tự kéo slider trên ảnh (Telegram không kéo "
    "được slider, bot không tự kéo thay bạn): {link}"
)
GHEP_LOGIN_FAIL = "Đăng nhập rồi ghép thất bại: {why}\nThử lại /ghep <tài khoản>-<mật khẩu>, hoặc dùng /ghep <UID>."
GHEP_LOGIN_MULTI = (
    "Tài khoản này liên kết nhiều acc Genshin: {uids}. Gõ /ghep <UID muốn lấy> ma <so> "
    "để ghép acc đó (dùng cookie chủ bot), hoặc gửi lại lệnh đăng nhập rồi chọn UID khi bot hỏi."
)

ADMIN_ONLY = "Lệnh này chỉ admin dùng được."
NEED_MOI_ARGS = (
    "Cú pháp: /moi @username hoặc /moi <chat_id>. Ví dụ /moi @ten_ban hay /moi 123456789 "
    "(người đó gõ /start với bot sẽ thấy chat ID của mình)."
)
NEED_BOMOI_ARGS = "Cú pháp: /bomoi @username hoặc /bomoi <chat_id>. Xem danh sách bằng /danhsach."
BAD_USERNAME = (
    "Username '{raw}' không hợp lệ: Telegram chỉ cho 5–32 ký tự gồm chữ, số, gạch dưới (_), "
    "có thể có @ ở đầu. Ví dụ /moi @ten_ban"
)
MOI_DONE = "Đã mời chat {id}: từ giờ chat này dùng được bot."
MOI_ALREADY = "Chat {id} đã có quyền dùng bot rồi."
MOI_USER_DONE = (
    "Đã mời @{name}. Bot không tự tra được chat ID từ username, nên khi @{name} nhắn bot lần đầu "
    "mình sẽ ghi nhận chat ID và báo bạn."
)
MOI_USER_ALREADY = "@{name} đã có trong danh sách mời rồi."
BOMOI_DONE = "Đã bỏ quyền của chat {id}."
BOMOI_NOT_FOUND = "Chat {id} vốn không có trong danh sách."
BOMOI_ADMIN = "Chat {id} là admin (GACARD_ADMIN_CHAT_IDS), không bỏ quyền bằng lệnh được."
BOMOI_USER_DONE = "Đã bỏ lời mời @{name}."
BOMOI_USER_DONE_ID = "Đã bỏ lời mời @{name} và quyền của chat {id}."
BOMOI_USER_NOT_FOUND = "@{name} vốn không có trong danh sách mời."
USERNAME_RECOGNIZED = "Đã nhận diện @{name} (chat id {id}): từ giờ chat này dùng được bot."
DANHSACH_HEAD = "Chat được dùng bot ({n}):"
DANHSACH_USERS_HEAD = "Mời theo username ({n}):"
DANHSACH_USER_SEEN = "- @{name} → chat {id}"
DANHSACH_USER_UNSEEN = "- @{name} (chưa nhắn bot)"

UNKNOWN_COMMAND = "Mình không hiểu lệnh này. Gõ /help để xem danh sách lệnh."
TEXT_HINT = "Gửi ảnh chụp màn hình để bắt đầu, hoặc /help để xem các lệnh."

# ---------------------------------------------------------------- /help (/trogiup)
# (lệnh, ví dụ). Mọi lệnh ở đây phải có trong handlers.COMMANDS (test kiểm tra).
HELP_GROUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("Tạo ảnh", (
        ("/ghep", "/ghep 887630784 ma 1116 nen columbina — đọc đủ nhân vật từ HoYoLAB (chủ acc bật công khai Sổ Tay Hành Trình)"),
        ("/ghep", "/ghep <tài khoản>-<mật khẩu> ma 1116 — bot tự đăng nhập HoYoLAB rồi ghép (chỉ chat riêng, bot xoá tin ngay)"),
        ("/uid", "/uid 887630784 ma 1116 nen columbina — tra Enka và vẽ ảnh luôn"),
        ("/xong", "gửi 4–7 ảnh chụp (dạng File) rồi /xong — ghép ảnh"),
        ("/huy", "/huy — huỷ phiên, xoá ảnh đã gửi"),
        ("/trangthai", "/trangthai — xem phiên hiện tại có gì"),
    )),
    ("Thông tin cung mệnh và nền", (
        ("/ma", "/ma 1116 — đặt mã acc"),
        ("/cm", "/cm columbina 2 zibai 0 — nhập cung mệnh tay"),
        ("/nen", "/nen columbina — chọn nền theo nhân vật"),
        ("/themnen", "/themnen columbina — rồi gửi ảnh nền (File, từ 1920x1080)"),
        ("/dsnen", "/dsnen — xem kho nền và id"),
        ("/xoanen", "/xoanen bg12 — xoá nền theo id"),
        ("/uutien", "/uutien — bảng điểm hype (tham khảo; nền chọn ngẫu nhiên trong nhân vật của acc)"),
    )),
    ("Quản trị", (
        ("/moi", "/moi @ten_ban hoặc /moi 123456789 — cấp quyền dùng bot"),
        ("/bomoi", "/bomoi @ten_ban hoặc /bomoi 123456789 — bỏ quyền"),
        ("/danhsach", "/danhsach — xem chat ID và username được mời"),
    )),
    ("Khác", (
        ("/start", "/start — lời chào và cách dùng nhanh"),
        ("/huongdan", "/huongdan — 5 bước dùng bot"),
        ("/mau", "/mau — cách chụp ảnh"),
        ("/help", "/help hoặc /trogiup — danh sách này"),
    )),
)  # fmt: skip
HELP_ADMIN_GROUP = "Quản trị"
HELP_ADMIN_MARK = " (chỉ admin)"
HELP_HEAD = "Các lệnh của bot:"
HELP_NOT_ADMIN = "Bạn không phải admin nên không dùng được nhóm Quản trị."


def help_text(is_admin: bool) -> str:
    """Nội dung /help. Nhóm Quản trị luôn hiện nhưng mỗi dòng ghi "(chỉ admin)"."""
    lines = [HELP_HEAD]
    for title, items in HELP_GROUPS:
        admin_group = title == HELP_ADMIN_GROUP
        mark = HELP_ADMIN_MARK if admin_group else ""
        lines += ["", f"{title}{mark}:"]
        lines += [f"- {example}{mark}" for _cmd, example in items]
        if admin_group and not is_admin:
            lines.append(HELP_NOT_ADMIN)
    return "\n".join(lines)
