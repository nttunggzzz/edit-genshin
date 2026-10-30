"""Đăng nhập HoYoLAB bằng tài khoản/mật khẩu cho lệnh /ghep <tk>-<mk>.

Đây là NHÁNH RIÊNG, chỉ chạy khi người dùng đã được cấp quyền gửi đúng lệnh
``/ghep <tài khoản>-<mật khẩu>`` trong chat riêng. Nó KHÔNG thay thế chế độ
/ghep <UID> cũ (cookie chủ bot trong .env) và KHÔNG thay thế bất kỳ client API
nào cũ:

* Sau khi đăng nhập, module này chỉ lấy cookie từ trình duyệt rồi TRẢ LẠI cho
  code cũ dùng: ``HoyolabClient`` (Battle Chronicle) và ``wish.fetch_wish_history``
  (lịch sử roll). Không viết lại endpoint, không đổi tên trường, không đổi
  cách tính pity / khám phá bản đồ.
* Mật khẩu chỉ sống trong bộ nhớ của lần đăng nhập này: không ghi file, không
  ghi log, không đưa vào báo cáo, không lưu vào session của bot. Hết lần đăng
  nhập, tham chiếu bị hủy (``del``) ngay trong handler.
* Captcha là slider tương tác: code KHÔNG BAO GIỜ tự kéo slider. Gặp captcha
  thì code dừng và chờ người dùng tự kéo, theo một trong hai cách do cấu hình
  chọn (xem ``CaptchaMode``):
  - ``local``: trình duyệt Playwright mở NGAY TRÊN MÁY CHẠY BOT, người dùng
    kéo slider trực tiếp trong cửa sổ đó.
  - ``web``: bot mở một trang web tạm (xem ``gacard.login_web``) hiển thị ảnh
    chụp trình duyệt và chuyển thao tác kéo của người dùng về Playwright.
    Telegram không kéo slider trực tiếp được, nên không có chế độ "gửi ảnh
    captcha qua Telegram rồi xong".

Cần cài thêm (không bắt buộc cho chế độ /ghep <UID> cũ)::

    pip install -e ".[login]"
    playwright install chromium

Nếu Playwright chưa cài, module này chỉ ném ``LoginError("playwright_missing")``
khi được gọi, bot cũ vẫn chạy bình thường.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

HOYOLAB_HOME = "https://www.hoyolab.com"
BATTLE_CHRONICLE_URL = (
    "https://act.hoyolab.com/app/community-game-records-sea/index.html"
    "?bbs_presentation_style=fullscreen&v=350&gid=2&utm_source=hoyolab&utm_medium=tools"
    "&bbs_theme=light&bbs_theme_device=1#/ys"
)
# Endpoint lấy thẻ game đã liên kết, dùng để suy UID Genshin từ tài khoản vừa
# đăng nhập. Đây là endpoint đã ghi trong docs/acc-card/04-hoyolab-api.md:
# GET bbs-api-os.hoyolab.com/game_record/card/wapi/getGameRecordCard?uid=<ltuid>
# -> data.list[]: game_id, game_role_id, has_role, nickname, region, ...
CARD_HOST = "https://bbs-api-os.hoyolab.com"
CARD_PATH = "/game_record/card/wapi/getGameRecordCard"
GENSHIN_GAME_ID = 2

CAPTCHA_TEXTS = ("Trượt để hoàn thành", "Slide to complete", "Drag the slider")
LOGIN_TIMEOUT_MS = 60_000
CAPTCHA_TIMEOUT_SECONDS = 300.0
POLL_SECONDS = 1.0

MESSAGES = {
    "playwright_missing": (
        "Chưa cài Playwright nên chưa đăng nhập bằng tài khoản/mật khẩu được. "
        "Chạy: pip install -e \".[login]\" rồi: playwright install chromium"
    ),
    "login_failed": (
        "Đăng nhập HoYoLAB không thành công (sai tài khoản/mật khẩu, hoặc tài khoản "
        "cần xác minh thêm). Kiểm tra lại rồi thử lại."
    ),
    "too_many_requests": (
        "HoYoLAB đang giới hạn tạm thời (Too many requests). "
        "Đã dừng hàng đợi, chờ khách hàng quyết định."
    ),
    "captcha_timeout": (
        "Đã quá thời gian 300 giây chờ xác minh Captcha mà chưa hoàn thành. Vui lòng thử lại."
    ),
    "form_not_found": (
        "Không tìm thấy form đăng nhập sau 30 giây (HoYoLAB có thể đã đổi giao diện). "
        "Đã lưu ảnh chụp màn hình vào web-out/debug-login-failed.png để debug."
    ),
    "login_timeout_no_cookie": (
        "Đã quá 300 giây nhưng HoYoLAB không trả về cookie phiên và không xuất hiện Captcha. "
        "Đã lưu ảnh chụp màn hình vào web-out/debug-login-timeout.png để debug."
    ),
    "no_cookie": (
        "Đăng nhập xong nhưng trình duyệt không trả đủ cookie HoYoLAB "
        "(cần ltuid_v2 và ltoken_v2). Đăng nhập lại hoyolab.com trên trình duyệt "
        "rồi thử lại."
    ),
    "no_genshin_role": (
        "Tài khoản HoYoLAB này chưa liên kết nhân vật Genshin nào, hoặc Sổ Tay "
        "Hành Trình chưa bật công khai. Liên kết acc trong HoYoLAB rồi thử lại."
    ),
    "multiple_roles": "Tài khoản này liên kết nhiều acc Genshin, chưa biết lấy acc nào.",
    "card_error": "Không đọc được danh sách acc Genshin đã liên kết từ HoYoLAB.",
    "browser_error": "Không mở được trình duyệt đăng nhập HoYoLAB trên máy chạy bot.",
}


class LoginError(Exception):
    """Lỗi đăng nhập; `message_vi` hiển thị cho người dùng, không chứa mật khẩu."""

    def __init__(self, kind: str, message_vi: str | None = None):
        self.kind = kind
        self.message_vi = message_vi or MESSAGES.get(kind, "Đăng nhập HoYoLAB lỗi.")
        super().__init__(self.message_vi)


@dataclass
class LoginCookies:
    """Cookie lấy từ trình duyệt, chỉ dùng trong bộ nhớ của một lần /ghep.

    ``cookie``: chuỗi header Cookie cho HoyolabClient (cần ltuid_v2 + ltoken_v2).
    ``cookie_token``: chuỗi header Cookie cho wish.fetch_wish_history, hoặc None
    khi trình duyệt không trả cookie_token_v2 (khi đó ô banner sẽ bị bỏ qua kèm
    lý do, giống hành vi cũ khi thiếu GACARD_HOYOLAB_COOKIE_TOKEN).
    """

    cookie: str
    cookie_token: str | None = None
    ltuid: str | None = None

    def __repr__(self) -> str:  # không bao giờ lộ cookie trong repr/log
        return f"LoginCookies(cookie='***', cookie_token={'***' if self.cookie_token else None})"


@dataclass
class LoginResult:
    cookies: LoginCookies
    uids: list[str] = field(default_factory=list)  # UID Genshin đã liên kết (game_role_id)


# ------------------------------------------------------------------ cookie thuần
MAIN_COOKIE_NAMES = ("ltuid_v2", "ltoken_v2")
TOKEN_COOKIE_NAMES = ("account_id_v2", "cookie_token_v2", "account_mid_v2")


def build_cookie_headers(cookies: list[dict[str, Any]]) -> LoginCookies:
    """Danh sách cookie Playwright (dict name/value) -> LoginCookies.

    Chỉ nhặt đúng tên cần thiết, không giữ nguyên cả jar: Battle Chronicle cần
    ltuid_v2 + ltoken_v2; lịch sử roll cần cookie_token_v2 (+ account_id_v2).
    Thiếu ltuid/ltoken -> LoginError("no_cookie"). Thiếu cookie_token_v2 thì
    cookie_token=None (không phải lỗi: ô banner chỉ bị bỏ qua).
    """
    by_name: dict[str, str] = {}
    for c in cookies or []:
        name = str(c.get("name") or "")
        value = str(c.get("value") or "")
        if name and value:
            by_name[name] = value
    if not by_name.get("ltuid_v2") or not by_name.get("ltoken_v2"):
        raise LoginError("no_cookie")
    main = "; ".join(f"{n}={by_name[n]}" for n in MAIN_COOKIE_NAMES if n in by_name)
    token_parts = [f"{n}={by_name[n]}" for n in TOKEN_COOKIE_NAMES if n in by_name]
    token = "; ".join(token_parts) if "cookie_token_v2" in by_name else None
    return LoginCookies(cookie=main, cookie_token=token, ltuid=by_name.get("ltuid_v2"))


def parse_game_card(data: dict) -> list[str]:
    """data của getGameRecordCard -> UID Genshin đã liên kết, giữ thứ tự API trả.

    Chỉ lấy game_id == 2 và has_role đúng; các game khác (HSR/ZZZ) không đụng
    tới, tránh xung đột với dữ liệu Genshin của tool ảnh acc.
    """
    out: list[str] = []
    raw = data.get("list") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            game_id = int(item.get("game_id"))
        except (TypeError, ValueError):
            continue
        role_id = str(item.get("game_role_id") or "").strip()
        if game_id == GENSHIN_GAME_ID and item.get("has_role") and re.fullmatch(r"\d{9,10}", role_id):
            if role_id not in out:
                out.append(role_id)
    return out


def discover_uids(cookies: LoginCookies, transport: Callable | None = None) -> list[str]:
    """ltuid -> UID Genshin qua getGameRecordCard, dùng đúng header DS của hoyolab.py.

    Không dùng transport lạ: mặc định dùng ``hoyolab.urllib_transport`` (đã có
    trong code cũ) để không lệch hành vi mạng / timeout với client cũ.
    """
    from gacard import hoyolab

    if not cookies.ltuid:
        raise LoginError("no_cookie")
    headers = hoyolab.build_headers(cookies.cookie, "vi-vn")
    url = f"{CARD_HOST}{CARD_PATH}?uid={cookies.ltuid}"
    send = transport or hoyolab.urllib_transport
    try:
        status, raw = send("GET", url, headers, None, 15.0)
    except LoginError:
        raise
    except Exception:
        raise LoginError("card_error") from None
    if status != 200:
        raise LoginError("card_error")
    try:
        import json

        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise LoginError("card_error") from None
    if not isinstance(doc, dict) or doc.get("retcode") != 0 or not isinstance(doc.get("data"), dict):
        raise LoginError("card_error")
    return parse_game_card(doc["data"])


# ------------------------------------------------------------------ Playwright
def _signed_in(page: Any) -> bool:
    """Đã đăng nhập chưa: có cookie ltuid_v2/ltoken_v2 là đủ (không đoán theo DOM)."""
    try:
        names = {str(c.get("name") or "") for c in page.context.cookies()}
    except Exception:
        return False
    return "ltuid_v2" in names and "ltoken_v2" in names


def _captcha_visible(page: Any) -> bool:
    """Slider có hiện không: kiểm tra CẢ khung chính lẫn các iframe với timeout cho từng frame.
    Tuyệt đối không dùng frame.content() vì gây treo vô hạn khi frame đang chuyển hướng."""
    CAPTCHA_SELECTORS = (
        '.geetest_slider',
        '.geetest_radar',
        '.geetest_popup_wrap',
        '.geetest_holder',
        '.geetest_text_tips',
        '[class*="geetest"]',
        '[class*="captcha"]',
        'div[class*="puzzle"]',
    )
    CAPTCHA_TEXT_PATTERNS = (
        "Trượt để hoàn thành",
        "Slide to complete",
        "Drag the slider",
        "Hoàn thành câu đố",
        "Slide to complete the puzzle",
    )

    frames = [page] + list(getattr(page, "frames", []) or [])
    for frame in frames:
        # 1. Thử tìm nhanh qua selector CSS
        for sel in CAPTCHA_SELECTORS:
            try:
                loc = frame.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    return True
            except Exception:
                pass

        # 2. Thử tìm nhanh qua text locator
        for text in CAPTCHA_TEXT_PATTERNS:
            try:
                t_loc = frame.get_by_text(text).first
                if t_loc.count() > 0 and t_loc.is_visible():
                    return True
            except Exception:
                pass

        # 3. Đọc text body qua evaluate với timeout 1000ms (an toàn, không bao giờ treo)
        try:
            body_text = frame.evaluate(
                "() => { try { return document.body ? document.body.innerText : ''; } catch(e) { return ''; } }",
                timeout=1000
            )
            if body_text and any(t in body_text for t in CAPTCHA_TEXT_PATTERNS):
                return True
        except Exception:
            pass

    return False


def _detect_login_error(page: Any) -> str | None:
    """Quét các frame để tìm dòng báo lỗi đăng nhập (Vant toast, Element Plus error, v.v.)."""
    ERROR_SELECTORS = (
        '.van-toast__text',
        '.van-toast',
        '.el-form-item__error',
        '.el-message__content',
        '[class*="toast"]',
        '[class*="error-text"]',
        '[role="alert"]',
    )
    ERROR_KEYWORDS = (
        "invalid", "incorrect", "error", "không chính xác", "không hợp lệ",
        "thất bại", "không tồn tại", "lỗi", "too many", "quá nhiều", "not exist"
    )
    frames = [page] + list(getattr(page, "frames", []) or [])
    for frame in frames:
        for sel in ERROR_SELECTORS:
            try:
                locs = frame.locator(sel)
                count = locs.count()
                for i in range(count):
                    el = locs.nth(i)
                    if el.is_visible():
                        txt = el.inner_text().strip()
                        if txt and len(txt) < 150:
                            lower = txt.lower()
                            if any(k in lower for k in ERROR_KEYWORDS):
                                return txt
            except Exception:
                continue
    return None


def _form_present(frame: Any) -> bool:
    """Khung đăng nhập đã hiện trong frame này chưa (có ô user + ô password)."""
    try:
        for sel in (
            'input[placeholder*="Tên Người Dùng"]',
            'input[placeholder*="Email"]',
            'input[name="username"]',
        ):
            if frame.locator(sel).first.count() > 0 and frame.locator(sel).first.is_visible():
                return frame.locator('input[type="password"]').first.count() > 0
    except Exception:
        return False
    return False


def _click_login_trigger(page: Any, on_log: Callable[[str], None] | None = None) -> bool:
    """Bấm nút mở khung đăng nhập trên trang chủ HoYoLAB với timeout 5000ms."""
    # 1. Đóng cookie banner nếu có
    for cookie_sel in ('button:has-text("Accept")', 'button:has-text("Chấp nhận")', 'button:has-text("OK")', '.cookie-btn', '[class*="cookie"] button'):
        try:
            c = page.locator(cookie_sel).first
            if c.count() > 0 and c.is_visible():
                c.click(timeout=1000, force=True)
        except Exception:
            pass

    # 2. Thử các nút mở đăng nhập
    candidates = []
    for label in ("Đăng Nhập", "Log In", "Login", "Sign In"):
        try:
            candidates.append((label, page.get_by_role("button", name=label).first))
            candidates.append((label, page.locator(f'button:has-text("{label}")').first))
            candidates.append((label, page.locator(f'a:has-text("{label}")').first))
            candidates.append((label, page.get_by_text(label, exact=True).first))
        except Exception:
            continue
    for label, loc in candidates:
        try:
            if loc.count() > 0 and loc.is_visible():
                if on_log:
                    on_log(f"Tìm thấy nút mở đăng nhập '{label}', đang bấm...")
                try:
                    loc.click(timeout=2000)
                except Exception:
                    loc.click(timeout=2000, force=True)
                page.wait_for_timeout(2000)
                return True
        except Exception:
            continue
    return False


def _bring_page_to_front(page: Any, on_log: Callable[[str], None] | None = None) -> bool:
    """Đưa cửa sổ trình duyệt Chromium lên trước màn hình và ghi log rõ ràng kết quả."""
    try:
        page.bring_to_front()
        msg = "Đã đưa cửa sổ trình duyệt Chromium lên trước màn hình thành công."
        log.info(msg)
        if on_log:
            on_log(msg)
        return True
    except Exception as exc:
        msg = f"Không thể đưa cửa sổ trình duyệt Chromium lên trước ({type(exc).__name__}: {exc})."
        log.warning(msg)
        if on_log:
            on_log(msg)
        return False


def login_with_password(
    username: str,
    password: str,
    *,
    headless: bool = False,
    captcha_mode: str = "local",
    captcha_url_base: str = "",
    captcha_port: int = 8765,
    on_captcha: Callable[[str], None] | None = None,
    on_stage: Callable[[str, str], None] | None = None,
    timeout_seconds: float = CAPTCHA_TIMEOUT_SECONDS,
    check_action_requests: Callable[[], dict[str, bool]] | None = None,
) -> LoginCookies:
    """Đăng nhập đồng bộ (chạy bằng asyncio.to_thread từ bot).

    Luồng: mở hoyolab.com -> nếu đã đăng nhập thì xong -> nếu chưa, điền form
    -> nếu xuất hiện slider captcha thì DỪNG và chờ người dùng tự kéo (không
    tự kéo), tối đa ``timeout_seconds`` -> kiểm tra cookie -> trả LoginCookies.

    ``on_captcha``: callback bot dùng để nhắn link/web cho người dùng (chế độ
    web) hoặc nhắn "hãy kéo slider trên máy chạy bot" (chế độ local). Callback
    chỉ nhận link/văn bản, không nhận mật khẩu.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise LoginError("playwright_missing") from None

    if captcha_mode not in ("local", "web"):
        captcha_mode = "local"
    try:
        if on_stage:
            on_stage("logging_in", "Khởi động trình duyệt Playwright...")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="vi-VN",
                timezone_id="Asia/Ho_Chi_Minh",
            )
            page = context.new_page()
            web_server = None

            def _stage_log(msg: str) -> None:
                if on_stage:
                    on_stage("logging_in", msg)

            try:
                if on_stage:
                    on_stage("logging_in", "Trình duyệt đã mở, đang kết nối máy chủ HoYoLAB...")
                page.goto(HOYOLAB_HOME, timeout=LOGIN_TIMEOUT_MS)
                page.wait_for_timeout(2000)
                _bring_page_to_front(page, on_log=_stage_log)
                if not _signed_in(page):
                    if on_stage:
                        on_stage("logging_in", "Đang tìm và điền form đăng nhập HoYoLAB...")
                    filled = _fill_login_form_sync(page, username, password, on_log=_stage_log)
                    if not filled:
                        raise LoginError("login_failed")
                    page.wait_for_timeout(2000)
                deadline = time.monotonic() + timeout_seconds
                captcha_announced = False
                captcha_start_time = 0.0
                last_bring_front_time = 0.0
                last_log_time = 0.0
                try:
                    while True:
                        now = time.monotonic()
                        remaining = max(0, int(deadline - now))
                        signed = _signed_in(page)
                        has_captcha = _captcha_visible(page)

                        # Kiểm tra cờ yêu cầu từ API web (được xử lý an toàn trong chính luồng Playwright này)
                        if check_action_requests:
                            actions = check_action_requests()
                            if actions.get("bring_to_front"):
                                log.info("Luồng Playwright nhận cờ yêu cầu đưa cửa sổ Chromium lên trước màn hình.")
                                _bring_page_to_front(page, on_log=_stage_log)
                            if actions.get("fallback_web"):
                                log.info("Luồng Playwright nhận cờ yêu cầu mở trang web kéo Captcha dự phòng.")
                                if web_server is None:
                                    try:
                                        from gacard.login_web import CaptchaWebServer
                                        web_server = CaptchaWebServer(page, port=captcha_port)
                                        link = web_server.start(base_url=captcha_url_base)
                                        web_server.tick()
                                        if on_captcha is not None:
                                            on_captcha(link)
                                        if on_stage:
                                            on_stage("waiting_captcha", f"Đã mở trang web kéo Captcha dự phòng tại cổng {captcha_port}.")
                                        log.info("Đã mở CaptchaWebServer dự phòng tại cổng %d từ cờ yêu cầu thành công.", captcha_port)
                                    except Exception as e:
                                        log.warning("Không thể mở CaptchaWebServer dự phòng từ cờ yêu cầu: %s", e)

                        # 0. Kiểm tra nếu HoYoLAB hiển thị toast/alert báo lỗi đăng nhập (sai pass, sai format, too many...) -> dừng ngay
                        login_err = _detect_login_error(page)
                        if login_err:
                            is_too_many = any(k in login_err.lower() for k in ("too many", "quá nhiều", "quá giới hạn"))
                            if is_too_many:
                                if on_stage:
                                    on_stage("error", f"HoYoLAB đang giới hạn tạm thời: {login_err}")
                                raise LoginError("too_many_requests", f"HoYoLAB đang giới hạn tạm thời ({login_err}). Đã dừng hàng đợi, chờ khách hàng quyết định.")
                            if on_stage:
                                on_stage("error", f"Đăng nhập thất bại: {login_err}")
                            raise LoginError("login_failed", f"HoYoLAB báo: {login_err}")

                        # Log định kỳ mỗi 3 giây
                        if now - last_log_time >= 3.0:
                            last_log_time = now
                            status_text = (
                                f"Kiểm tra phản hồi HoYoLAB: đã có cookie={signed}, captcha hiện={has_captcha}, "
                                f"còn lại {remaining}s / {int(timeout_seconds)}s"
                            )
                            if on_stage:
                                stage_name = "waiting_captcha" if has_captcha else "logging_in"
                                on_stage(stage_name, status_text)

                        # 1. Đã có cookie ltuid_v2 và ltoken_v2 thật sự và không còn captcha
                        if signed and not has_captcha:
                            if on_stage:
                                on_stage("fetching_data", "Đã xác nhận phiên đăng nhập! Đang kết nối Sổ Tay Hành Trình để lấy cookie_token_v2...")
                            try:
                                from gacard.hoyolab import BATTLE_CHRONICLE_URL
                                page.goto(BATTLE_CHRONICLE_URL, timeout=15000)
                                page.wait_for_timeout(2000)
                            except Exception:
                                pass
                            break

                        # 2. Phát hiện captcha
                        if has_captcha:
                            if not captcha_announced:
                                captcha_announced = True
                                captcha_start_time = now
                                last_bring_front_time = now
                                if captcha_mode == "web":
                                    if on_stage:
                                        on_stage("waiting_captcha", f"Phát hiện Captcha trượt! Đang mở trang web kéo Captcha (còn {remaining}s)...")
                                    from gacard.login_web import CaptchaWebServer

                                    web_server = CaptchaWebServer(page, port=captcha_port)
                                    link = web_server.start(base_url=captcha_url_base)
                                    web_server.tick()
                                    if on_captcha is not None:
                                        on_captcha(link)
                                else:
                                    front_ok = _bring_page_to_front(page, on_log=_stage_log)
                                    if front_ok:
                                        if on_stage:
                                            on_stage("waiting_captcha", f"Phát hiện Captcha trượt! Cửa sổ Chromium đã hiện lên trước (còn {remaining}s)...")
                                        if on_captcha is not None and web_server is None:
                                            on_captcha("")
                                    else:
                                        # Cửa sổ local không đưa lên trước được -> tự động chuyển sang trang kéo dự phòng ngay lập tức!
                                        if on_stage:
                                            on_stage("waiting_captcha", f"Không thể đưa cửa sổ Chromium lên trước! Tự động mở trang kéo dự phòng (còn {remaining}s)...")
                                        try:
                                            from gacard.login_web import CaptchaWebServer
                                            web_server = CaptchaWebServer(page, port=captcha_port)
                                            link = web_server.start(base_url=captcha_url_base)
                                            web_server.tick()
                                            if on_captcha is not None:
                                                on_captcha(link)
                                            log.info("Đã tự động chuyển sang CaptchaWebServer dự phòng tại cổng %d do không đưa được cửa sổ lên trước.", captcha_port)
                                        except Exception as err:
                                            log.warning("Không thể mở CaptchaWebServer dự phòng: %s", err)

                            if now > deadline:
                                raise LoginError("captcha_timeout")

                            # Trong lúc chờ captcha ở chế độ local:
                            if captcha_mode == "local":
                                # 1. Gọi đưa cửa sổ lên trước lặp lại mỗi 5 giây
                                if now - last_bring_front_time >= 5.0:
                                    last_bring_front_time = now
                                    _bring_page_to_front(page, on_log=_stage_log)

                                # 2. Nếu sau 15 giây chờ kéo mà web_server chưa bật, tự động mở server dự phòng ở port 8766
                                if web_server is None and (now - captcha_start_time >= 15.0):
                                    try:
                                        from gacard.login_web import CaptchaWebServer
                                        web_server = CaptchaWebServer(page, port=captcha_port)
                                        link = web_server.start(base_url=captcha_url_base)
                                        web_server.tick()
                                        if on_captcha is not None:
                                            on_captcha(link)
                                        if on_stage:
                                            on_stage("waiting_captcha", f"Đã mở thêm trang kéo Captcha dự phòng tại cổng {captcha_port}...")
                                        log.info("Đã tự động mở CaptchaWebServer dự phòng tại cổng %d sau 15s chờ local.", captcha_port)
                                    except Exception as e:
                                        log.warning("Không thể mở CaptchaWebServer dự phòng: %s", e)

                            if web_server is not None:
                                web_server.tick()
                                page.wait_for_timeout(800)
                            else:
                                page.wait_for_timeout(1000)
                            continue

                        # 3. Hết thời gian mà vẫn chưa có cookie và chưa thấy captcha
                        if now > deadline:
                            for out_dir in (Path("web-out"), Path("data/acc-card/web-out")):
                                try:
                                    out_dir.mkdir(parents=True, exist_ok=True)
                                    page.screenshot(path=str(out_dir / "debug-login-timeout.png"), timeout=5000)
                                    break
                                except Exception:
                                    pass
                            raise LoginError("login_timeout_no_cookie")

                        page.wait_for_timeout(1000)

                    cookies = build_cookie_headers(context.cookies())
                    return cookies
                finally:
                    if web_server is not None:
                        web_server.stop()
            finally:
                context.close()
                browser.close()
    except LoginError:
        raise
    except Exception as exc:
        log.info("Lỗi trình duyệt đăng nhập: %s", type(exc).__name__)
        raise LoginError("browser_error") from None


