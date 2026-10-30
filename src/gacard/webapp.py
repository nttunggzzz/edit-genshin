"""Máy chủ Web Local cho công cụ Edit Genshin (gacard).

Chạy độc lập với Telegram Bot, dùng chung toàn bộ pipeline cũ:
- RealLoginCard: đăng nhập HoYoLAB + cào Battle Chronicle + lịch sử roll.
- BackgroundLibrary: quản lý và chọn hình nền theo nhân vật trong acc.
- render_sheet: vẽ ảnh thẻ JPEG 1920×1080.
- Captcha Web: chạy ở cổng 8766 khi HoYoLAB yêu cầu kéo slider.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import time
import uuid
import zipfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web
from PIL import Image

from gacard import backgrounds as bg_mod, characters as char_mod, hoyolab, render, theme, wish as wish_mod
from gacard.bot.app import RealLoginCard, UserFacingError
from gacard.bot.config import Config
from gacard.bot.services import characters_in_account
from gacard.hoyolab_login import LoginError, discover_uids, login_with_password
from gacard.models import RenderOptions
from gacard.uidmode import IconCache
from gacard.web_xlsx import CODE_RE, parse_batch_rows

log = logging.getLogger("gacard.webapp")

DEFAULT_PORT = 8001
CAPTCHA_PORT = 8766
FORBIDDEN_PORTS = {5500, 7850}

TEMPLATE_DIR = Path(__file__).resolve().parent / "web_templates"


# ------------------------------------------------------------------ Cấu hình & Settings


def get_data_dir() -> Path:
    env_dir = os.environ.get("GACARD_DATA_DIR")
    if env_dir:
        return Path(env_dir).resolve()
    return Path("data/acc-card").resolve()


def get_settings_path(data_dir: Path) -> Path:
    return data_dir / "web-settings.json"


def load_web_settings(data_dir: Path) -> dict[str, Any]:
    path = get_settings_path(data_dir)
    defaults = {
        "frame_color": "",
        "height": 1080,
        "watermark": "",
        "default_bg_mode": "auto",
        "default_bg_character": "",
    }
    if not path.is_file():
        return defaults
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            defaults.update(data)
    except Exception:
        pass
    return defaults


def save_web_settings(data_dir: Path, settings: dict[str, Any]) -> None:
    path = get_settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ Quản lý Job


def log_terminal(job_id: str, message: str) -> None:
    now_str = datetime.now().strftime("%H:%M:%S")
    clean_id = job_id[:8] if job_id else "webapp"
    line = f"[{now_str}] [Job {clean_id}] {message}"
    try:
        print(line, flush=True)
    except Exception:
        try:
            print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)
        except Exception:
            pass
    try:
        with open("debug-log.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


@dataclass
class SingleJob:
    id: str
    code: str
    username: str
    password: str = field(repr=False)
    bg_mode: str = "auto"
    bg_character: str = ""
    bg_id: str = ""
    frame_color: str | None = None
    height: int = 1080
    watermark: str = ""
    status: str = "logging_in"  # logging_in, waiting_captcha, fetching_data, rendering, done, error
    stage_message: str = "Trình duyệt đang khởi động và kết nối HoYoLAB..."
    captcha_link: str | None = None
    captcha_deadline: float | None = None
    sheet: Any = None
    report: Any = None
    uid: str | None = None
    work_dir: Path | None = None
    chars_in_account: list[str] = field(default_factory=list)
    current_bg_id: str | None = None
    image_filename: str | None = None
    image_url: str | None = None
    download_name: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    req_bring_to_front: bool = False
    req_fallback_web: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class BatchJob:
    id: str
    status: str = "queued"  # queued, running, done, stopped
    total: int = 0
    success_count: int = 0
    error_count: int = 0
    current_index: int = 0
    rows: list[dict[str, Any]] = field(default_factory=list)
    successful_images: list[tuple[str, Path]] = field(default_factory=list)  # (filename, path)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class WebState:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.out_dir = data_dir / "web-out"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.bg_lib = bg_mod.BackgroundLibrary(data_dir / "backgrounds")
        self.characters = char_mod
        self.single_jobs: dict[str, SingleJob] = {}
        self.batch_jobs: dict[str, BatchJob] = {}
        self.worker_lock = asyncio.Lock()  # Đảm bảo tuần tự cho async
        self.thread_lock = threading.Lock()  # Đảm bảo tuần tự cho luồng Playwright
        self.thumb_cache: dict[str, bytes] = {}
        self.last_login_time: float | None = None
        self.current_login_time: float | None = None
        self.login_history: list[float] = []

    def record_login_start(self) -> None:
        now = time.time()
        self.last_login_time = self.current_login_time
        self.current_login_time = now
        self.login_history.append(now)

    def get_pacing_stats(self) -> dict[str, Any]:
        now = time.time()
        recent_1h = sum(1 for t in self.login_history if now - t <= 3600)
        return {
            "last_login_time": self.last_login_time,
            "current_login_time": self.current_login_time,
            "accs_last_1h": recent_1h,
        }

    def get_thumb_bytes(self, bg_id: str) -> bytes | None:
        if bg_id in self.thumb_cache:
            return self.thumb_cache[bg_id]
        try:
            bg = self.bg_lib.get(bg_id)
            img = self.bg_lib.open_image(bg)
            img.thumbnail((320, 180), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=80)
            data = buf.getvalue()
            self.thumb_cache[bg_id] = data
            return data
        except Exception:
            return None


# ------------------------------------------------------------------ Logic Chọn Nền Theo Luật Cũ


def pick_background_for_acc(
    bg_lib: bg_mod.BackgroundLibrary,
    chars_svc: Any,
    chars_in_account: list[str],
    bg_mode: str,
    bg_character: str = "",
    bg_id: str = "",
    exclude_id: str | None = None,
    rng: random.Random | None = None,
) -> tuple[str | None, str]:
    """Chọn nền đúng luật của dự án:
    1. Chọn cụ thể theo ID: lấy đúng ID đó.
    2. Chọn theo tên nhân vật: lấy ngẫu nhiên 1 nền của nhân vật đó. Nếu kho chưa có thì rơi về tự động.
    3. Tự động (mặc định): CHỈ CHỌN TRONG POOL NHÂN VẬT CỦA ACC (chars_in_account). Tuyệt đối không chọn ngoài acc.
    4. Reroll (Đổi nền): loại trừ exclude_id, chọn nền khác trong pool của acc.
    """
    rng = rng or random.Random()
    exclude = [exclude_id] if exclude_id else []

    # 1. Chọn ID cụ thể
    if bg_mode == "specific" and bg_id:
        try:
            bg_lib.get(bg_id)
            return bg_id, f"Nền chỉ định ({bg_id})"
        except Exception:
            pass  # Nếu ID không tồn tại thì rơi về tự động

    # 2. Chọn theo tên nhân vật
    if bg_mode == "character" and bg_character:
        slug = chars_svc.resolve_character(bg_character)
        if slug:
            picked = bg_lib.pick([slug], rng=rng, exclude=exclude)
            if picked:
                return picked.id, f"Nền {chars_svc.display_name(slug)} ({picked.id})"
        # Không có nền của nhân vật này trong kho -> rơi về tự động

    # 3. Tự động: POOL = CHỈ NHÂN VẬT TRONG ACC
    if chars_in_account:
        picked = bg_lib.pick(chars_in_account, rng=rng, exclude=exclude)
        if picked:
            return picked.id, f"Tự động: {chars_svc.display_name(picked.character)} ({picked.id})"

    return None, "Nền mặc định"


# ------------------------------------------------------------------ Quy Trình Render Ảnh Thẻ


def render_account_card(
    sheet: Any,
    work_dir: Path,
    code: str,
    uid: str,
    background_id: str | None,
    frame_color: str | None,
    height: int,
    watermark: str | None,
    bg_lib: bg_mod.BackgroundLibrary,
    out_dir: Path,
) -> tuple[Path, str, str]:
    """Gọi render_sheet gốc, lưu vào web-out/acc-<mã>-<uid>-<timestamp>.jpg.
    Trả về: (full_path, filename, download_filename).
    """
    options = RenderOptions(
        code=code,
        uid=uid,
        background=background_id,
        frame_color=frame_color if frame_color else None,
        height=height if height in (720, 1080) else 1080,
        watermark=watermark if watermark else None,
    )
    library = bg_lib if background_id else None
    jpeg_bytes = render.render_sheet(sheet, work_dir, options, backgrounds=library)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    clean_code = re.sub(r"[^A-Za-z0-9_\-]", "_", code)
    filename = f"acc-{clean_code}-{uid}-{ts}.jpg"
    dest_path = out_dir / filename
    dest_path.write_bytes(jpeg_bytes)

    download_name = f"acc-{clean_code}-{uid}.jpg"
    return dest_path, filename, download_name


def save_acc_json(
    out_dir: Path,
    code: str,
    uid: str,
    index_data: dict,
    list_data: dict,
    extras: Any,
    wish_lines: list[str],
    chars_acc: list[str],
) -> Path:
    """Lưu snapshot dữ liệu cào sạch theo mã acc trước khi vẽ.
    Tuyệt đối không chứa mật khẩu, cookie, authkey.
    """
    clean_code = re.sub(r"[^A-Za-z0-9_\-]", "_", code)
    json_path = out_dir / f"acc-{clean_code}-{uid}.json"
    payload = {
        "code": code,
        "uid": uid,
        "saved_at": time.time(),
        "index_data": index_data,
        "list_data": list_data,
        "extras": extras,
        "wish_lines": wish_lines,
        "chars_in_account": chars_acc,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path


def process_single_account_pipeline(
    username: str,
    password: str,
    code: str,
    bg_mode: str,
    bg_character: str,
    bg_id: str,
    frame_color: str | None,
    height: int,
    watermark: str,
    state: WebState,
    work_dir: Path,
    on_stage: Callable[[str, str], None] | None = None,
    on_captcha: Callable[[str], None] | None = None,
    captcha_mode: str = "local",
    check_action_requests: Callable[[], dict[str, bool]] | None = None,
) -> tuple[Path, str, str, str, str, Any, Any, list[str], list[str]]:
    """Lõi xử lý thống nhất cho 1 acc (dùng chung cho cả Ghép đơn và Ghép hàng loạt Excel):
    1. Đăng nhập ẩn danh mới (headless=False), đóng hẳn sau khi xong, không dùng profile Chrome cá nhân.
    2. Chế độ captcha: 'local' (hiện cửa sổ Chromium cho người dùng kéo trực tiếp, có dự phòng).
    3. Ghé qua Battle Chronicle 1 lần để lấy cookie_token_v2.
    4. Cào thông tin với delay ngẫu nhiên 1-5s giữa các request.
    5. Lưu JSON sạch trước khi vẽ.
    6. Chọn nền theo đúng pool nhân vật có trong acc.
    7. Render ảnh JPEG 1920x1080 (hoặc 720).
    """
    state.record_login_start()

    # 1. Đăng nhập
    cookies = login_with_password(
        username,
        password,
        headless=False,
        captcha_mode=captcha_mode,
        captcha_url_base="",
        captcha_port=CAPTCHA_PORT,
        on_captcha=on_captcha,
        on_stage=on_stage,
        timeout_seconds=300.0,
        check_action_requests=check_action_requests,
    )
    uids = discover_uids(cookies)
    if not uids:
        raise LoginError(
            "no_genshin_role",
            "Tài khoản HoYoLAB này chưa liên kết nhân vật Genshin nào, hoặc Sổ Tay Hành Trình chưa bật công khai."
        )
    uid = uids[0]

    # 2. Cào dữ liệu với delay ngẫu nhiên 1-5s giữa mỗi bước
    if on_stage:
        on_stage("fetching_data", f"Đăng nhập thành công (UID {uid})! Bắt đầu cào dữ liệu...")

    client = hoyolab.HoyolabClient(cookies.cookie, on_verification=None)
    time.sleep(random.uniform(1.0, 5.0))
    index_data = client.index(uid)

    time.sleep(random.uniform(1.0, 5.0))
    list_data = client.character_list(uid)

    root = hoyolab.data_dir()
    icons = hoyolab.UrlIconCache(root / "icons" / "hoyolab")
    enka_icons = IconCache(root / "icons")
    time.sleep(random.uniform(1.0, 5.0))
    extras = hoyolab.fetch_enka_extras(uid)

    wish_counter, wish_lines = None, []
    if cookies.cookie_token:
        time.sleep(random.uniform(1.0, 5.0))
        got = wish_mod.fetch_wish_history(uid, cookie_token=cookies.cookie_token)
        if got.records is not None:
            wish_counter = wish_mod.banner_counter(got.records)
            wish_lines = [
                f"Lịch sử roll ({got.source}, {len(got.records)} lượt):",
                *("  " + t for t in wish_mod.summary_vi(got.records).splitlines())
            ]
            if wish_counter is None:
                wish_lines.append("Bỏ qua ô banner: chưa có lượt roll banner nhân vật nào.")
        else:
            wish_lines = [f"Bỏ qua ô banner: {got.reason}"]
    else:
        wish_lines = [
            "Bỏ qua ô banner: trình duyệt đăng nhập không trả cookie_token_v2 "
            "(lịch sử roll cần cookie này; các phần khác vẫn vẽ đủ)."
        ]

    options = RenderOptions(code=code, uid=uid)
    sheet, report = hoyolab.build_sheet_from_data(
        index_data, list_data, work_dir, icons, enka_icons, extras, options,
        wish_counter, wish_lines
    )

    # 3. Tập hợp nhân vật trong acc
    chars_acc = list(getattr(report, "account_characters", None) or [])
    for c in characters_in_account(None, sheet):
        if c not in chars_acc:
            chars_acc.append(c)

    # 4. Lưu JSON sạch trước khi vẽ
    save_acc_json(
        state.out_dir, code, uid, index_data, list_data, extras, wish_lines, chars_acc
    )

    # 5. Chọn nền trong pool của acc
    if on_stage:
        on_stage("rendering", "Đã cào dữ liệu xong! Đang chọn hình nền và render ảnh thẻ 1920×1080...")

    bg_id_picked, bg_desc = pick_background_for_acc(
        state.bg_lib, state.characters, chars_acc,
        bg_mode, bg_character, bg_id
    )

    # 6. Render
    dest_path, filename, dl_name = render_account_card(
        sheet, work_dir, code, uid, bg_id_picked,
        frame_color, height, watermark,
        state.bg_lib, state.out_dir
    )

    return dest_path, filename, dl_name, uid, bg_desc, sheet, report, wish_lines, chars_acc


# ------------------------------------------------------------------ Workers Xử Lý Nền (Đa luồng)


def run_single_job_thread(job: SingleJob, state: WebState) -> None:
    """Chạy độc lập trên luồng nền riêng biệt (threading.Thread).
    Tuyệt đối không chạy trên event loop aiohttp để không bao giờ làm nghẽn request web.
    """
    with state.thread_lock:
        work_dir = Path(tempfile.mkdtemp(prefix=f"gacard-web-{job.id[:8]}-"))
        job.work_dir = work_dir
        job.status = "logging_in"
        job.stage_message = "Trình duyệt đang khởi động và kết nối HoYoLAB..."
        job.updated_at = time.time()
        log_terminal(job.id, f"Bắt đầu xử lý ghép ảnh acc '{job.code}'...")

        def _on_captcha(link: str) -> None:
            job.status = "waiting_captcha"
            job.captcha_link = link
            job.captcha_deadline = time.time() + 300.0
            job.updated_at = time.time()
            if link:
                job.stage_message = "HoYoLAB yêu cầu kéo Captcha xác minh"
                log_terminal(job.id, f"HoYoLAB yêu cầu giải Captcha tại {link}")
            else:
                job.stage_message = "HoYoLAB yêu cầu kéo Captcha xác minh. Vui lòng kéo trực tiếp trên cửa sổ trình duyệt Chromium."
                log_terminal(job.id, "HoYoLAB yêu cầu giải Captcha. Cửa sổ trình duyệt Chromium đã hiện lên trên màn hình để kéo trực tiếp.")

        def _on_stage(stage: str, msg: str) -> None:
            job.status = stage
            job.stage_message = msg
            job.updated_at = time.time()
            log_terminal(job.id, msg)

        def _check_action_requests() -> dict[str, bool]:
            actions = {
                "bring_to_front": job.req_bring_to_front,
                "fallback_web": job.req_fallback_web,
            }
            if job.req_bring_to_front:
                job.req_bring_to_front = False
            if job.req_fallback_web:
                job.req_fallback_web = False
            return actions

        try:
            (
                dest_path, filename, dl_name, uid, bg_desc,
                sheet, report, wish_lines, chars_acc
            ) = process_single_account_pipeline(
                username=job.username,
                password=job.password,
                code=job.code,
                bg_mode=job.bg_mode,
                bg_character=job.bg_character,
                bg_id=job.bg_id,
                frame_color=job.frame_color,
                height=job.height,
                watermark=job.watermark,
                state=state,
                work_dir=work_dir,
                on_stage=_on_stage,
                on_captcha=_on_captcha,
                captcha_mode="local",
                check_action_requests=_check_action_requests,
            )
            job.uid = uid
            job.sheet = sheet
            job.report = report
            job.chars_in_account = chars_acc
            job.current_bg_id = bg_desc
            job.image_filename = filename
            job.image_url = f"/images/{filename}"
            job.download_name = dl_name

            wl_str = f"WL {report.world_level}" if report.world_level is not None else ""
            ar_str = f"AR {report.adventure_rank or '?'}"
            if wl_str:
                ar_str += f" ({wl_str})"

            job.summary = {
                "uid": uid,
                "nickname": report.nickname or "HoYoLAB User",
                "ar": ar_str,
                "chars_drawn": len(report.characters),
                "chars_total": report.characters_total or len(report.characters),
                "weapons_drawn": len(report.weapons),
                "weapons_total": getattr(report, "weapons_total", None) or len(report.weapons),
                "explorations": [f"{e.name}: {e.pct}%" for e in report.explorations],
                "background_desc": bg_desc,
                "wish_info": "\n".join(wish_lines) if wish_lines else "Bỏ qua ô banner",
            }
            job.status = "done"
            job.stage_message = f"Ghép ảnh thành công! File xuất: {filename}"
            job.updated_at = time.time()
            log_terminal(job.id, f"Hoàn tất ghép ảnh: {filename}")
        except Exception as exc:
            job.status = "error"
            msg = getattr(exc, "message_vi", None) or str(exc)
            job.error = msg
            job.stage_message = f"Lỗi: {msg}"
            job.updated_at = time.time()
            log_terminal(job.id, f"Lỗi xử lý acc: {msg}")
        finally:
            job.req_bring_to_front = False
            job.req_fallback_web = False
            job.password = ""
            job.username = ""


async def run_batch_job(batch: BatchJob, rows_to_run: list[dict[str, Any]], state: WebState) -> None:
    async with state.worker_lock:
        batch.status = "running"
        batch.updated_at = time.time()

        for idx, item in enumerate(rows_to_run):
            batch.current_index = idx + 1
            row_dict = batch.rows[idx]

            # Nếu hàng đã bị lỗi từ khâu đọc file (thiếu mk, sai regex)
            if row_dict.get("status") == "Lỗi":
                batch.error_count += 1
                batch.updated_at = time.time()
                continue

            row_dict["status"] = "Đang chạy"
            batch.updated_at = time.time()

            code = item["code"]
            username = item["username"]
            password = item["password"]
            bg_input = item.get("background")
            frame_color = item.get("frame_color")
            bg_mode = "character" if bg_input else "auto"

            work_dir = Path(tempfile.mkdtemp(prefix=f"gacard-batch-{batch.id[:6]}-{idx}-"))

            def _on_batch_captcha(link: str) -> None:
                row_dict["status"] = "Chờ kéo Captcha"
                batch.updated_at = time.time()
                log_terminal(batch.id, f"Hàng {idx+1} ({code}): HoYoLAB yêu cầu kéo Captcha trên cửa sổ trình duyệt.")

            try:
                dest_path, filename, dl_name, uid, bg_desc, sheet, report, wish_lines, chars_acc = await asyncio.to_thread(
                    process_single_account_pipeline,
                    username=username,
                    password=password,
                    code=code,
                    bg_mode=bg_mode,
                    bg_character=bg_input or "",
                    bg_id="",
                    frame_color=frame_color,
                    height=1080,
                    watermark="",
                    state=state,
                    work_dir=work_dir,
                    on_stage=None,
                    on_captcha=_on_batch_captcha,
                    captcha_mode="local",
                )
                row_dict["uid"] = uid
                row_dict["background"] = bg_desc
                row_dict["status"] = "Xong"
                row_dict["image_url"] = f"/images/{filename}"
                row_dict["download_filename"] = filename
                batch.successful_images.append((dl_name, dest_path))
                batch.success_count += 1
            except LoginError as exc:
                row_dict["status"] = "Lỗi"
                row_dict["reason"] = exc.message_vi
                batch.error_count += 1
                if exc.kind == "too_many_requests":
                    batch.status = "stopped"
                    batch.updated_at = time.time()
                    log_terminal(batch.id, f"HoYoLAB giới hạn tạm thời (Too many requests). Đã dừng hàng đợi tại hàng {idx+1}!")
                    break
            except Exception as exc:
                row_dict["status"] = "Lỗi"
                row_dict["reason"] = getattr(exc, "message_vi", None) or str(exc)
                batch.error_count += 1
            finally:
                item["password"] = ""

            batch.updated_at = time.time()

        if batch.status != "stopped":
            batch.status = "done"
        batch.updated_at = time.time()
        batch.updated_at = time.time()


# ------------------------------------------------------------------ Handlers Tuyến Đường


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok", content_type="text/plain", headers={"Cache-Control": "no-cache"})


async def handle_api_stats(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    return web.json_response(state.get_pacing_stats())


def setup_routes(app: web.Application) -> None:
    # Health Check
    app.router.add_get("/health", handle_health)
    app.router.add_get("/api/stats", handle_api_stats)

    # Pages HTML
    app.router.add_get("/", handle_page_single)
    app.router.add_get("/single", handle_page_single)
    app.router.add_get("/batch", handle_page_batch)
    app.router.add_get("/backgrounds", handle_page_backgrounds)
    app.router.add_get("/settings", handle_page_settings)

    # API Single
    app.router.add_post("/api/single", handle_api_single)
    app.router.add_get("/api/jobs/{id}", handle_api_job_status)
    app.router.add_post("/api/jobs/{id}/reroll-bg", handle_api_job_reroll)
    app.router.add_post("/api/jobs/{id}/bring-to-front", handle_api_job_bring_to_front)
    app.router.add_post("/api/jobs/{id}/fallback-web-captcha", handle_api_job_fallback_web)

    # API Batch
    app.router.add_post("/api/batch", handle_api_batch)
    app.router.add_get("/api/batch/{id}", handle_api_batch_status)
    app.router.add_get("/api/batch/{id}/download-zip", handle_api_batch_zip)

    # API Backgrounds
    app.router.add_get("/api/backgrounds", handle_api_bg_list)
    app.router.add_get("/api/backgrounds/{id}/thumb", handle_api_bg_thumb)
    app.router.add_post("/api/backgrounds/add", handle_api_bg_add)
    app.router.add_post("/api/backgrounds/{id}/delete", handle_api_bg_delete)
    app.router.add_post("/api/backgrounds/{id}/side", handle_api_bg_side)

    # API Settings
    app.router.add_get("/api/settings", handle_api_settings_get)
    app.router.add_post("/api/settings", handle_api_settings_post)

    # Images & Download
    app.router.add_get("/images/{filename}", handle_serve_image)
    app.router.add_get("/download/{filename}", handle_download_image)


def render_html_file(filename: str) -> web.Response:
    path = TEMPLATE_DIR / filename
    if not path.is_file():
        return web.Response(text="Template not found", status=404)
    content = path.read_text(encoding="utf-8")
    return web.Response(text=content, content_type="text/html", charset="utf-8")


async def handle_page_single(request: web.Request) -> web.Response:
    return render_html_file("single.html")


async def handle_page_batch(request: web.Request) -> web.Response:
    return render_html_file("batch.html")


async def handle_page_backgrounds(request: web.Request) -> web.Response:
    return render_html_file("backgrounds.html")


async def handle_page_settings(request: web.Request) -> web.Response:
    return render_html_file("settings.html")


# ------------------------------------------------------------------ API Logic


async def handle_api_single(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Dữ liệu JSON không hợp lệ"}, status=400)

    code = str(body.get("code") or "").strip()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")

    if not code or not username or not password:
        return web.json_response({"error": "Vui lòng điền đầy đủ Mã acc, Tài khoản và Mật khẩu"}, status=400)

    if not CODE_RE.match(code):
        return web.json_response({"error": f"Mã acc '{code}' không hợp lệ (yêu cầu 1-16 ký tự chữ, số, _, -, #)"}, status=400)

    job_id = str(uuid.uuid4())
    job = SingleJob(
        id=job_id,
        code=code,
        username=username,
        password=password,
        bg_mode=body.get("bg_mode") or "auto",
        bg_character=str(body.get("bg_character") or "").strip(),
        bg_id=str(body.get("bg_id") or "").strip(),
        frame_color=body.get("frame_color") or None,
        height=int(body.get("height") or 1080),
        watermark=str(body.get("watermark") or "").strip(),
        status="logging_in",
        stage_message="Trình duyệt đang khởi động và kết nối HoYoLAB...",
    )
    state.single_jobs[job_id] = job

    # Chạy ở thread nền riêng biệt (không bao giờ chạy trong cùng luồng trả lời HTTP)
    worker_thread = threading.Thread(
        target=run_single_job_thread,
        args=(job, state),
        daemon=True,
        name=f"Worker-{job_id[:8]}"
    )
    worker_thread.start()

    log_terminal(job_id, f"Khởi tạo job cho mã acc '{code}'. Trả về job_id ngay lập tức cho client.")
    return web.json_response({
        "job_id": job_id,
        "status": job.status,
        "stage_message": job.stage_message
    })


async def handle_api_job_status(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    job_id = request.match_info["id"]
    job = state.single_jobs.get(job_id)
    if not job:
        return web.json_response({"error": "Job không tồn tại"}, status=404)

    return web.json_response({
        "id": job.id,
        "code": job.code,
        "status": job.status,
        "stage_message": job.stage_message,
        "captcha_link": job.captcha_link,
        "captcha_deadline": job.captcha_deadline,
        "image_filename": job.image_filename,
        "image_url": job.image_url,
        "download_name": job.download_name,
        "summary": job.summary,
        "error": job.error,
        "pacing_stats": state.get_pacing_stats(),
    })


def reroll_account_from_json(
    json_path: Path,
    work_dir: Path,
    state: WebState,
    exclude_bg_id: str | None = None,
    frame_color: str | None = None,
    height: int = 1080,
    watermark: str = "",
) -> tuple[Path, str, str, str, str]:
    """Vẽ lại ảnh thẻ từ file JSON đã lưu, không cào lại HoYoLAB, chỉ chọn nền trong acc."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    code = data["code"]
    uid = str(data["uid"])
    chars_acc = data.get("chars_in_account", [])
    index_data = data["index_data"]
    list_data = data["list_data"]
    extras = data.get("extras")
    wish_lines = data.get("wish_lines", [])

    new_bg_id, new_bg_desc = pick_background_for_acc(
        state.bg_lib, state.characters, chars_acc,
        "auto", exclude_id=exclude_bg_id
    )

    root = Path(__file__).resolve().parent.parent.parent
    icons = hoyolab.UrlIconCache(root / "icons" / "hoyolab")
    enka_icons = IconCache(root / "icons")
    options = RenderOptions(code=code, uid=uid)
    sheet, report = hoyolab.build_sheet_from_data(
        index_data, list_data, work_dir, icons, enka_icons, extras, options,
        None, wish_lines
    )

    dest_path, filename, dl_name = render_account_card(
        sheet, work_dir, code, uid, new_bg_id,
        frame_color, height, watermark,
        state.bg_lib, state.out_dir
    )
    return dest_path, filename, dl_name, new_bg_id, new_bg_desc


