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
    "captcha_timeout": (
        "Đã quá thời gian 300 giây chờ xác minh Captcha mà chưa hoàn thành. Vui lòng thử lại."
    ),
    "form_not_found": (
        "Không tìm thấy form đăng nhập sau 30 giây (HoYoLAB có thể đã đổi giao diện). "
        "Đã lưu ảnh chụp màn hình vào web-out/debug-login-failed.png để debug."
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
    """Slider có hiện không: kiểm tra CẢ khung chính lẫn các iframe.

    Lỗi cũ ở đây: chỉ đọc ``page.content()`` (khung chính) nên slider nằm
    trong iframe đăng nhập của HoYoverse sẽ không bao giờ được phát hiện,
    bot cứ chờ im lặng mà không nhắn gì cho người dùng.
    """
    frames = [page] + list(getattr(page, "frames", []) or [])
    for frame in frames:
        try:
            body = frame.content()
        except Exception:
            continue
        if any(t in body for t in CAPTCHA_TEXTS):
            return True
    return False


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
    candidates = []
    for label in ("Đăng Nhập", "Log In", "Login", "Sign In"):
        try:
            candidates.append((label, page.get_by_role("button", name=label).first))
            candidates.append((label, page.get_by_text(label, exact=True).first))
        except Exception:
            continue
    for label, loc in candidates:
        try:
            if loc.count() > 0 and loc.is_visible():
                if on_log:
                    on_log(f"Tìm thấy nút mở đăng nhập '{label}', đang bấm...")
                loc.click(timeout=5000)
                page.wait_for_timeout(2000)
                return True
        except Exception as e:
            if on_log:
                on_log(f"Lỗi khi bấm nút '{label}': {type(e).__name__}")
            continue
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
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            page = context.new_page()
            try:
                if on_stage:
                    on_stage("logging_in", "Trình duyệt đã mở, đang kết nối máy chủ HoYoLAB...")
                page.goto(HOYOLAB_HOME, timeout=LOGIN_TIMEOUT_MS)
                page.wait_for_timeout(2000)
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                if not _signed_in(page):
                    if on_stage:
                        on_stage("logging_in", "Đang tìm và điền form đăng nhập HoYoLAB...")
                    def _stage_log(msg: str) -> None:
                        if on_stage:
                            on_stage("logging_in", msg)
                    filled = _fill_login_form_sync(page, username, password, on_log=_stage_log)
                    if not filled:
                        raise LoginError("login_failed")
                    page.wait_for_timeout(2000)
                deadline = time.monotonic() + timeout_seconds
                captcha_announced = False
                web_server = None
                try:
                    while True:
                        if _signed_in(page) and not _captcha_visible(page):
                            if on_stage:
                                on_stage("fetching_data", "Đăng nhập thành công! Đang lấy cookie phiên...")
                            break
                        if _captcha_visible(page):
                            if not captcha_announced:
                                captcha_announced = True
                                if on_stage:
                                    on_stage("waiting_captcha", "Phát hiện captcha trượt! Đang mở trang web kéo captcha...")
                                if captcha_mode == "web":
                                    from gacard.login_web import CaptchaWebServer

                                    web_server = CaptchaWebServer(page, port=captcha_port)
                                    link = web_server.start(base_url=captcha_url_base)
                                    web_server.tick()
                                    if on_captcha is not None:
                                        on_captcha(link)
                                elif on_captcha is not None:
                                    on_captcha("")
                            if time.monotonic() > deadline:
                                raise LoginError("captcha_timeout")
                            if web_server is not None:
                                web_server.tick()
                                page.wait_for_timeout(800)
                            else:
                                page.wait_for_timeout(int(POLL_SECONDS * 1000))
                            continue
                        if time.monotonic() > deadline:
                            raise LoginError("login_failed")
                        if web_server is not None:
                            web_server.tick()
                            page.wait_for_timeout(800)
                        else:
                            page.wait_for_timeout(int(POLL_SECONDS * 1000))
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
        'button[type="submit"]',
        'button:has-text("Đăng Nhập")',
        'button:has-text("Log In")',
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

            # Bấm nút Đăng Nhập
            for b_sel in SUBMIT_SELECTORS:
                try:
                    btn = target_frame.locator(b_sel).first
                    if btn.count() > 0 and btn.is_visible():
                        if on_log:
                            on_log(f"[{elapsed}s] Đã tìm thấy nút submit '{b_sel}', đang bấm...")
                        btn.click(timeout=5000)
                        if on_log:
                            on_log(f"[{elapsed}s] Đã gửi thông tin đăng nhập thành công!")
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
                    on_log(f"[{elapsed}s] Đã nhấn Enter gửi form!")
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
