"""Cấu hình bot từ biến môi trường / file .env (đường dẫn đổi được bằng GACARD_ENV_FILE)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

DEFAULT_USER_AGENT = "gacard/0.1 (+telegram-bot)"
DEFAULT_OUTPUT_DIR = Path(r"D:\Claude\genshin\output")
DEFAULT_ADMIN_CHAT_IDS = frozenset({5569863657})


class ConfigError(Exception):
    """Cấu hình thiếu/sai. message_vi là câu tiếng Việt hiển thị cho người chạy bot."""

    def __init__(self, message_vi: str):
        super().__init__(message_vi)
        self.message_vi = message_vi


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    allowed_chat_ids: frozenset[int]
    data_dir: Path
    enka_user_agent: str = DEFAULT_USER_AGENT
    fixture: str = ""  # mã fixture cho FixtureCropper; rỗng = lấy fixture đầu tiên tìm được
    admin_chat_ids: frozenset[int] = DEFAULT_ADMIN_CHAT_IDS  # được dùng /moi, /bomoi, /danhsach
    output_dir: Path = DEFAULT_OUTPUT_DIR  # bản sao ảnh cho chủ dự án: acc-<ma>-<uid>-hoyolab|uid|chup.jpg
    hoyolab_owner_uids: frozenset[str] = frozenset()  # UID thuộc cookie HoYoLAB của chủ bot (ô banner /ghep)
    # --- đăng nhập /ghep <tk>-<mk> (không ảnh hưởng chế độ /ghep <UID> cũ) ---
    login_headless: bool = False  # False = mở trình duyệt thật trên máy chạy bot để kéo captcha
    login_captcha_mode: str = "local"  # "local" | "web" (web = trang kéo captcha tạm, xem login_web.py)
    login_public_url: str = ""  # base URL tới máy chạy bot cho chế độ web, vd http://<IP>:8765
    login_port: int = 8765
    login_timeout: float = 300.0
    # --- tham số vận hành (không đọc từ env) ---
    session_ttl: float = 15 * 60
    max_images: int = 12
    max_bytes: int = 20 * 1024 * 1024
    debounce: float = 2.0
    min_long_side: int = 1280
    output_keep_days: int = 7

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def out_dir(self) -> Path:
        return self.data_dir / "out"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def backgrounds_dir(self) -> Path:
        return self.data_dir / "backgrounds"

    @property
    def enka_cache_dir(self) -> Path:
        return self.data_dir / "enka-cache"

    @property
    def fixtures_dir(self) -> Path:
        return self.data_dir / "fixtures"

    @property
    def lock_file(self) -> Path:
        return self.data_dir / "bot.lock"

    @property
    def allowed_file(self) -> Path:
        return self.data_dir / "allowed.json"


def parse_env_file(path: Path) -> dict[str, str]:
    """Đọc file .env đơn giản (KEY=VALUE, # ghi chú, bỏ dấu nháy). Không có file → {}."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def parse_chat_ids(raw: str, name: str = "GACARD_ALLOWED_CHAT_IDS") -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise ConfigError(
                f"{name} có giá trị không phải số: '{part}'. "
                "Ghi các chat ID cách nhau bằng dấu phẩy, ví dụ 123456789,-1001234567890."
            ) from None
    return frozenset(ids)


def load_config(environ: Mapping[str, str] | None = None) -> Config:
    """Gộp .env (file) và biến môi trường (ưu tiên hơn). Thiếu token → ConfigError."""
    env = os.environ if environ is None else environ
    env_file = Path(env.get("GACARD_ENV_FILE") or ".env")
    merged = {**parse_env_file(env_file), **env}
    base = env_file.resolve().parent

    token = (merged.get("GACARD_BOT_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "Chưa có GACARD_BOT_TOKEN.\n"
            "Cách sửa: tạo bot RIÊNG ở @BotFather (không dùng token của bot GAIS), rồi ghi vào file "
            f".env ({env_file.resolve()}) dòng:\n    GACARD_BOT_TOKEN=<token>\n"
            "Ghi thêm GACARD_ALLOWED_CHAT_IDS=<chat id của bạn> để bot chịu trả lời."
        )

    data_dir = Path(merged.get("GACARD_DATA_DIR") or "data/acc-card")
    if not data_dir.is_absolute():
        data_dir = base / data_dir

    output_dir = Path((merged.get("GACARD_OUTPUT_DIR") or "").strip() or DEFAULT_OUTPUT_DIR)
    if not output_dir.is_absolute():
        output_dir = base / output_dir

    admins_raw = (merged.get("GACARD_ADMIN_CHAT_IDS") or "").strip()
    admins = parse_chat_ids(admins_raw, "GACARD_ADMIN_CHAT_IDS") if admins_raw else DEFAULT_ADMIN_CHAT_IDS

    owner_uids = frozenset(
        u.strip() for u in (merged.get("GACARD_HOYOLAB_OWNER_UIDS") or "").replace(";", ",").split(",") if u.strip()
    )

    def _bool_env(key: str, default: bool) -> bool:
        raw = (merged.get(key) or "").strip().lower()
        return default if raw not in ("0", "1", "true", "false", "yes", "no") else raw in ("1", "true", "yes")

    try:
        login_port = int((merged.get("GACARD_LOGIN_PORT") or "8765").strip())
    except ValueError:
        login_port = 8765
    try:
        login_timeout = float((merged.get("GACARD_LOGIN_TIMEOUT") or "300").strip())
    except ValueError:
        login_timeout = 300.0

    return Config(
        token=token,
        allowed_chat_ids=parse_chat_ids(merged.get("GACARD_ALLOWED_CHAT_IDS", "")),
        data_dir=data_dir,
        enka_user_agent=(merged.get("GACARD_ENKA_USER_AGENT") or DEFAULT_USER_AGENT).strip(),
        fixture=(merged.get("GACARD_FIXTURE") or "").strip(),
        admin_chat_ids=admins,
        output_dir=output_dir,
        hoyolab_owner_uids=owner_uids,
        login_headless=_bool_env("GACARD_LOGIN_HEADLESS", False),
        login_captcha_mode=(merged.get("GACARD_LOGIN_CAPTCHA_MODE") or "local").strip().lower() or "local",
        login_public_url=(merged.get("GACARD_LOGIN_PUBLIC_URL") or "").strip(),
        login_port=login_port,
        login_timeout=login_timeout,
    )
