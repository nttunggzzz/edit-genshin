"""Giao thức (Protocol) cho mọi phụ thuộc của bot + logic thuần (không đụng Telegram, dễ test).

Bot chỉ nói chuyện qua các Protocol này; app.py gắn module thật bằng import trễ.
"""

from __future__ import annotations

import random
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

# ----------------------------------------------------------------------------------------
# Protocol
# ----------------------------------------------------------------------------------------

Buttons = Sequence[Sequence[tuple[str, str]]]  # hàng -> (nhãn, callback_data)


class BackgroundLike(Protocol):
    id: str
    character: str


class BackgroundStore(Protocol):
    def add(self, image_bytes: bytes, character: str, source: str | None = None) -> BackgroundLike: ...
    def list(self, character: str | None = None) -> "list[BackgroundLike]": ...
    def get(self, background_id: str) -> "BackgroundLike | None": ...
    def remove(self, background_id: str) -> Any: ...
    def pick(
        self, characters_in_account: "list[str]", rng: "random.Random | None" = None, exclude: "Sequence[str]" = ()
    ) -> "BackgroundLike | None": ...  # ngẫu nhiên trong các nền có nhân vật nằm trong acc
    def mark_used(self, background_id: str) -> Any: ...


class CharacterService(Protocol):
    def resolve_character(self, name: str) -> "str | None": ...
    def load_hype(self) -> "dict[str, int]": ...
    def display_name(self, slug: str) -> str: ...


class EnkaFetcher(Protocol):
    async def fetch(self, uid: str) -> Any: ...  # EnkaProfile


class UserFacingError(Exception):
    """Lỗi nghiệp vụ có sẵn câu tiếng Việt để nói với người dùng."""

    def __init__(self, message_vi: str):
        super().__init__(message_vi)
        self.message_vi = message_vi


class Cropper(Protocol):
    def build_sheet(self, images: "list[Path]", work_dir: Path) -> Any: ...  # AccountSheet


@dataclass
class RenderRequest:
    """Tuỳ chọn render ở dạng trung lập; adapter đổi sang RenderOptions của gacard.models."""

    code: str | None = None
    uid: str | None = None
    background_id: str | None = None
    watermark: str | None = None


class Renderer(Protocol):
    """Trả JPEG bytes. Nền chọn từ kho (background_id) do renderer tự mark_used; None = nền mặc định."""

    def render(
        self,
        sheet: Any,
        base_dir: Path,
        request: RenderRequest,
        backgrounds: "BackgroundStore | None",
    ) -> bytes: ...


class UidSheetBuilder(Protocol):
    """Chế độ chỉ UID: tra Enka rồi tự vẽ mảnh vào work_dir. Trả (sheet, hồ sơ Enka, UidModeReport).

    Chạy đồng bộ (gọi bằng asyncio.to_thread); lỗi Enka mang message_vi."""

    def build(self, uid: str, work_dir: Path) -> "tuple[Any, Any, Any]": ...


class HoyolabSheetBuilder(Protocol):
    """Chế độ HoYoLAB (/ghep): Sổ Tay Hành Trình qua cookie CỦA CHỦ BOT trong .env → mảnh tự vẽ trong work_dir.

    Trả (sheet, HoyolabReport). Chạy đồng bộ (asyncio.to_thread). Lỗi HoYoLAB mang `kind` + message_vi;
    kind == "verification" nghĩa là HoYoLAB đòi captcha (bot báo admin, không bao giờ lấy/chuyển captcha)."""

    def build(self, uid: str, work_dir: Path, code: "str | None") -> "tuple[Any, Any]": ...


class Messenger(Protocol):
    async def send_text(self, chat_id: int, text: str, buttons: "Buttons | None" = None) -> None: ...
    async def send_photo(self, chat_id: int, data: bytes, caption: "str | None" = None) -> None: ...
    async def send_document(
        self,
        chat_id: int,
        data: bytes,
        filename: str,
        caption: "str | None" = None,
        buttons: "Buttons | None" = None,
    ) -> None: ...
    async def download(self, file_id: str, dest: Path) -> None: ...
    async def delete_message(self, chat_id: int, message_id: int) -> None: ...


