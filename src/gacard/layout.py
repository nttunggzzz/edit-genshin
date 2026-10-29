"""Tự xếp bố cục (hợp đồng mục 5): right / left / wide.

Đầu vào chỉ là kích thước các mảnh + chữ; đầu ra là danh sách khối (Box) có toạ độ trên
canvas logic 1920×1080. Không vẽ gì ở đây nên test được không cần ảnh.

- right: hồ sơ + lưới nhân vật ở cột phải; panel cột trái (tràn → cột thứ hai cạnh cột phải,
  phía trên dải ô nhỏ); vũ khí ở đáy; dải ô nhỏ trên vũ khí dồn về phía cột chính; dòng UID canh trái
  theo mép trái lưới vũ khí, ngay trên nó (dep.psd); mã acc góc trên trái, ngang đỉnh ô hồ sơ.
- left: đối xứng gương của right.
- wide: nhân vật cột trái cao hết ảnh, hồ sơ giữa trên, vũ khí giữa dưới, panel ở bên phải
  (một cột cao hết ảnh + các cột phụ phía trên vũ khí); mã acc góc trên phải; dòng UID ngay trên
  lưới vũ khí, canh trái theo nó (như right), vướng thì về góc trên trái. Đỉnh khung hồ sơ ngang
  đỉnh khung lưới nhân vật.
- Thẻ khám phá (nếu có): một cột cạnh lưới vũ khí ở phía trống (wide: phải; right: trái; left: phải),
  cách lưới một khoảng khối, đáy trùng đáy lưới vũ khí; không đủ chỗ thì bỏ thẻ cuối danh sách.

Lề và khoảng cách co theo nội dung (docs/acc-card/03-phan-tich-psd.md): ở mỗi tỉ lệ s thử các bậc
SPACING_TIERS từ rộng tới chật; bậc rộng chỉ được giữ khi nó không bắt các khối nhỏ đi đáng kể.
Đáy cột chính, lưới vũ khí và cột panel trùng một đường; khoảng trống dư dồn lên trên.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from statistics import median

from gacard.theme import text_size, text_weight

log = logging.getLogger(__name__)

W, H = 1920, 1080
MARGIN = 24          # lề ngoài nhỏ nhất (bậc chật nhất)
GAP = 16             # khoảng cách nhỏ nhất giữa các khối (bậc chật nhất)
SPACING_TIERS = ((64, 36), (44, 26), (MARGIN, GAP))   # (lề, khoảng cách), thử theo thứ tự ở mỗi s
TIER_KEEP = 0.95     # bậc rộng hơn được giữ nếu tổng diện tích khối ≥ 95 % so với bậc chật nhất
FRAME_WIDTH = 4      # viền khung, vẽ ra NGOÀI rect khối (compose)
PAD = 3              # đệm trong khung lưới
CHAR_GAP_RATIO = 0.14    # khoảng giữa thẻ nhân vật = 0,14 × rộng thẻ (PSD ≈ 12 ở thẻ 86)
WEAPON_GAP_RATIO = 0.19  # khoảng giữa thẻ vũ khí = 0,19 × rộng thẻ (PSD 16–18 ở thẻ 83–89)
ITEM_GAP = 32        # khoảng giữa các ô nhỏ (không vượt khoảng cách khối của bậc)
PROFILE_GAP = 17     # hồ sơ → lưới nhân vật (dep.psd: 17), không vượt khoảng cách khối của bậc
CODE_Y = 38          # đỉnh chữ mã acc (mặc định / thấp nhất khi canh theo hồ sơ)
CODE_Y_MAX = 200     # canh mã theo đỉnh hồ sơ chỉ khi đỉnh chữ không thấp hơn mức này
CODE_ABOVE_PROFILE = 17   # dep.psd: đỉnh glyph mã cao hơn đỉnh ô hồ sơ 17 px
UID_ABOVE = 34       # đáy khung chữ UID (gồm viền) → đỉnh khối bên dưới; glyph cách ảnh ≈ 38 (dep.psd)
LINE_GAP = 6         # khoảng giữa các dòng chữ UID / dòng thêm

CHAR_COLS = 5
COLUMN_MAX = 525     # bề rộng tối đa cột nhân vật (s = 1); dep.psd: 523
PROFILE_MAX = 525    # bề rộng tối đa ô hồ sơ khi không có lưới nhân vật cùng cột
WEAPON_CELL_MAX = 100    # dep.psd: lưới 8 cột rộng 927 → ô ≈ 99
DEFAULT_WEAPON_COLS = 8  # lưới cũ / dep.psd
WEAPON_WIDEN = 0.48      # lưới vũ khí được nới thêm tối đa 48 % phần trống bên cạnh lưới 8 cột (chừa nền
                         # + chỗ cho cột thẻ khám phá; chủ dự án: bớt 1 cột so với 0,65 → acc 887630784 9×3)
EXPLORATION_ASPECT = 3.8     # rộng / cao thẻ khám phá (mặc định khi không có kích thước mảnh)
EXPLORATION_MIN_H = 72       # thẻ khám phá không thấp hơn (s = 1); không vừa thì bỏ thẻ cuối danh sách
EXPLORATION_MAX_H = 130
EXPLORATION_MAX_W = 440
WEAPON_CELL_MIN = 80     # nới thêm cột nhưng thẻ không nhỏ hơn ≈ 80 (mẫu chủ dự án: 80–100)
WEAPON_CELL_ROOMY = 0.95 # chế độ HoYoLAB ưu tiên số thẻ cho lưới phủ bề rộng mà thẻ ≥ 95 % cỡ tối đa
EMPTY_CELL_WEIGHT = 1.5  # ô trống ở hàng cuối xấu hơn khoảng nền trống cùng diện tích
PANEL_MAX, PANEL_MIN = 380, 200
ITEM_H = 100
PORTRAIT_FACTOR = 1.9    # chân dung cạnh các ô nhỏ khác: cao 1,9 × ITEM_H
PORTRAIT_SOLO_FACTOR = 2.4   # dải chỉ có chân dung: cao 2,4 × ITEM_H (dep.psd: 195×243)
DEFAULT_CARD_ASPECT = 0.82   # rộng / cao của thẻ nhân vật, vũ khí trong game

CODE_SIZE, UID_SIZE, EXTRA_SIZE, WM_SIZE = 32, 29, 26, 30
TEXT_STROKE, WM_STROKE = FRAME_WIDTH, 2

BLOCK_KINDS = {"profile", "characters", "weapons", "panel", "item", "exploration"}
TEXT_KINDS = {"code", "uid", "extra", "watermark"}
CELL_KINDS = {"character", "weapon"}
FAMILIES = ("right", "left", "wide")
WIDE_MIN_CHARS = 36          # ≥ 36 nhân vật (> 7 hàng) → wide
SIDE_SCALES = (1.0, 0.95, 0.9)  # right/left phải vừa ở các tỉ lệ này, không thì wide

Size = tuple[int, int]


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h

    def gap_to(self, other: Rect) -> int:
        """Khoảng cách nhỏ nhất theo trục (âm = chồng lên nhau)."""
        dx = max(other.x - self.right, self.x - other.right)
        dy = max(other.y - self.bottom, self.y - other.bottom)
        return max(dx, dy)

    def mirrored(self, width: int = W) -> Rect:
        return Rect(width - self.right, self.y, self.w, self.h)


@dataclass(frozen=True)
class Box:
    kind: str                  # xem *_KINDS
    rect: Rect
    index: int | None = None   # vị trí trong danh sách nguồn (thẻ, panel, item, dòng chữ)
    text: str | None = None
    size: int = 0              # cỡ chữ (khối chữ)
    stroke: int = 0


@dataclass(frozen=True)
class Spacing:
    """Một bậc lề / khoảng cách."""
    margin: int = MARGIN
    gap: int = GAP

    @property
    def item_gap(self) -> int:
        return min(ITEM_GAP, self.gap)


@dataclass
class LayoutSpec:
    """Kích thước mảnh (rộng, cao) theo thứ tự vẽ + chữ cần đặt."""
    profile: Size | None = None
    characters: list[Size] = field(default_factory=list)
    weapons: list[Size] = field(default_factory=list)
    panels: list[Size] = field(default_factory=list)
    items: list[Size] = field(default_factory=list)          # đã sắp theo thứ tự dải
    portrait_items: frozenset[int] = frozenset()               # chỉ số item là chân dung (to hơn)
    explorations: list[Size] = field(default_factory=list)    # thẻ khám phá, theo thứ tự ưu tiên
                                                               # (không đủ chỗ → bỏ từ cuối danh sách)
    code: str | None = None                                    # chữ đã định dạng, vd "#1043"
    uid_line: str | None = None                                # vd "CHECK BUILD UID 8503..."
    extra_lines: list[str] = field(default_factory=list)
    watermark: str | None = None
    subject_side: str | None = None                            # left | center | right | None


@dataclass
class Layout:
    family: str
    boxes: list[Box]
    scale: float = 1.0
    width: int = W
    height: int = H
    margin: int = MARGIN       # bậc lề / khoảng cách đã dùng
    gap: int = GAP
    weapon_plan: WeaponPlan | None = None   # số cột / ô vũ khí đã chọn (weapon_columns)

    def of(self, kind: str) -> list[Box]:
        return [b for b in self.boxes if b.kind == kind]

    def block_area(self) -> int:
        """Tổng diện tích khối để chọn bậc lề; thẻ khám phá (phần thêm, được bỏ bớt) không tính."""
        return sum(b.rect.w * b.rect.h for b in self.boxes if b.kind in BLOCK_KINDS and b.kind != "exploration")

    def problems(self, spec: LayoutSpec | None = None) -> list[str]:
        """Danh sách lỗi bố cục (rỗng = hợp lệ): tràn canvas, chồng khối, thiếu mảnh.
        Khối cách nhau ≥ `gap` (ô nhỏ với nhau ≥ min(ITEM_GAP, gap), hồ sơ → lưới nhân vật
        ≥ min(PROFILE_GAP, gap)); chữ cách khối đủ chỗ cho viền."""
        out: list[str] = []
        item_gap = Spacing(self.margin, self.gap).item_gap
        tops = [b for b in self.boxes if b.kind not in CELL_KINDS]
        for b in tops:
            r = b.rect
            lim = self.margin if b.kind in BLOCK_KINDS else 0
            if r.w <= 0 or r.h <= 0:
                out.append(f"{b.kind}#{b.index}: kích thước rỗng")
            if r.x < lim or r.y < lim or r.right > self.width - lim or r.bottom > self.height - lim:
                out.append(f"{b.kind}#{b.index}: tràn ra ngoài lề {r}")
        for i, a in enumerate(tops):
            for b in tops[i + 1:]:
                blocks = (a.kind in BLOCK_KINDS) + (b.kind in BLOCK_KINDS)
                if blocks == 2:
                    need = item_gap if a.kind == b.kind == "item" else self.gap
                    if {a.kind, b.kind} == {"profile", "characters"}:
                        need = min(PROFILE_GAP, self.gap)
                else:
                    need = FRAME_WIDTH + 1 if blocks == 1 else 1
                if a.rect.gap_to(b.rect) < need:
                    out.append(f"{a.kind}#{a.index} sát/chồng {b.kind}#{b.index}")
        for grid, cell in (("characters", "character"), ("weapons", "weapon")):
            frames = self.of(grid)
            for c in self.of(cell):
                if frames and not _inside(c.rect, frames[0].rect):
                    out.append(f"{cell}#{c.index} nằm ngoài khung lưới")
        if spec is not None:
            expect = {"character": len(spec.characters), "weapon": len(spec.weapons),
                      "panel": len(spec.panels), "item": len(spec.items),
                      "profile": int(spec.profile is not None)}
            for kind, n in expect.items():
                if len(self.of(kind)) != n:
                    out.append(f"thiếu {kind}: đặt {len(self.of(kind))}/{n}")
            if len(self.of("exploration")) > len(spec.explorations):   # thẻ khám phá: được bỏ bớt
                out.append("thừa exploration")
        return out


def _inside(inner: Rect, outer: Rect) -> bool:
    return (inner.x >= outer.x and inner.y >= outer.y
            and inner.right <= outer.right and inner.bottom <= outer.bottom)


# ---------------------------------------------------------------- công khai

def choose_family(spec: LayoutSpec, requested: str = "auto") -> str:
    """auto: wide khi quá nhiều nhân vật (> 7 hàng) hoặc kiểu cạnh bên không còn vừa ở cỡ gần
    đầy đủ; không thì right/left: khối dồn về phía đối diện nhân vật trên nền (mặc định right)."""
    if requested in FAMILIES:
        return requested
    if len(spec.characters) >= WIDE_MIN_CHARS:
        return "wide"
    side = "left" if spec.subject_side == "right" else "right"
    for s in SIDE_SCALES:
        layout, clean = _best_tier(spec, side, s)
        if clean:
            return side
    return "wide"


def compute_layout(spec: LayoutSpec, family: str = "auto") -> Layout:
    """Bố cục hợp lệ lớn nhất: thử tỉ lệ s giảm dần (mỗi s thử các bậc lề / khoảng cách)
    đến khi không còn chồng/tràn."""
    fam = choose_family(spec, family)
    last: Layout | None = None
    for step in range(0, 14):
        s = round(1.0 - 0.05 * step, 2)
        layout, clean = _best_tier(spec, fam, s)
        if clean:
            return layout
        last = layout or last
    log.warning("Không tìm được bố cục sạch cho %s; dùng bản thu nhỏ nhất", fam)
    return last or Layout(fam, [])


def weapon_grid_fit(spec: LayoutSpec, family: str = "auto") -> tuple[int, bool]:
    """(số cột, lưới "đẹp"?) mà bố cục chọn cho `spec` — để bên chọn vũ khí lấy đúng số thẻ cho hàng
    cuối đủ. "Đẹp" = phủ hết bề rộng vùng VÀ thẻ gần cỡ tối đa (≥ WEAPON_CELL_ROOMY × WEAPON_CELL_MAX·s,
    không thu nhỏ thẻ để nhét thêm cột). Không có vũ khí → (0, False). Một hàng: số cột = số thẻ."""
    layout = compute_layout(spec, family)
    plan = layout.weapon_plan
    if not plan:
        return 0, False
    return plan.cols, plan.fills and plan.cell >= WEAPON_CELL_ROOMY * WEAPON_CELL_MAX * layout.scale


def weapon_columns(spec: LayoutSpec, family: str = "auto") -> int:
    """Số cột lưới vũ khí bố cục chọn cho `spec` (0 nếu không có vũ khí)."""
    return weapon_grid_fit(spec, family)[0]


def _best_tier(spec: LayoutSpec, family: str, s: float) -> tuple[Layout | None, bool]:
    """Bậc rộng nhất còn vừa ở tỉ lệ s mà không làm khối nhỏ đi quá TIER_KEEP so với bậc chật
    nhất vừa. Trả về (layout, sạch?); không bậc nào sạch → (bản chật nhất dựng được, False)."""
    built = [_build(spec, family, s, Spacing(m, g)) for m, g in SPACING_TIERS]
    clean = [lay for lay in built if lay is not None and not lay.problems(spec)]
    if not clean:
        return next((lay for lay in reversed(built) if lay is not None), None), False
    ref = max(lay.block_area() for lay in clean)
    return next(lay for lay in clean if lay.block_area() >= TIER_KEEP * ref), True


# ---------------------------------------------------------------- tiện ích

def _aspect(sizes: list[Size], default: float = DEFAULT_CARD_ASPECT) -> float:
    vals = [w / h for w, h in sizes if w > 0 and h > 0]
    return median(vals) if vals else default


def _cell_gap(cell_w: int, ratio: float) -> int:
    return round(cell_w * ratio)


def _grid_dims(n: int, cols: int, cell_w: int, aspect: float, ratio: float) -> tuple[int, int, int, int]:
    """(rộng ngoài, cao ngoài, cao ô, số cột thực) của một lưới n thẻ; khoảng giữa thẻ = ratio × ô."""
    cols = max(1, min(cols, n))
    rows = math.ceil(n / cols)
    cell_h = max(1, round(cell_w / aspect))
    gap = _cell_gap(cell_w, ratio)
    return (2 * PAD + cols * cell_w + (cols - 1) * gap,
            2 * PAD + rows * cell_h + (rows - 1) * gap, cell_h, cols)


def _grid_boxes(kind: str, cell_kind: str, x: int, y: int, n: int, cols: int,
                cell_w: int, aspect: float, ratio: float) -> list[Box]:
    gw, gh, cell_h, cols = _grid_dims(n, cols, cell_w, aspect, ratio)
    gap = _cell_gap(cell_w, ratio)
    boxes = [Box(kind, Rect(x, y, gw, gh))]
    for i in range(n):
        r, c = divmod(i, cols)
        boxes.append(Box(cell_kind, Rect(x + PAD + c * (cell_w + gap),
                                         y + PAD + r * (cell_h + gap), cell_w, cell_h), i))
    return boxes


@dataclass(frozen=True)
class WeaponPlan:
    """Lưới vũ khí đã chọn: số cột, rộng ô, và lưới có phủ hết bề rộng vùng không (`fills`)."""
    cols: int
    cell: int
    fills: bool


def _plain_cell(n: int, cols: int, width: float, max_h: float, cap: int, aspect: float) -> int:
    r = WEAPON_GAP_RATIO
    return min(cap, _cell_for_width(width, cols, r), _cell_for_height(max_h, math.ceil(n / cols), aspect, r))


def _weapon_plan(n: int, width: float, max_h: float, s: float, aspect: float) -> WeaponPlan | None:
    """Chọn số cột lưới vũ khí: nới rộng hơn lưới 8 cột cũ một chút cho cân, KHÔNG kéo tới mép —
    chừa nền bên cạnh để nhân vật trên nền vẫn nhìn rõ.

    Bề rộng được dùng tối đa: limit = g8 + WEAPON_WIDEN × (width − g8), g8 = rộng lưới 8 cột cũ.
    Mỗi số cột c (1…n): ô = min(WEAPON_CELL_MAX·s, vừa limit, vừa max_h). Chỉ xét ô ≥ ngưỡng
    (WEAPON_CELL_MIN·s, hoặc 90 % ô lớn nhất khi chật). Ưu tiên: (1) hàng cuối đủ — phủ hết limit
    (dư < nửa bước thẻ) trước, rồi ít hàng, rồi ô to; (2) không có cách nào đủ hàng (số thẻ cố định ở
    chế độ ảnh chụp): diện tích trống ít nhất (khoảng dư trong limit + EMPTY_CELL_WEIGHT × ô trống ở
    hàng cuối), rồi ít hàng, rồi ô to. Chế độ HoYoLAB chọn số thẻ để lưới vừa đủ hàng vừa phủ limit."""
    if n <= 0:
        return None
    r = WEAPON_GAP_RATIO
    cap = round(WEAPON_CELL_MAX * s)
    c8 = min(n, DEFAULT_WEAPON_COLS)
    g8 = _grid_dims(n, c8, max(1, _plain_cell(n, c8, width, max_h, cap, aspect)), aspect, r)[0]
    limit = min(width, g8 + WEAPON_WIDEN * max(0, width - g8))
    opts = []
    for c in range(1, n + 1):
        rows = math.ceil(n / c)
        cell = _plain_cell(n, c, limit, max_h, cap, aspect)
        if cell < 8:
            continue
        gw, gh, cell_h, _ = _grid_dims(n, c, cell, aspect, r)
        side = max(0, limit - gw)
        fills = side < (cell + _cell_gap(cell, r)) / 2
        empty = rows * c - n
        waste = side * gh + EMPTY_CELL_WEIGHT * empty * cell * cell_h
        opts.append((c, rows, cell, fills, empty, waste))
    if not opts:
        return None
    best = max(o[2] for o in opts)
    floor = min(round(WEAPON_CELL_MIN * s), round(0.9 * best))
    ok = [o for o in opts if o[2] >= floor]

    def key(o):
        c, rows, cell, fills, empty, waste = o
        return (0, not fills, rows, -cell) if empty == 0 else (1, waste, rows, -cell)

    c, _, cell, fills, _, _ = min(ok, key=key)
    return WeaponPlan(c, cell, fills)


def _cell_for_width(width: float, cols: int, ratio: float) -> int:
    """Rộng ô lớn nhất để `cols` ô (kèm khoảng theo tỉ lệ) vừa `width`."""
    cell = math.floor((width - 2 * PAD) / (cols + (cols - 1) * ratio)) + 1
    while cell > 0 and 2 * PAD + cols * cell + (cols - 1) * _cell_gap(cell, ratio) > width:
        cell -= 1
    return cell


def _cell_for_height(height: float, rows: int, aspect: float, ratio: float) -> int:
    """Rộng ô lớn nhất để `rows` hàng thẻ (tỉ lệ `aspect`) vừa `height`."""
    cell = math.floor((height - 2 * PAD) / (rows / aspect + (rows - 1) * ratio)) + 1

    def tall(c: int) -> int:
        return 2 * PAD + rows * max(1, round(c / aspect)) + (rows - 1) * _cell_gap(c, ratio)

    while cell > 0 and tall(cell) > height:
        cell -= 1
    return cell


def _text_box(kind: str, text: str, size: int, x: int, y: int, stroke: int = TEXT_STROKE,
              index: int | None = None) -> Box:
    w, h = text_size(text, size, stroke, text_weight(kind))
    return Box(kind, Rect(x, y, w, h), index, text, size, stroke)


def _text_block(spec: LayoutSpec, s: float) -> list[tuple[str, str, int]]:
    """Các dòng UID + dòng thêm: (kind, text, size)."""
    k = max(s, 0.7)
    lines = []
    if spec.uid_line:
        lines.append(("uid", spec.uid_line, round(UID_SIZE * k)))
    lines += [("extra", t, round(EXTRA_SIZE * k)) for t in spec.extra_lines if t.strip()]
    return lines


def _stack_text(lines: list[tuple[str, str, int]], x: int, y: int, align: str = "left") -> list[Box]:
    """Xếp các dòng chữ từ (x, y) xuống dưới; align=right thì canh phải theo mép phải khối."""
    block_w, _ = _block_size(lines)
    boxes, extra_i = [], 0
    for kind, text, size in lines:
        idx = None
        if kind == "extra":
            idx, extra_i = extra_i, extra_i + 1
        b = _text_box(kind, text, size, x, y, index=idx)
        if align == "right":
            b = replace(b, rect=Rect(x + block_w - b.rect.w, y, b.rect.w, b.rect.h))
        boxes.append(b)
        y = b.rect.bottom + LINE_GAP
    return boxes


def _block_size(lines: list[tuple[str, str, int]]) -> Size:
    if not lines:
        return 0, 0
    sizes = [text_size(t, sz, TEXT_STROKE, text_weight(k)) for k, t, sz in lines]
    return max(w for w, _ in sizes), sum(h for _, h in sizes) + LINE_GAP * (len(sizes) - 1)


def _fill_panels(spec: LayoutSpec, width: int, columns: list[Rect], gap: int) -> list[Box] | None:
    """Xếp panel theo thứ tự vào các cột (mỗi cột canh đáy); None nếu không đủ chỗ."""
    heights = [max(1, round(width * h / w)) for w, h in spec.panels]
    boxes: list[Box] = []
    i = 0
    for col in columns:
        if i >= len(heights) or col.w < width:
            continue
        used, start = 0, i
        while i < len(heights) and used + heights[i] + (gap if i > start else 0) <= col.h:
            used += heights[i] + (gap if i > start else 0)
            i += 1
        y = col.bottom - used
        for j in range(start, i):
            boxes.append(Box("panel", Rect(col.x, y, width, heights[j]), j))
            y += heights[j] + gap
    return boxes if i == len(heights) else None


def _panel_widths(s: float) -> list[int]:
    hi = round(PANEL_MAX * s)
    lo = min(hi, round(PANEL_MIN * max(s, 0.6)))
    return list(range(hi, lo - 1, -10)) or [hi]


def _strip(spec: LayoutSpec, s: float, x0: int, x1: int, bottom: int, align: str,
           item_gap: int) -> list[Box]:
    """Dải ô nhỏ canh đáy tại `bottom`, trong [x0, x1], dồn trái hoặc phải."""
    if not spec.items:
        return []
    solo = all(i in spec.portrait_items for i in range(len(spec.items)))
    portrait = PORTRAIT_SOLO_FACTOR if solo else PORTRAIT_FACTOR
    factors = [portrait if i in spec.portrait_items else 1.0 for i in range(len(spec.items))]
    gaps = item_gap * (len(spec.items) - 1)
    unit = ITEM_H * s
    total = sum(f * w / h for f, (w, h) in zip(factors, spec.items)) * unit + gaps
    if total > x1 - x0:
        unit *= max(1, x1 - x0 - gaps) / (total - gaps)
    sizes = []
    for f, (w, h) in zip(factors, spec.items):
        ih = max(1, math.floor(unit * f))
        sizes.append((max(1, math.floor(ih * w / h)), ih))
    x = x0 if align == "left" else x1 - (sum(w for w, _ in sizes) + gaps)
    boxes = []
    for i, (iw, ih) in enumerate(sizes):
        boxes.append(Box("item", Rect(x, bottom - ih, iw, ih), i))
        x += iw + item_gap
    return boxes


def _place_strip_and_text(spec: LayoutSpec, s: float, sp: Spacing, x0: int, x1: int, base: int,
                          weapons: Rect | None, obstacles: list[Rect] = ()) -> list[Box]:
    """Ô nhỏ canh đáy ngay trên lưới vũ khí, dồn phải tới mép phải của nó (phía cột chính);
    khối chữ UID canh trái theo mép trái lưới vũ khí (glyph thẳng mép ảnh, viền chữ thẳng viền khung),
    đáy cách đỉnh vũ khí UID_ABOVE. Chữ mà vướng dải ô nhỏ: vẫn ngay trên vũ khí, canh phải tới
    (mép trái dải − gap), được lấn sang trái mép vũ khí tới x0 nếu không chạm `obstacles`; không vừa
    thì lên trên dải. Không có vũ khí:
    dải canh đáy tại `base`, dồn phải tới x1; chữ canh trái theo dải (hoặc dồn phải tới x1)."""
    g = sp.gap
    bottom = weapons.y - g if weapons else base
    anchor = weapons.right if weapons else x1
    items = _strip(spec, s, x0, anchor, bottom, "right", sp.item_gap)
    lines = _text_block(spec, s)
    if not lines:
        return items
    tw, th = _block_size(lines)
    if weapons:
        tx = weapons.x - TEXT_STROKE
    elif items:
        tx = items[0].rect.x - TEXT_STROKE
    else:
        tx = x1 - tw
    tx = max(x0 - TEXT_STROKE, min(tx, x1 - tw))
    t_bottom = weapons.y - round(UID_ABOVE * s) if weapons else base
    rect = Rect(tx, t_bottom - th, tw, th)
    if any(rect.gap_to(b.rect) < g for b in items):
        shifted = Rect(min(b.rect.x for b in items) - g - tw, rect.y, tw, th)
        clear = all(shifted.gap_to(o) >= g for o in [*obstacles, *(b.rect for b in items)])
        if shifted.x >= x0 - TEXT_STROKE and clear:
            rect = shifted
        else:
            rect = Rect(tx, min(b.rect.y for b in items) - g - th, tw, th)
    return items + _stack_text(lines, rect.x, rect.y)


def _code_and_watermark(spec: LayoutSpec, code_side: str, sp: Spacing,
                        profile: Rect | None = None) -> tuple[list[Box], int]:
    """Mã acc (góc trên `code_side`; glyph thẳng mép lề / cột panel, viền chữ thẳng viền khung)
    + watermark giữa đáy; trả về (boxes, đáy chữ mã). Có ô hồ sơ (`profile`) thì đỉnh glyph mã cao
    hơn đỉnh hồ sơ CODE_ABOVE_PROFILE (dep.psd), trong [CODE_Y, CODE_Y_MAX]; ngoài khoảng → CODE_Y."""
    boxes = []
    code_bottom = sp.margin
    if spec.code:
        w, _ = text_size(spec.code, CODE_SIZE, TEXT_STROKE, text_weight("code"))
        x = sp.margin - TEXT_STROKE if code_side == "left" else W - sp.margin - w + TEXT_STROKE
        y = CODE_Y
        if profile is not None:
            aligned = profile.y - CODE_ABOVE_PROFILE - TEXT_STROKE
            if CODE_Y <= aligned <= CODE_Y_MAX:
                y = aligned
        b = _text_box("code", spec.code, CODE_SIZE, x, y)
        boxes.append(b)
        code_bottom = b.rect.bottom
    if spec.watermark:
        w, h = text_size(spec.watermark, WM_SIZE, WM_STROKE, text_weight("watermark"))
        boxes.append(_text_box("watermark", spec.watermark, WM_SIZE, (W - w) // 2, H - 12 - h, WM_STROKE))
    return boxes, code_bottom


def _baseline(spec: LayoutSpec, sp: Spacing) -> int:
    """Đường đáy chung của các khối."""
    if not spec.watermark:
        return H - sp.margin
    _, h = text_size(spec.watermark, WM_SIZE, WM_STROKE, text_weight("watermark"))
    return min(H - sp.margin, H - 12 - h - 8)


# ---------------------------------------------------------------- các họ bố cục

def _build(spec: LayoutSpec, family: str, s: float, sp: Spacing = Spacing()) -> Layout | None:
    if family == "wide":
        return _build_wide(spec, s, sp)
    layout = _build_right(spec, s, sp)
    if layout is not None and family == "left":
        layout.boxes = _mirror(layout.boxes)
        layout.family = "left"
    return layout


def _mirror(boxes: list[Box]) -> list[Box]:
    """Lật gương bố cục nhưng giữ thứ tự đọc trái→phải trong lưới và dải ô nhỏ."""
    groups = {"character": "characters", "weapon": "weapons"}
    frames = {b.kind: b.rect for b in boxes if b.kind in groups.values()}
    items = [b.rect for b in boxes if b.kind == "item"]
    out = []
    for b in boxes:
        if b.kind in groups:
            f = frames[groups[b.kind]]
            dx = (W - f.right) - f.x
        elif b.kind == "item":
            lo, hi = min(r.x for r in items), max(r.right for r in items)
            dx = (W - hi) - lo
        else:
            out.append(replace(b, rect=b.rect.mirrored()))
            continue
        out.append(replace(b, rect=Rect(b.rect.x + dx, b.rect.y, b.rect.w, b.rect.h)))
    return out


def _main_column(spec: LayoutSpec, s: float, sp: Spacing, top: int, bottom: int,
                 right: int) -> list[Box]:
    """Hồ sơ + lưới nhân vật 5 cột, canh phải tại `right`, canh đáy tại `bottom`, không vượt `top`."""
    n = len(spec.characters)
    g = min(PROFILE_GAP, sp.gap)        # khoảng hồ sơ → lưới nhân vật
    avail = bottom - top
    if not n and spec.profile is None:
        return []
    pa = spec.profile[0] / spec.profile[1] if spec.profile else None
    ca = _aspect(spec.characters)
    r = CHAR_GAP_RATIO
    if n:
        rows = math.ceil(n / CHAR_COLS)
        # rộng cột Wc → ô = (Wc − 2PAD)/(5 + 4r); cao lưới = 2PAD + ô·(rows/ca + (rows−1)·r)
        k = (rows / ca + (rows - 1) * r) / (CHAR_COLS + (CHAR_COLS - 1) * r)
        coeff = k + (1 / pa if pa else 0)
        const = 2 * PAD * (1 - k) + (g if pa else 0)
        col_w = min(COLUMN_MAX * s, (avail - const) / coeff)
        cell = _cell_for_width(col_w, CHAR_COLS, r)
        while cell >= 8:                 # làm tròn có thể vượt vài px → bớt dần
            gw, gh, _, _ = _grid_dims(n, CHAR_COLS, cell, ca, r)
            if gh + (round(gw / pa) + g if pa else 0) <= avail:
                break
            cell -= 1
        if cell < 8:
            return []
        col_w = gw
    else:
        col_w = min(PROFILE_MAX * s, avail * pa)
        gh = 0
    ph = round(col_w / pa) if pa else 0
    total = ph + gh + (g if ph and gh else 0)
    y = bottom - total
    x = right - int(col_w)
    boxes: list[Box] = []
    if pa:
        boxes.append(Box("profile", Rect(x, y, int(col_w), ph)))
        y += ph + g
    if n:
        boxes += _grid_boxes("characters", "character", x, y, n, CHAR_COLS, cell, ca, r)
    return boxes


def _weapons_grid(spec: LayoutSpec, s: float, x0: int, x1: int, bottom: int, max_h: float,
                  align: str) -> tuple[list[Box], WeaponPlan | None]:
    """Lưới vũ khí canh đáy tại `bottom`, trải trong [x0, x1] (số cột theo _weapon_plan)."""
    wa = _aspect(spec.weapons)
    plan = _weapon_plan(len(spec.weapons), x1 - x0, max_h, s, wa)
    if plan is None:
        return [], None
    n, r = len(spec.weapons), WEAPON_GAP_RATIO
    gw, gh, _, _ = _grid_dims(n, plan.cols, plan.cell, wa, r)
    x = {"left": x0, "right": x1 - gw}.get(align, x0 + (x1 - x0 - gw) // 2)
    return _grid_boxes("weapons", "weapon", x, bottom - gh, n, plan.cols, plan.cell, wa, r), plan


def _exploration_stack(spec: LayoutSpec, s: float, g: int, grid: Rect | None, lo: int, hi: int,
                       side: str) -> list[Box]:
    """Cột thẻ khám phá cạnh lưới vũ khí, phía `side` (right: từ grid.right + g tới `hi`; left: từ
    `lo` tới grid.x − g). Cùng rộng, giữ tỉ lệ mảnh, cách nhau g; đáy cột = đáy lưới vũ khí (khung
    trùng khung). Cao thẻ = vừa chiều cao lưới / bề rộng còn lại, không quá EXPLORATION_MAX_H·s; thấp
    hơn EXPLORATION_MIN_H·s thì bỏ thẻ cuối danh sách (bên gọi xếp % khám phá tăng dần) rồi thử lại."""
    if not spec.explorations or grid is None:
        return []
    aspect = _aspect(spec.explorations, EXPLORATION_ASPECT)
    x0, x1 = (grid.right + g, hi) if side == "right" else (lo, grid.x - g)
    avail_w = min(x1 - x0, round(EXPLORATION_MAX_W * s))
    if avail_w <= 0:
        return []
    for k in range(len(spec.explorations), 0, -1):
        h = min((grid.h - (k - 1) * g) // k, math.floor(avail_w / aspect), round(EXPLORATION_MAX_H * s))
        if h >= round(EXPLORATION_MIN_H * s):
            break
    else:
        return []
    w = min(avail_w, round(h * aspect))
    x = x0 if side == "right" else x1 - w
    # cao bị chiều cao lưới giới hạn → phần dư làm tròn (< k px) chia cho các thẻ đầu, để đỉnh cột cũng
    # trùng đỉnh lưới (compose kéo mảnh đúng khung, lệch tỉ lệ ≤ 1 px)
    rem = grid.h - (k * h + (k - 1) * g)
    heights = [h + (1 if rem < k and i < rem else 0) for i in range(k)]
    y = grid.bottom - (sum(heights) + (k - 1) * g)
    boxes = []
    for i, hh in enumerate(heights):
        boxes.append(Box("exploration", Rect(x, y, w, hh), i))
        y += hh + g
    return boxes


def _build_right(spec: LayoutSpec, s: float, sp: Spacing) -> Layout | None:
    m, g = sp.margin, sp.gap
    base = _baseline(spec, sp)
    main = _main_column(spec, s, sp, m, base, W - m)
    profile = next((b.rect for b in main if b.kind == "profile"), None)
    texts, code_bottom = _code_and_watermark(spec, "left", sp, profile)
    x1 = min((b.rect.x for b in main), default=W - m + g) - g
    code_rect = texts[0].rect if spec.code else None

    widths = _panel_widths(s) if spec.panels else [0]
    for pw in widths:
        x0 = m + pw + g if spec.panels else m
        if x1 - x0 < 200:
            continue
        weapons, plan = _weapons_grid(spec, s, x0, x1, base, (base - m) * 0.5, "right")
        if spec.weapons and not weapons:
            continue
        w_rect = weapons[0].rect if weapons else None
        upper = _place_strip_and_text(spec, s, sp, x0, x1, base, w_rect,
                                      [b.rect for b in main + texts])
        # cột phải là cột chính → thẻ khám phá ở phía trống còn lại (trái lưới; left: gương → phải)
        boxes = main + weapons + upper + _exploration_stack(spec, s, g, w_rect, x0, x1, "left")
        if spec.panels:
            a_top = code_bottom + g if spec.code else m
            col_a = Rect(m, a_top, pw, base - a_top)
            b_top = m
            if code_rect and code_rect.right + g > x1 - pw:
                b_top = code_rect.bottom + g
            # cột B (cạnh cột chính) xuống tới khối thấp nhất phía dưới nó: chữ/ô nhỏ hoặc vũ khí
            bx = x1 - pw
            below = [b.rect.y for b in upper if b.rect.right + g > bx and b.rect.x < x1 + g]
            b_bottom = min(below + [w_rect.y if w_rect else base + g]) - g
            col_b = Rect(bx, b_top, pw, b_bottom - b_top)
            columns = [col_a] + ([col_b] if col_b.x >= x0 and col_b.h > 0 else [])
            panels = _fill_panels(spec, pw, columns, g)
            if panels is None:
                continue
            boxes += panels
        return Layout("right", texts + boxes, s, margin=m, gap=g, weapon_plan=plan)
    return None


def _build_wide(spec: LayoutSpec, s: float, sp: Spacing) -> Layout | None:
    """Dòng UID ưu tiên ngay trên lưới vũ khí, canh trái theo nó (như dep.psd); vướng thì góc trên trái."""
    if spec.weapons and _text_block(spec, s):
        layout = _build_wide_at(spec, s, sp, uid_above_weapons=True)
        if layout is not None and not layout.problems(spec):
            return layout
    return _build_wide_at(spec, s, sp, uid_above_weapons=False)


def _build_wide_at(spec: LayoutSpec, s: float, sp: Spacing, uid_above_weapons: bool) -> Layout | None:
    m, g = sp.margin, sp.gap
    base = _baseline(spec, sp)
    texts, code_bottom = _code_and_watermark(spec, "right", sp)
    lines = _text_block(spec, s)
    _, th = _block_size(lines)
    if lines and not uid_above_weapons:
        texts += _stack_text(lines, m - TEXT_STROKE, CODE_Y)
    chars_top = max(m, CODE_Y + th + g) if lines and not uid_above_weapons else m

    chars: list[Box] = []
    n = len(spec.characters)
    if n:
        ca = _aspect(spec.characters)
        rows = math.ceil(n / CHAR_COLS)
        r = CHAR_GAP_RATIO
        cell = min(_cell_for_width(COLUMN_MAX * s, CHAR_COLS, r),
                   _cell_for_height(base - chars_top, rows, ca, r))
        if cell >= 8:
            _, gh, _, _ = _grid_dims(n, CHAR_COLS, cell, ca, r)
            chars = _grid_boxes("characters", "character", m, base - gh, n, CHAR_COLS, cell, ca, r)
    x0 = chars[0].rect.right + g if chars else m
    line_boxes = [b.rect for b in texts if b.kind in ("uid", "extra")]
    text_right = max((r.right for r in line_boxes), default=0)
    text_bottom = max((r.bottom for r in line_boxes), default=0)

    def top_at(x: int) -> int:
        """Đỉnh vùng trên ở hoành độ x: dưới dòng UID nếu dòng chữ kéo dài tới đó."""
        return text_bottom + g if line_boxes and x < text_right + g else m

    widths = _panel_widths(s) if spec.panels else [0]
    for pw in widths:
        a_top = code_bottom + g if spec.code else m
        col_a = Rect(W - m - pw, a_top, pw, base - a_top)
        x1 = col_a.x - g if spec.panels else W - m
        if x1 - x0 < 300:
            continue
        weapons, plan = _weapons_grid(spec, s, x0, x1, base, (base - m) * 0.45, "left")
        if spec.weapons and not weapons:
            continue
        w_top = weapons[0].rect.y if weapons else base + g
        upper_bottom = w_top - g
        uid_boxes: list[Box] = []
        strip: list[Box] | None = None      # dải ô nhỏ đã đặt cạnh dòng UID (None = chưa)
        if uid_above_weapons and weapons and lines:
            w_rect = weapons[0].rect
            tw, _ = _block_size(lines)
            tx = max(x0 - TEXT_STROKE, min(w_rect.x - TEXT_STROKE, x1 - tw))
            if spec.items:
                # ô nhỏ (vd ô đếm banner) canh mép trái lưới vũ khí, ngay trên nó; dòng UID đứng bên phải dải
                # (như mẫu của chủ dự án), không chồng. Không đủ chỗ ngang → dải nằm trên dòng UID (cũ).
                beside = _strip(spec, s, w_rect.x, x1, w_rect.y - g, "left", sp.item_gap)
                text_x = beside[-1].rect.right + g
                if text_x + tw <= x1:
                    strip, tx = beside, text_x
            uid_boxes = _stack_text(lines, tx, w_rect.y - round(UID_ABOVE * s) - th)
            upper_bottom = min(b.rect.y for b in strip or uid_boxes) - g
        uid_limit = min((b.rect.y for b in uid_boxes), default=upper_bottom + g) - g
        uid_x0 = min((b.rect.x for b in uid_boxes), default=0)
        uid_x1 = max((b.rect.right for b in uid_boxes), default=0)

        def bottom_at(cx: int, width: int) -> int:
            """Đáy vùng trên cho một cột [cx, cx+width]: trên dòng UID nếu cột nằm ngang qua nó."""
            if uid_boxes and cx < uid_x1 + g and cx + width > uid_x0 - g:
                return uid_limit
            return w_top - g

        profile: list[Box] = []
        if spec.profile:
            pa = spec.profile[0] / spec.profile[1]
            def profile_w(top: int) -> int:
                return int(min(PROFILE_MAX * s, x1 - x0, (upper_bottom - top) * 0.5 * pa))

            # đỉnh khung hồ sơ ngang đỉnh khung lưới nhân vật (chủ dự án: thẳng hàng tuyệt đối), trừ khi
            # lưới nhân vật thấp (ít thẻ) tới mức hạ hồ sơ xuống sẽ làm nó nhỏ đi
            p_top = top_at(x0)
            if chars and chars[0].rect.y > p_top and profile_w(chars[0].rect.y) >= profile_w(p_top):
                p_top = chars[0].rect.y
            pw_ = profile_w(p_top)
            profile = [Box("profile", Rect(x0, p_top, pw_, round(pw_ / pa)))]
        if strip is None:
            strip = _strip(spec, s, x0, x1, upper_bottom, "left", sp.item_gap)
        left_right = max((b.rect.right for b in profile + strip), default=x0 - g)
        w_rect = weapons[0].rect if weapons else None
        boxes = chars + weapons + profile + strip + _exploration_stack(spec, s, g, w_rect, x0, x1, "right")
        if spec.panels:
            columns = [col_a]
            cx = col_a.x - g - pw
            while cx >= left_right + g:
                columns.append(Rect(cx, top_at(cx), pw, bottom_at(cx, pw) - top_at(cx)))
                cx -= g + pw
            panels = _fill_panels(spec, pw, columns, g)
            if panels is None:
                continue
            boxes += panels
        return Layout("wide", texts + uid_boxes + boxes, s, margin=m, gap=g, weapon_plan=plan)
    return None