async def handle_api_job_reroll(request: web.Request) -> web.Response:
    """Nút 'Đổi nền': vẽ lại từ file JSON đã lưu của acc với nền ngẫu nhiên khác trong pool acc."""
    state: WebState = request.app["state"]
    job_id = request.match_info["id"]
    job = state.single_jobs.get(job_id)
    if not job:
        return web.json_response({"error": "Không tìm thấy dữ liệu để đổi nền"}, status=400)

    work_dir = job.work_dir or Path(tempfile.mkdtemp(prefix=f"gacard-reroll-{job.id[:8]}-"))
    job.work_dir = work_dir

    json_path = state.out_dir / f"acc-{job.code}-{job.uid}.json"
    if not json_path.exists():
        json_path = state.out_dir / f"acc-{job.code}.json"

    try:
        if json_path.exists():
            dest_path, filename, dl_name, new_bg_id, new_bg_desc = await asyncio.to_thread(
                reroll_account_from_json,
                json_path=json_path,
                work_dir=work_dir,
                state=state,
                exclude_bg_id=job.current_bg_id,
                frame_color=job.frame_color,
                height=job.height,
                watermark=job.watermark,
            )
        elif job.sheet:
            new_bg_id, new_bg_desc = pick_background_for_acc(
                state.bg_lib, state.characters, job.chars_in_account,
                "auto", exclude_id=job.current_bg_id
            )
            dest_path, filename, dl_name = await asyncio.to_thread(
                render_account_card,
                job.sheet, work_dir, job.code, job.uid or "0", new_bg_id,
                job.frame_color, job.height, job.watermark,
                state.bg_lib, state.out_dir
            )
        else:
            return web.json_response({"error": "Không tìm thấy file JSON hoặc dữ liệu để đổi nền"}, status=400)

        job.current_bg_id = new_bg_id
        job.image_filename = filename
        job.image_url = f"/images/{filename}"
        job.download_name = dl_name
        job.summary["background_desc"] = new_bg_desc
        job.updated_at = time.time()
    except Exception as exc:
        return web.json_response({"error": f"Lỗi render lại ảnh: {exc}"}, status=500)

    return web.json_response({
        "status": "done",
        "image_url": job.image_url,
        "image_filename": job.image_filename,
        "download_name": job.download_name,
        "summary": job.summary,
    })