@dataclass
class Services:
    cropper: Cropper
    renderer: Renderer
    backgrounds: BackgroundStore
    characters: CharacterService
    enka: "EnkaFetcher | None" = None
    now: Callable[[], datetime] = datetime.now
    uidcard: "UidSheetBuilder | None" = None  # None = /uid chỉ đặt UID + tóm tắt Enka như trước
    hoyocard: "HoyolabSheetBuilder | None" = None  # None = /ghep báo chưa bật
    rng: random.Random = field(default_factory=random.Random)  # chọn nền ngẫu nhiên (test truyền seed)


def error_text_vi(exc: BaseException) -> "str | None":
    """Lấy message_vi của lỗi nghiệp vụ (BackgroundError, EnkaError...) nếu có."""
    msg = getattr(exc, "message_vi", None)
    return str(msg) if msg else None


# ----------------------------------------------------------------------------------------
# Cropper tạm: sao chép sheet giả lập có sẵn (bỏ qua ảnh gốc)
# ----------------------------------------------------------------------------------------


class FixtureCropper:
    """Chưa có ảnh gốc thật: bỏ qua ảnh người dùng gửi, trả về sheet trong data/acc-card/fixtures/<ma>/."""

    needs_images = False

    def __init__(self, fixture_dir: Path, loader: "Callable[[Path], Any] | None" = None):
        self.fixture_dir = Path(fixture_dir)
        self._loader = loader

    def build_sheet(self, images: "list[Path]", work_dir: Path) -> Any:
        if not (self.fixture_dir / "sheet.json").is_file():
            from .texts import NO_FIXTURE

            raise UserFacingError(NO_FIXTURE)
        if work_dir.exists():
            shutil.rmtree(work_dir)
        shutil.copytree(self.fixture_dir, work_dir)
        loader = self._loader
        if loader is None:
            from gacard.render import load_sheet  # import trễ: module do agent khác viết

            def loader(d: Path) -> Any:
                return load_sheet(d)[0]

        return loader(work_dir)


def find_default_fixture(fixtures_dir: Path, code: str = "") -> "Path | None":
    """Thư mục fixture theo mã; mã rỗng → fixture đầu tiên (theo tên) có sheet.json."""
    if code:
        cand = fixtures_dir / code
        return cand if (cand / "sheet.json").is_file() else None
    if fixtures_dir.is_dir():
        for d in sorted(fixtures_dir.iterdir()):
            if (d / "sheet.json").is_file():
                return d
    return None


# ----------------------------------------------------------------------------------------
# Phân tích lệnh / caption
# ----------------------------------------------------------------------------------------

UID_RE = re.compile(r"^\d{9,10}$")
CODE_RE = re.compile(r"^[A-Za-z0-9#_\-]{1,16}$")


def normalize_uid(text: str) -> "str | None":
    t = text.strip()
    return t if UID_RE.match(t) else None


def normalize_code(text: str) -> "str | None":
    t = text.strip()
    return t if CODE_RE.match(t) else None


@dataclass
class CaptionOptions:
    code: "str | None" = None
    uid: "str | None" = None
    nen: "str | None" = None  # tên nhân vật thô (chưa resolve)
    errors: "list[str]" = field(default_factory=list)

    @property
    def only_nen(self) -> bool:
        return self.nen is not None and self.code is None and self.uid is None


_CAPTION_KEYS = {"ma": "code", "uid": "uid", "nen": "nen", "themnen": "nen"}


def parse_caption_options(caption: "str | None") -> CaptionOptions:
    """'ma 1043 uid 850363095 nen columbina' -> CaptionOptions. Cho phép dấu '/' đứng trước từ khoá."""
    opts = CaptionOptions()
    if not caption:
        return opts
    tokens = caption.replace("\n", " ").split()
    key: "str | None" = None
    buf: "list[str]" = []

    def flush() -> None:
        if key is None:
            return
        value = " ".join(buf).strip()
        if key == "code":
            code = normalize_code(value) if value else None
            if code:
                opts.code = code
            else:
                opts.errors.append(f"Mã acc '{value}' không hợp lệ (chỉ chữ, số, #, -).")
        elif key == "uid":
            uid = normalize_uid(value) if value else None
            if uid:
                opts.uid = uid
            else:
                opts.errors.append(f"UID '{value}' không hợp lệ (cần 9–10 chữ số).")
        elif key == "nen":
            if value:
                opts.nen = value
            else:
                opts.errors.append("Thiếu tên nhân vật sau 'nen'.")

    for tok in tokens:
        name = tok.lstrip("/").split("@")[0].lower()
        if name in _CAPTION_KEYS:
            flush()
            key, buf = _CAPTION_KEYS[name], []
        elif key is not None:
            buf.append(tok)
    flush()
    return opts