def _fill_login_form_sync(
    page: Any,
    username: str,
    password: str,
    on_log: Callable[[str], None] | None = None
) -> bool:
    """Điền + bấm Đăng Nhập. Timeout 5000ms cho mọi thao tác locator, fill, click.
    Log rõ từng selector tìm thấy hoặc không thấy.
    Nếu sau 30 giây vẫn không tìm thấy form thì hủy job với lỗi rõ ràng và lưu ảnh chụp debug vào web-out.
    """
    start_time = time.monotonic()
    deadline = start_time + 30.0

    USER_SELECTORS = (
        'input[placeholder*="Tên Người Dùng"]',
        'input[placeholder*="Username"]',
        'input[placeholder*="Email"]',
        'input[name="username"]',
        'input[name="account"]',
    )
    PASS_SELECTOR = 'input[type="password"]'
    SUBMIT_SELECTORS = (
        'button.cmn-button[type="submit"]',
        'button[type="submit"]:has-text("Log In")',
        'button[type="submit"]:has-text("Đăng Nhập")',
        'button[type="submit"]',
        'button:has-text("Log In")',
        'button:has-text("Đăng Nhập")',
        'button:has-text("Login")',
        'button:has-text("Sign In")',
    )

    iteration = 0
    while time.monotonic() < deadline:
        iteration += 1
        elapsed = int(time.monotonic() - start_time)
        frames = [page] + list(getattr(page, "frames", []) or [])

        found_user = None
        found_pass = None
        target_frame = None

        for idx, frame in enumerate(frames):
            for sel in USER_SELECTORS:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        found_user = (sel, loc)
                        target_frame = frame
                        if on_log:
                            on_log(f"[{elapsed}s] Đã tìm thấy selector user: '{sel}'")
                        break
                    else:
                        if on_log and iteration == 1:
                            on_log(f"[{elapsed}s] Không thấy selector user: '{sel}'")
                except Exception:
                    continue

            if found_user is not None:
                try:
                    p_loc = frame.locator(PASS_SELECTOR).first
                    if p_loc.count() > 0 and p_loc.is_visible():
                        found_pass = p_loc
                        if on_log:
                            on_log(f"[{elapsed}s] Đã tìm thấy selector password: '{PASS_SELECTOR}'")
                        break
                    else:
                        if on_log:
                            on_log(f"[{elapsed}s] Không thấy selector password trong frame chứa user")
                except Exception:
                    pass

        # Nếu đã tìm thấy cả user và pass:
        if found_user is not None and found_pass is not None and target_frame is not None:
            sel_name, u_loc = found_user
            if on_log:
                on_log(f"[{elapsed}s] Đang điền tài khoản vào selector '{sel_name}'...")
            u_loc.fill(username, timeout=5000)
            if on_log:
                on_log(f"[{elapsed}s] Đang điền mật khẩu...")
            found_pass.fill(password, timeout=5000)
            page.wait_for_timeout(500)

            # Bấm nút Đăng Nhập
            for b_sel in SUBMIT_SELECTORS:
                try:
                    btn = target_frame.locator(b_sel).first
                    if btn.count() > 0 and btn.is_visible():
                        if on_log:
                            on_log(f"[{elapsed}s] Đã tìm thấy nút submit '{b_sel}', đang bấm...")
                        btn.click(timeout=5000)
                        if on_log:
                            on_log(f"[{elapsed}s] Đã gửi form, đang chờ HoYoLAB xác nhận...")
                        return True
                except Exception as e:
                    if on_log:
                        on_log(f"[{elapsed}s] Lỗi khi bấm nút submit '{b_sel}': {type(e).__name__}")
                    continue

            # Fallback nhấn Enter trên ô password
            try:
                if on_log:
                    on_log(f"[{elapsed}s] Thử nhấn Enter trên ô mật khẩu...")
                found_pass.press("Enter", timeout=5000)
                if on_log:
                    on_log(f"[{elapsed}s] Đã nhấn Enter gửi form, đang chờ HoYoLAB xác nhận...")
                return True
            except Exception:
                pass

        # Nếu chưa tìm thấy form, thử bấm nút Đăng Nhập trên trang chủ
        if iteration % 2 == 1:
            if on_log:
                on_log(f"[{elapsed}s] Chưa thấy form đăng nhập, thử tìm và bấm nút Đăng Nhập trên trang chủ...")
            _click_login_trigger(page, on_log=on_log)

        page.wait_for_timeout(1000)

    # Quá 30 giây không thấy form: lưu ảnh debug và ném LoginError
    for out_dir in (Path("web-out"), Path("data/acc-card/web-out")):
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            shot_file = out_dir / "debug-login-failed.png"
            page.screenshot(path=str(shot_file), timeout=5000)
            if on_log:
                on_log(f"Đã lưu ảnh debug màn hình vào: {shot_file}")
            break
        except Exception:
            pass

    if on_log:
        on_log("Không tìm thấy form đăng nhập HoYoLAB sau 30 giây. Hủy job.")
    raise LoginError("form_not_found")