async def handle_api_job_bring_to_front(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    job_id = request.match_info["id"]
    job = state.single_jobs.get(job_id)
    if not job or job.status in ("done", "error", "stopped"):
        return web.json_response({
            "ok": False,
            "message": "Không tìm thấy job đang chạy hoặc job đã hoàn thành."
        }, status=400)
    job.req_bring_to_front = True
    return web.json_response({
        "ok": True,
        "message": "Đã ghi nhận yêu cầu đưa cửa sổ Chromium lên trước màn hình."
    })


async def handle_api_job_fallback_web(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    job_id = request.match_info["id"]
    job = state.single_jobs.get(job_id)
    if not job or job.status in ("done", "error", "stopped"):
        return web.json_response({
            "ok": False,
            "message": "Không tìm thấy job đang chạy hoặc job đã hoàn thành."
        }, status=400)
    job.req_fallback_web = True
    return web.json_response({
        "ok": True,
        "message": "Đã ghi nhận yêu cầu mở trang kéo Captcha dự phòng cổng 8766."
    })


async def handle_api_batch(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    reader = await request.multipart()

    field_file = None
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "file":
            field_file = part
            break

    if field_file is None:
        return web.json_response({"error": "Thiếu file upload"}, status=400)

    filename = field_file.filename or "upload.xlsx"
    file_bytes = await field_file.read()

    # Đọc danh sách hàng
    parsed_rows = parse_batch_rows(file_bytes, filename)
    if not parsed_rows:
        return web.json_response({"error": "File rỗng hoặc không có hàng hợp lệ"}, status=400)

    # Tạo batch job
    batch_id = str(uuid.uuid4())
    rows_display: list[dict[str, Any]] = []
    rows_to_run: list[dict[str, Any]] = []

    for r in parsed_rows:
        status_text = "Chờ chạy" if r.is_valid else "Lỗi"
        reason_text = r.error if r.error else ""
        rows_display.append({
            "stt": r.row_num,
            "code": r.code,
            "uid": None,
            "status": status_text,
            "reason": reason_text,
            "background": r.background or "",
            "image_url": None,
            "download_filename": None,
        })
        rows_to_run.append({
            "code": r.code,
            "username": r.username,
            "password": r.password,
            "background": r.background,
            "frame_color": r.frame_color,
        })

    batch = BatchJob(
        id=batch_id,
        status="queued",
        total=len(rows_display),
        rows=rows_display,
    )
    state.batch_jobs[batch_id] = batch

    # Bắt đầu chạy tuần tự ở nền
    asyncio.create_task(run_batch_job(batch, rows_to_run, state))

    return web.json_response({"batch_id": batch_id})


async def handle_api_batch_status(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    batch_id = request.match_info["id"]
    batch = state.batch_jobs.get(batch_id)
    if not batch:
        return web.json_response({"error": "Batch không tồn tại"}, status=404)

    return web.json_response({
        "id": batch.id,
        "status": batch.status,
        "total": batch.total,
        "success_count": batch.success_count,
        "error_count": batch.error_count,
        "current_index": batch.current_index,
        "rows": batch.rows,
        "pacing_stats": state.get_pacing_stats(),
    })


async def handle_api_batch_zip(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    batch_id = request.match_info["id"]
    batch = state.batch_jobs.get(batch_id)
    if not batch:
        return web.Response(text="Batch not found", status=404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, file_path in batch.successful_images:
            if file_path.is_file():
                zf.write(file_path, arcname=arcname)

    buf.seek(0)
    zip_bytes = buf.getvalue()
    filename = f"acc-batch-{batch.id[:8]}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return web.Response(body=zip_bytes, content_type="application/zip", headers=headers)


# ------------------------------------------------------------------ Backgrounds API


async def handle_api_bg_list(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    items = state.bg_lib.list()
    out = []
    for b in items:
        out.append({
            "id": b.id,
            "character": b.character,
            "width": b.width,
            "height": b.height,
            "subject_side": b.subject_side,
            "used_count": b.used_count,
            "added_at": b.added_at.isoformat() if b.added_at else "",
            "source": b.source,
        })
    return web.json_response(out)


async def handle_api_bg_thumb(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    bg_id = request.match_info["id"]
    # Kiểm tra an toàn id
    if ".." in bg_id or "/" in bg_id or "\\" in bg_id:
        return web.Response(text="Forbidden", status=403)

    data = state.get_thumb_bytes(bg_id)
    if not data:
        return web.Response(text="Image not found", status=404)
    return web.Response(body=data, content_type="image/jpeg")


async def handle_api_bg_add(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    reader = await request.multipart()

    character = ""
    file_bytes = b""

    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "character":
            character = (await part.text()).strip()
        elif part.name == "file":
            file_bytes = await part.read()

    if not character or not file_bytes:
        return web.json_response({"error": "Vui lòng nhập tên nhân vật và chọn file ảnh"}, status=400)

    # Chuẩn hóa slug
    slug = state.characters.resolve_character(character) or character.lower()
    try:
        new_bg = state.bg_lib.add(file_bytes, slug, source="web")
        state.thumb_cache.pop(new_bg.id, None)
        return web.json_response({"success": True, "id": new_bg.id, "character": new_bg.character})
    except bg_mod.BackgroundError as exc:
        return web.json_response({"error": exc.message_vi}, status=400)
    except Exception as exc:
        return web.json_response({"error": f"Lỗi không thể lưu ảnh: {exc}"}, status=400)


async def handle_api_bg_delete(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    bg_id = request.match_info["id"]
    try:
        state.bg_lib.remove(bg_id)
        state.thumb_cache.pop(bg_id, None)
        return web.json_response({"success": True})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def handle_api_bg_side(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    bg_id = request.match_info["id"]
    try:
        body = await request.json()
        side = body.get("side")
        if side not in ("left", "center", "right"):
            return web.json_response({"error": "Vị trí không hợp lệ"}, status=400)
        state.bg_lib.set_subject_side(bg_id, side)
        return web.json_response({"success": True})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=400)


# ------------------------------------------------------------------ Settings API


async def handle_api_settings_get(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    s = load_web_settings(state.data_dir)
    return web.json_response(s)


async def handle_api_settings_post(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    try:
        body = await request.json()
        save_web_settings(state.data_dir, body)
        return web.json_response({"success": True})
    except Exception as exc:
        return web.json_response({"error": str(exc)}, status=400)


# ------------------------------------------------------------------ File Serving


async def handle_serve_image(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    filename = request.match_info["filename"]
    if ".." in filename or "/" in filename or "\\" in filename:
        return web.Response(text="Forbidden", status=403)

    file_path = state.out_dir / filename
    if not file_path.is_file():
        return web.Response(text="Not found", status=404)

    return web.Response(body=file_path.read_bytes(), content_type="image/jpeg")


async def handle_download_image(request: web.Request) -> web.Response:
    state: WebState = request.app["state"]
    filename = request.match_info["filename"]
    if ".." in filename or "/" in filename or "\\" in filename:
        return web.Response(text="Forbidden", status=403)

    file_path = state.out_dir / filename
    if not file_path.is_file():
        return web.Response(text="Not found", status=404)

    # Rút gọn tên tải về: acc-<mã>-<uid>.jpg
    parts = filename.split("-")
    if len(parts) >= 3 and parts[0] == "acc":
        clean_name = f"acc-{parts[1]}-{parts[2]}.jpg"
    else:
        clean_name = filename

    headers = {"Content-Disposition": f'attachment; filename="{clean_name}"'}
    return web.Response(body=file_path.read_bytes(), content_type="image/jpeg", headers=headers)


# ------------------------------------------------------------------ Khởi Tạo Ứng Dụng


def create_app() -> web.Application:
    data_dir = get_data_dir()
    state = WebState(data_dir)
    app = web.Application()
    app["state"] = state
    setup_routes(app)
    return app


def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Edit Genshin Local Web UI")
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help=f"Cổng chạy web (mặc định: {DEFAULT_PORT})")
    args = parser.parse_args()

    port = args.port
    if port in FORBIDDEN_PORTS:
        print(f"LỖI: Cổng {port} đang được sử dụng bởi dịch vụ khác của bạn. Vui lòng chọn cổng khác (ví dụ: {DEFAULT_PORT}).", file=sys.stderr)
        sys.exit(1)

    app = create_app()
    print("=" * 60)
    print(f"  EDIT GENSHIN LOCAL WEB UI ĐANG KHỞI CHẠY")
    print(f"  -> Truy cập tại: http://localhost:{port}/")
    print(f"  -> Cổng Captcha: http://localhost:{CAPTCHA_PORT}/")
    print(f"  -> Nhấn Ctrl + C để dừng máy chủ")
    print("=" * 60)

    web.run_app(app, host="127.0.0.1", port=port, print=None)


if __name__ == "__main__":
    main()