def parse_cm_args(
    args: "str | None", resolve: "Callable[[str], str | None]"
) -> "tuple[dict[str, int], list[str]]":
    """'/cm columbina 2 zibai 0' hoặc 'hu tao 1' -> ({'columbina': 2, ...}, [lỗi tiếng Việt]).

    Tên có thể nhiều từ: gom chữ cho tới khi gặp một số, rồi mới tra tên.
    """
    pairs: "dict[str, int]" = {}
    errors: "list[str]" = []
    name_tokens: "list[str]" = []
    for tok in (args or "").replace(",", " ").split():
        if tok.isdigit():
            number = int(tok)
            name = " ".join(name_tokens).strip()
            name_tokens = []
            if not name:
                errors.append(f"Số {tok} đứng một mình, thiếu tên nhân vật phía trước.")
                continue
            if not 0 <= number <= 6:
                errors.append(f"Cung mệnh của '{name}' phải từ 0 đến 6 (bạn gõ {number}).")
                continue
            slug = resolve(name)
            if slug is None:
                errors.append(f"Không nhận ra nhân vật '{name}'.")
                continue
            pairs[slug] = number
        else:
            name_tokens.append(tok)
    if name_tokens:
        errors.append(f"'{' '.join(name_tokens)}' thiếu số cung mệnh phía sau.")
    return pairs, errors


# ----------------------------------------------------------------------------------------
# Cung mệnh
# ----------------------------------------------------------------------------------------


@dataclass
class BadgeReport:
    from_enka: "dict[str, int]" = field(default_factory=dict)
    from_manual: "dict[str, int]" = field(default_factory=dict)
    missing: "list[str]" = field(default_factory=list)  # thẻ nhận ra nhân vật nhưng không có nguồn cung mệnh
    unmatched_manual: "list[str]" = field(default_factory=list)  # /cm cho nhân vật không có thẻ nào


def enka_constellations(profile: Any) -> "dict[str, int]":
    out: "dict[str, int]" = {}
    for av in getattr(profile, "avatars", None) or []:
        if av.character and av.constellation is not None:
            out[av.character] = av.constellation
    return out


def apply_constellations(sheet: Any, profile: "Any | None", manual: "dict[str, int]") -> BadgeReport:
    """Gắn card.badge: /cm (thủ công) thắng Enka; không có nguồn nào thì giữ badge cũ của thẻ."""
    enka = enka_constellations(profile) if profile is not None else {}
    report = BadgeReport()
    seen: "set[str]" = set()
    for card in sheet.characters:
        slug = card.character
        if not slug:
            continue
        seen.add(slug)
        if slug in manual:
            card.badge = manual[slug]
            report.from_manual[slug] = manual[slug]
        elif slug in enka:
            card.badge = enka[slug]
            report.from_enka[slug] = enka[slug]
        elif card.badge is None and slug not in report.missing:
            report.missing.append(slug)
    report.unmatched_manual = [s for s in manual if s not in seen]
    return report


def characters_in_account(profile: "Any | None", sheet: Any) -> "list[str]":
    """Enka (trưng bày) ∪ thẻ đã nhận diện, giữ thứ tự xuất hiện, không trùng."""
    out: "list[str]" = []
    for av in getattr(profile, "avatars", None) or []:
        if av.character and av.character not in out:
            out.append(av.character)
    for card in sheet.characters:
        if card.character and card.character not in out:
            out.append(card.character)
    return out


# ----------------------------------------------------------------------------------------
# Chọn nền
# ----------------------------------------------------------------------------------------


@dataclass
class BgChoice:
    background: "BackgroundLike | None"  # None = nền mặc định
    reason: str  # "Nền: Columbina — chọn ngẫu nhiên trong các nhân vật có trong acc"


@dataclass
class BgPlan:
    choices: "list[BgChoice]"
    notes: "list[str]" = field(default_factory=list)


BG_RANDOM_WHY = "chọn ngẫu nhiên trong các nhân vật có trong acc"


def bg_choice(characters_svc: CharacterService, bg: "BackgroundLike | None", why: str) -> BgChoice:
    if bg is None:
        return BgChoice(None, "Nền mặc định — " + why)
    return BgChoice(bg, f"Nền: {characters_svc.display_name(bg.character)} — {why}")


