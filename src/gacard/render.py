"""API công khai: load_sheet() + render_sheet() (hợp đồng mục 4)."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
from PIL import Image

from gacard.backgrounds import Background, BackgroundError, BackgroundLibrary, estimate_subject_side
from gacard.compose import PieceImages, compose
from gacard.export import to_jpeg
from gacard.layout import Layout, LayoutSpec, compute_layout
from gacard.models import AccountSheet, ItemKind, Piece, RenderOptions
from gacard.theme import pick_theme

# thẻ tháng (PORTRAIT) rồi khung số roll (FATE) đứng cạnh nhau, sau đó tới ô banner (nhân vật, vũ khí)
ITEM_ORDER = [ItemKind.PORTRAIT, ItemKind.FATE, ItemKind.BANNER_COUNTER, ItemKind.RESIN,
              ItemKind.OTHER, ItemKind.ACCOUNT_LINK]


class SheetError(Exception):
    """Sheet hỏng/thiếu file; `message_vi` để báo người dùng."""

    def __init__(self, message_vi: str):
        super().__init__(message_vi)
        self.message_vi = message_vi


def load_sheet(sheet_dir: Path) -> tuple[AccountSheet, Path]:
    """Đọc <sheet_dir>/sheet.json (hoặc đường dẫn thẳng tới file .json) → (sheet, thư mục gốc ảnh)."""
    path = Path(sheet_dir)
    if path.is_dir():
        path = path / "sheet.json"
    if not path.is_file():
        raise SheetError(f"Không thấy file sheet: {path}")
    try:
        sheet = AccountSheet.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SheetError(f"sheet.json không hợp lệ: {exc}") from exc
    return sheet, path.parent


def load_options(sheet_dir: Path) -> RenderOptions:
    """options.json cạnh sheet.json nếu có, không thì mặc định."""
    path = Path(sheet_dir)
    path = (path if path.is_dir() else path.parent) / "options.json"
    if path.is_file():
        return RenderOptions.model_validate(json.loads(path.read_text(encoding="utf-8")))
    return RenderOptions()


def format_code(code: str | None) -> str | None:
    """'1043' → '#1043'; chuỗi đã có '#' (vd 'CN#1076') giữ nguyên."""
    code = (code or "").strip()
    if not code:
        return None
    return code if "#" in code else f"#{code}"


def sorted_items(sheet: AccountSheet) -> list:
    """Ô nhỏ theo thứ tự dải: portrait, fate, banner_counter, resin, other, account_link."""
    return sorted(sheet.items, key=lambda it: ITEM_ORDER.index(it.kind))


def iter_pieces(sheet: AccountSheet) -> list[tuple[str, Piece]]:
    """Mọi mảnh của sheet kèm nhãn, để kiểm tra file."""
    out: list[tuple[str, Piece]] = []
    if sheet.profile:
        out.append(("hồ sơ", sheet.profile))
    out += [(f"nhân vật {i + 1}", p) for i, p in enumerate(sheet.characters)]
    out += [(f"vũ khí {i + 1}", p) for i, p in enumerate(sheet.weapons)]
    out += [(f"panel {i + 1}", p) for i, p in enumerate(sheet.panels)]
    out += [(f"ô nhỏ {i + 1} ({p.kind})", p) for i, p in enumerate(sheet.items)]
    out += [(f"thẻ khám phá {i + 1}", p) for i, p in enumerate(sheet.explorations)]
    return out


def open_piece(base_dir: Path, piece: Piece) -> Image.Image:
    base = Path(base_dir).resolve()
    path = (base / piece.image).resolve()
    if not path.is_relative_to(base):
        raise SheetError(f"Đường dẫn mảnh nằm ngoài thư mục sheet: {piece.image}")
    if not path.is_file():
        raise SheetError(f"Thiếu file mảnh: {piece.image}")
    try:
        with Image.open(path) as img:
            return img.convert("RGBA")
    except OSError as exc:
        raise SheetError(f"Không mở được ảnh {piece.image}: {exc}") from exc


def account_characters(sheet: AccountSheet) -> list[str]:
    """Slug nhân vật nhận ra được trên thẻ (thứ tự đọc, không trùng)."""
    return list(dict.fromkeys(c.character for c in sheet.characters if c.character))


def choose_background(sheet: AccountSheet, options: RenderOptions,
                      backgrounds: BackgroundLibrary | None, *,
                      characters: list[str] | None = None,
                      rng: random.Random | None = None) -> Background | None:
    """options.background (mã nền hoặc slug nhân vật) > ngẫu nhiên trong các nền có nhân vật nằm trong acc
    (`characters`: toàn bộ nhân vật của acc nếu biết, mặc định nhân vật trên thẻ) > None (nền mặc định)."""
    if backgrounds is None:
        return None
    wanted = (options.background or "").strip()
    if wanted:
        try:
            return backgrounds.get(wanted)
        except BackgroundError:
            bg = backgrounds.pick([wanted.lower()], rng)
            if bg is None:
                raise BackgroundError(f"Kho chưa có nền cho {wanted!r}. Gửi ảnh nền kèm tên nhân vật trước.")
            return bg
    pool = characters if characters is not None else account_characters(sheet)
    return backgrounds.pick(pool, rng)


def spec_texts(options: RenderOptions) -> dict:
    """Các trường chữ của LayoutSpec theo tuỳ chọn (dùng chung cho render và bên chọn vũ khí)."""
    return dict(
        code=format_code(options.code),
        uid_line=f"CHECK BUILD UID {options.uid.strip()}" if options.uid and options.uid.strip() else None,
        extra_lines=[t.strip() for t in options.extra_lines if t.strip()],
        watermark=(options.watermark or "").strip() or None)


def build_spec(sheet: AccountSheet, pieces: PieceImages, options: RenderOptions,
               subject_side: str | None) -> LayoutSpec:
    def sizes(imgs: list[Image.Image]) -> list[tuple[int, int]]:
        return [im.size for im in imgs]

    items = sorted_items(sheet)
    return LayoutSpec(
        profile=pieces.profile.size if pieces.profile else None,
        characters=sizes(pieces.characters), weapons=sizes(pieces.weapons),
        panels=sizes(pieces.panels), items=sizes(pieces.items), explorations=sizes(pieces.explorations),
        portrait_items=frozenset(i for i, it in enumerate(items) if it.kind == ItemKind.PORTRAIT),
        subject_side=subject_side, **spec_texts(options))


def load_pieces(sheet: AccountSheet, base_dir: Path) -> PieceImages:
    items = sorted_items(sheet)
    p = PieceImages(
        profile=open_piece(base_dir, sheet.profile) if sheet.profile else None,
        characters=[open_piece(base_dir, c) for c in sheet.characters],
        weapons=[open_piece(base_dir, c) for c in sheet.weapons],
        panels=[open_piece(base_dir, c) for c in sheet.panels],
        items=[open_piece(base_dir, c) for c in items],
        explorations=[open_piece(base_dir, c) for c in sheet.explorations])
    for kind, seq in (("character", sheet.characters), ("weapon", sheet.weapons),
                      ("panel", sheet.panels), ("item", items)):
        for i, piece in enumerate(seq):
            if piece.badge is not None:
                p.badges[(kind, i)] = piece.badge
    for i, it in enumerate(items):
        if it.own_frame:
            p.own_frame.add(("item", i))
    return p


def refine_subject_side(img: Image.Image, side: str | None) -> str | None:
    """"center" (ước lượng thô) → "left"/"right" khi phần trên của ảnh (nơi có mặt) nghiêng rõ về một bên:
    so chi tiết + màu ở 30 % mép trái với 30 % mép phải (tỉ lệ ≥ 1,25). Bố cục nhờ đó dồn khối sang bên
    đối diện, không che mặt. Cân bằng thật sự (hoặc side khác center) thì giữ nguyên."""
    if side != "center":
        return side
    rgb = np.asarray(img.convert("RGB").resize((192, 108), Image.Resampling.BILINEAR), dtype=np.float32) / 255
    gray = rgb.mean(axis=2)
    detail = (np.abs(np.diff(gray, axis=1))[:-1, :] + np.abs(np.diff(gray, axis=0))[:, :-1])[:60]
    sat = (rgb.max(axis=2) - rgb.min(axis=2))[:-1, :-1][:60]
    col = (detail * (0.5 + sat)).sum(axis=0)
    n = len(col)
    left, right = float(col[:int(n * 0.3)].sum()), float(col[int(n * 0.7):].sum())
    if right >= 1.25 * left:
        return "right"
    if left >= 1.25 * right:
        return "left"
    return "center"


def render_image(sheet: AccountSheet, base_dir: Path, options: RenderOptions,
                 backgrounds: BackgroundLibrary | None = None, *, background_file: Path | None = None,
                 characters: list[str] | None = None,
                 rng: random.Random | None = None) -> tuple[Image.Image, Layout, Background | None]:
    """Ảnh 1920×1080 (chưa nén) + bố cục + nền đã dùng từ kho (None nếu không dùng kho)."""
    bg_meta: Background | None = None
    bg_img: Image.Image | None = None
    subject_side: str | None = None
    if background_file is not None:
        with Image.open(background_file) as im:
            bg_img = im.convert("RGB")
        subject_side = refine_subject_side(bg_img, estimate_subject_side(bg_img))
    else:
        bg_meta = choose_background(sheet, options, backgrounds, characters=characters, rng=rng)
        if bg_meta is not None and backgrounds is not None:
            bg_img = backgrounds.open_image(bg_meta)
            subject_side = refine_subject_side(bg_img, bg_meta.subject_side)
    pieces = load_pieces(sheet, base_dir)
    spec = build_spec(sheet, pieces, options, subject_side)
    layout = compute_layout(spec, options.family)
    theme = pick_theme(bg_img, options.frame_color)
    return compose(layout, pieces, theme, bg_img, subject_side), layout, bg_meta


def render_sheet(sheet: AccountSheet, base_dir: Path, options: RenderOptions,
                 backgrounds: BackgroundLibrary | None = None, *, background_file: Path | None = None,
                 characters: list[str] | None = None, rng: random.Random | None = None) -> bytes:
    """JPEG bytes (1920×1080 hoặc 1280×720, ≤ 2 MB). Nền lấy từ kho thì được đánh dấu đã dùng."""
    img, _, bg = render_image(sheet, base_dir, options, backgrounds, background_file=background_file,
                             characters=characters, rng=rng)
    data = to_jpeg(img, options.height)
    if bg is not None and backgrounds is not None:
        backgrounds.mark_used(bg.id)
    return data