def build_bg_plan(
    store: BackgroundStore,
    characters_svc: CharacterService,
    in_account: "list[str]",
    override_slug: "str | None" = None,
    rng: "random.Random | None" = None,
) -> BgPlan:
    """Nền cho lần vẽ đầu (luật chủ dự án): `nen <tên>` thắng; không thì random.choice trong các nền có
    nhân vật nằm trong acc (`in_account` = mọi nhân vật biết được của acc); không có → nền mặc định.
    Không dùng điểm hype. Nút "Đổi nền" chọn tiếp bằng store.pick(..., exclude=[nền đang dùng])."""
    name = characters_svc.display_name
    plan = BgPlan(choices=[])
    if override_slug:
        own = store.pick([override_slug], rng)
        if own is not None:
            plan.choices.append(bg_choice(characters_svc, own, "bạn chọn bằng nen"))
        else:
            plan.notes.append(
                f"Chưa có nền nào của {name(override_slug)} trong kho nên bot chọn tự động. "
                f"Gửi nền bằng /themnen {override_slug} rồi bấm Làm lại."
            )
    if not plan.choices:
        picked = store.pick(list(in_account), rng) if in_account else None
        why = BG_RANDOM_WHY if picked is not None else "kho chưa có nền của nhân vật nào trong acc"
        plan.choices.append(bg_choice(characters_svc, picked, why))
    return plan


# ----------------------------------------------------------------------------------------
# Tên file / tóm tắt Enka
# ----------------------------------------------------------------------------------------


def output_filename(code: "str | None", when: datetime) -> str:
    """acc-<ma>-<yyyymmdd-hhmm>.jpg — tuyệt đối không có UID."""
    stamp = when.strftime("%Y%m%d-%H%M")
    safe = re.sub(r"[^A-Za-z0-9\-]+", "", (code or "").replace("#", ""))
    return f"acc-{safe}-{stamp}.jpg" if safe else f"acc-{stamp}.jpg"


def _safe_part(text: "str | None") -> str:
    return re.sub(r"[^A-Za-z0-9\-]+", "", (text or "").replace("#", ""))


def saved_copy_name(mode: str, code: "str | None", uid: "str | None", when: datetime) -> str:
    """Tên bản sao trong thư mục output: acc-<ma>-<uid>-<mode>.jpg (mode = uid | chup | hoyolab).

    Thiếu mã hoặc UID thì bỏ phần đó; thiếu cả hai → acc-<yyyymmdd-hhmmss>-<mode>.jpg."""
    parts = [p for p in (_safe_part(code), _safe_part(uid)) if p]
    if not parts:
        parts = [when.strftime("%Y%m%d-%H%M%S")]
    return "acc-" + "-".join(parts) + f"-{mode}.jpg"


@dataclass
class UidArgs:
    uid: "str | None" = None
    code: "str | None" = None
    nen: "str | None" = None
    errors: "list[str]" = field(default_factory=list)


def parse_uid_args(args: str) -> UidArgs:
    """'/uid 887630784 ma 1116 nen columbina' → UidArgs. UID là từ đầu tiên (hoặc sau từ khoá 'uid')."""
    tokens = (args or "").split()
    out = UidArgs()
    if not tokens:
        return out
    first, rest = tokens[0], " ".join(tokens[1:])
    if first.lstrip("/").lower() in _CAPTION_KEYS:
        rest = " ".join(tokens)
        first = ""
    opts = parse_caption_options(rest)
    out.uid = normalize_uid(first) if first else opts.uid
    out.code, out.nen, out.errors = opts.code, opts.nen, list(opts.errors)
    return out


def format_enka_summary(profile: Any, characters_svc: CharacterService) -> str:
    name = characters_svc.display_name
    detailed = [a for a in profile.avatars if a.character and a.constellation is not None]
    no_detail = [a for a in profile.avatars if a.character and a.constellation is None]
    unknown = [a for a in profile.avatars if not a.character]
    head = f"Enka UID {profile.uid}: {profile.nickname or '(không tên)'}"
    bits = []
    if profile.adventure_rank is not None:
        bits.append(f"AR {profile.adventure_rank}")
    if profile.world_level is not None:
        bits.append(f"TG {profile.world_level}")
    lines = [head + (" — " + ", ".join(bits) if bits else "")]
    if detailed:
        lines.append("Có cung mệnh: " + ", ".join(f"{name(a.character)} C{a.constellation}" for a in detailed))
    if no_detail:
        lines.append(
            "Trưng bày nhưng Enka không trả chi tiết (chủ acc chưa bật 'hiện chi tiết'): "
            + ", ".join(name(a.character) for a in no_detail)
        )
    if unknown:
        lines.append(f"{len(unknown)} nhân vật trưng bày chưa có trong bảng nhân vật (cần cập nhật characters.json).")
    if not profile.avatars:
        lines.append("Tủ trưng bày đang trống nên Enka không trả nhân vật nào.")
    lines.append(
        "Nhân vật KHÔNG đặt trong Tủ trưng bày thì Enka không có cung mệnh: gõ /cm <ten> <so>, "
        "ví dụ /cm columbina 2 zibai 0."
    )
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------
# Chặn tài khoản / mật khẩu (bot KHÔNG BAO GIỜ nhận, dùng hay lưu tài khoản HoYoverse)
# ----------------------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[^\s@/]+@[^\s@]+\.[A-Za-z]{2,}")
_PASSWORD_WORD_RE = re.compile(
    r"(?<![\w])(mk|pass|passwd|password|pwd|mật\s*khẩu|mat\s*khau|matkhau)(?![\w])", re.IGNORECASE
)
_SEPARATORS = "-:|"
_SEP_SPLIT_RE = re.compile(r"[-:|]")
_TRIM = ".,;!?()[]{}<>\"'`"


def _non_numeric(part: str) -> bool:
    return bool(part) and not part.isdigit()


def looks_like_credentials(text: "str | None", safe_token: "Callable[[str], bool] | None" = None) -> bool:
    """Tin có giống tài khoản/mật khẩu không: email, chữ mk/pass/password/mật khẩu, hoặc "-", ":", "|" nằm
    giữa hai cụm không phải số (vd user:pass, tk|mk, abc - xyz). `safe_token`: cụm có "-" mà bot biết là
    hợp lệ (slug nhân vật như hu-tao, id nền) thì bỏ qua. Không bao giờ ghi/log nội dung."""
    if not text:
        return False
    if _EMAIL_RE.search(text) or _PASSWORD_WORD_RE.search(text):
        return True
    tokens = text.split()
    for tok in tokens:
        core = tok.strip(_TRIM)
        if not any(c in core for c in _SEPARATORS):
            continue
        parts = _SEP_SPLIT_RE.split(core)
        if not any(_non_numeric(a) and _non_numeric(b) for a, b in zip(parts, parts[1:])):
            continue
        if ":" not in core and "|" not in core and safe_token is not None and safe_token(core):
            continue
        return True
    for a, sep, b in zip(tokens, tokens[1:], tokens[2:]):
        if sep in ("-", ":", "|") and _non_numeric(a.strip(_TRIM)) and _non_numeric(b.strip(_TRIM)):
            return True
    return False


# ----------------------------------------------------------------------------------------
# Báo cáo ngắn chế độ HoYoLAB
# ----------------------------------------------------------------------------------------


def hoyolab_summary_vi(report: Any, uid: str) -> str:
    """Vài dòng tiếng Việt từ HoyolabReport (không chứa cookie hay dữ liệu đăng nhập)."""
    nick = getattr(report, "nickname", None) or "(không tên)"
    ar = getattr(report, "adventure_rank", None)
    wl = getattr(report, "world_level", None)
    head = f"HoYoLAB UID {uid}: {nick} — AR {ar if ar is not None else '?'}"
    if wl is not None:
        head += f", Cấp Thế Giới {wl}"
    drawn = list(getattr(report, "characters", None) or [])
    total = max(int(getattr(report, "characters_total", 0) or 0), len(drawn))
    weapons = list(getattr(report, "weapons", None) or [])
    expl = list(getattr(report, "explorations", None) or [])
    lines = [head, f"Đã vẽ {len(drawn)}/{total} nhân vật, {len(weapons)} vũ khí, {len(expl)} thẻ khám phá."]
    for line in getattr(report, "wish_lines", None) or []:
        if line.startswith("Ô banner") or line.startswith("Bỏ qua ô banner"):
            lines.append(line)
    unknown = getattr(report, "unknown_avatars", None) or []
    if unknown:
        lines.append(f"{len(unknown)} nhân vật chưa có trong bảng nhân vật (cần cập nhật characters.json).")
    missing = getattr(report, "missing_icons", None) or []
    if missing:
        lines.append(f"Thiếu {len(missing)} icon (đã vẽ ô thay thế).")
    return "\n".join(lines)
