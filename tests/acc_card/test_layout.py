import random

import pytest

from gacard.layout import (BLOCK_KINDS, CHAR_GAP_RATIO, CHAR_SPLIT_THRESHOLD, CODE_ABOVE_PROFILE, CODE_Y,
                           EXPLORATION_MIN_H, FRAME_WIDTH,
                           GAP, ITEM_H, MARGIN, PAD, PROFILE_GAP, SPACING_TIERS, TEXT_STROKE, UID_ABOVE, H,
                           LayoutSpec, W, WEAPON_CELL_MAX, WEAPON_GAP_RATIO, WEAPON_MIN_ASPECT, WEAPON_SPAN,
                           _char_groups, _weapon_plan, choose_family, compute_layout, weapon_columns, weapon_grid_fit)

CARD = (140, 170)
EXPLORE = (760, 200)     # mảnh thẻ khám phá HoYoLAB


def _spec(rng: random.Random, **over) -> LayoutSpec:
    n_items = rng.randint(0, 6)
    items = [rng.choice([(100, 120), (170, 96), (240, 90), (180, 230)]) for _ in range(n_items)]
    spec = LayoutSpec(
        profile=(600, 222) if rng.random() < 0.85 else None,
        characters=[CARD] * rng.choice([0, 3, 8, 10, 25, 30, 31, 40, 45, 50, 51, 53, 65, 70, 100]),
        weapons=[CARD] * rng.choice([0, 5, 16, 24, 25, 32, 48, 70]),
        panels=[(420, 70 + 75 * rng.choice([1, 1, 2, 3])) for _ in range(rng.choice([0, 1, 3, 6, 8, 9, 11, 14, 16]))],
        items=items,
        portrait_items=frozenset(i for i, s in enumerate(items) if s == (180, 230)),
        code=rng.choice([None, "#1043", "CN#1076"]),
        uid_line=rng.choice([None, "CHECK BUILD UID 850363095"]),
        extra_lines=rng.choice([[], ["DIE ID APPLE"], ["DIE ID APPLE", "Hạng Mạo Hiểm ữ ỵ ẳ"]]),
        watermark=rng.choice([None, "@hoavu"]),
        subject_side=rng.choice([None, "left", "center", "right"]),
        explorations=[EXPLORE] * rng.choice([0, 0, 1, 3, 6]),
    )
    for k, v in over.items():
        setattr(spec, k, v)
    return spec


@pytest.mark.parametrize("seed", range(250))
def test_random_content_never_overlaps(seed):
    rng = random.Random(seed)
    spec = _spec(rng)
    family = rng.choice(["auto", "auto", "right", "left", "wide"])
    layout = compute_layout(spec, family)
    assert layout.problems(spec) == []


def test_problems_detects_overlap():
    spec = _spec(random.Random(1), characters=[CARD] * 10, weapons=[CARD] * 16)
    layout = compute_layout(spec)
    grid = layout.of("characters")[0]
    layout.boxes.append(type(grid)("panel", grid.rect, 0))
    assert any("chồng" in p for p in layout.problems())


@pytest.mark.parametrize("chars,weapons,panels,panel_h,side,expected", [
    (25, 16, 3, 145, None, "right"),
    (25, 16, 3, 145, "left", "right"),
    (25, 16, 3, 145, "center", "right"),
    (25, 16, 3, 145, "right", "left"),
    (30, 16, 11, 117, None, "right"),      # như mau-1043: 11 panel vẫn là right
    (35, 32, 2, 145, None, "right"),       # như #1075: 7 hàng nhân vật, 4 hàng vũ khí
    (31, 16, 3, 145, "right", "left"),
    (36, 16, 3, 145, None, "wide"),        # > 7 hàng nhân vật
    (40, 24, 8, 145, "right", "wide"),     # như #1061
    (30, 16, 16, 295, None, "wide"),       # panel không còn vừa cạnh bên
])
def test_family_auto(chars, weapons, panels, panel_h, side, expected):
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * chars, weapons=[CARD] * weapons,
                      panels=[(420, panel_h)] * panels, subject_side=side, code="#1")
    assert choose_family(spec) == expected
    layout = compute_layout(spec)
    assert layout.family == expected and layout.problems(spec) == []


def test_explicit_family_wins():
    spec = LayoutSpec(characters=[CARD] * 40, subject_side="right")
    assert choose_family(spec, "right") == "right"


def _cols(boxes):
    return len({b.rect.x for b in boxes})


@pytest.mark.parametrize("n_chars,rows", [(8, 2), (25, 5), (30, 6), (40, 8)])
def test_character_grid_has_five_columns(n_chars, rows):
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * n_chars, weapons=[CARD] * 16)
    layout = compute_layout(spec)
    cells = layout.of("character")
    assert _cols(cells) == 5
    assert len({b.rect.y for b in cells}) == rows
    frame = layout.of("characters")[0].rect
    assert frame.bottom <= H - MARGIN


@pytest.mark.parametrize("n,cols", [(16, 8), (24, 8), (32, 8), (33, 11), (48, 12)])
def test_weapon_grid_columns(n, cols):
    # số thẻ cố định: hàng cuối đủ trước (16/24/32 → 8); 33 = 11×3, 48 = 12×4 — dải ngang phủ vùng vũ khí
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 25, weapons=[CARD] * n)
    layout = compute_layout(spec)
    assert _cols(layout.of("weapon")) == cols == weapon_columns(spec) == layout.weapon_plan.cols
    if n in (16, 33, 48):                                            # lưới "đẹp" là dải ngang
        grid = layout.of("weapons")[0].rect
        assert layout.weapon_plan.good and grid.w >= WEAPON_MIN_ASPECT * grid.h


def _hoyolab_like(n_weapons: int) -> LayoutSpec:
    """Như ảnh HoYoLAB acc 887630784: 45 nhân vật (wide), hồ sơ gọn, mã + UID + watermark."""
    return LayoutSpec(profile=(874, 180), characters=[(228, 300)] * 45, weapons=[(248, 304)] * n_weapons,
                      code="#1116", uid_line="CHECK BUILD UID 887630784", watermark="@sora kiwi")


@pytest.mark.parametrize("n", [30, 36, 52])
def test_weapon_grid_uses_free_width_but_keeps_background_clear(n):
    # chủ dự án (ảnh 50 nhân vật): vũ khí là dải ngang kéo tới x ≈ 1620, không kéo tới mép phải
    spec = _hoyolab_like(n)
    layout = compute_layout(spec)
    assert layout.family == "wide" and layout.problems(spec) == []
    grid = layout.of("weapons")[0].rect
    chars = layout.of("characters")[0].rect
    cells = layout.of("weapon")
    cols = _cols(cells)
    assert cols >= 10 and len(cells) % cols == 0 and len({b.rect.y for b in cells}) <= 4
    assert all(70 <= b.rect.w <= WEAPON_CELL_MAX for b in cells)
    free = W - layout.margin - (chars.right + layout.gap)
    assert 0.75 * free <= grid.w <= WEAPON_SPAN * free                # phủ gần hết phần được dùng
    assert 1550 <= grid.right <= 1680 and W - layout.margin - grid.right > 150   # chừa nền bên phải
    assert grid.w >= WEAPON_MIN_ASPECT * grid.h and layout.weapon_plan.good


@pytest.mark.parametrize("n,cols,rows", [(16, 8, 2), (20, 10, 2), (24, 12, 2), (36, 12, 3)])
def test_few_weapons_make_a_wide_strip(n, cols, rows):
    # ít vũ khí → 2–3 hàng kéo ngang (rộng ≥ 3 × cao), không thành khối vuông
    spec = _hoyolab_like(n)
    layout = compute_layout(spec, "wide")
    assert layout.problems(spec) == []
    grid = layout.of("weapons")[0].rect
    cells = layout.of("weapon")
    assert (_cols(cells), len({b.rect.y for b in cells})) == (cols, rows)
    assert grid.w >= 3 * grid.h


def test_weapon_grid_fit_marks_wide_strips_as_good():
    # 27 = 9×3, 30 = 10×3 thẻ cỡ tối đa: dải ngang → "đẹp"; 32 cố định ở right → 8×4 khối vuông → không
    # "đẹp" (bên chọn vũ khí lấp lên / bớt xuống cho đủ hàng và đẹp)
    for n, cols in ((27, 9), (30, 10)):
        spec = _hoyolab_like(n)
        layout = compute_layout(spec)
        cells = layout.of("weapon")
        assert layout.problems(spec) == [] and _cols(cells) == cols and len({b.rect.y for b in cells}) == 3
        assert weapon_grid_fit(spec) == (cols, True)
    assert weapon_grid_fit(LayoutSpec(profile=(600, 222), characters=[CARD] * 25, weapons=[CARD] * 32)) == (8, False)


def test_wide_profile_frame_top_level_with_character_grid():
    for spec in (_hoyolab_like(27), _hoyolab_like(30),
                 LayoutSpec(profile=(600, 222), characters=[CARD] * 40, weapons=[CARD] * 24, code="#1")):
        layout = compute_layout(spec, "wide")
        profile = layout.of("profile")[0].rect
        chars = layout.of("characters")[0].rect
        assert profile.y - FRAME_WIDTH == chars.y - FRAME_WIDTH     # mép trên khung ngoài (gồm viền 4 px)
        assert layout.problems(spec) == []


def _explore_spec(n_weapons=27, n_explore=6, **over) -> LayoutSpec:
    spec = _hoyolab_like(n_weapons)
    spec.explorations = [EXPLORE] * n_explore
    for k, v in over.items():
        setattr(spec, k, v)
    return spec


@pytest.mark.parametrize("n_explore", [1, 2, 3, 4])
def test_exploration_stack_right_of_weapons_bottom_aligned(n_explore):
    spec = _explore_spec(n_explore=n_explore)
    layout = compute_layout(spec)
    assert layout.problems(spec) == []
    grid = layout.of("weapons")[0].rect
    cards = sorted(layout.of("exploration"), key=lambda b: b.index)
    assert 1 <= len(cards) <= n_explore and [b.index for b in cards] == list(range(len(cards)))
    assert len({b.rect.w for b in cards}) == 1 and len({b.rect.x for b in cards}) == 1
    assert cards[0].rect.x == grid.right + layout.gap                      # cách khung vũ khí một khoảng khối
    assert cards[-1].rect.bottom == grid.bottom                            # đáy khung = đáy khung
    for a, b in zip(cards, cards[1:]):
        assert b.rect.y - a.rect.bottom == layout.gap
    assert all(b.rect.h >= EXPLORATION_MIN_H for b in cards)
    assert max(b.rect.right for b in cards) <= W - layout.margin
    assert cards[0].rect.y >= grid.y                                       # không cao hơn lưới


def test_exploration_never_drops_cards_row_above_weapons_when_no_side_room():
    without = compute_layout(_explore_spec(n_explore=0))
    spec = _explore_spec(n_explore=6)
    layout = compute_layout(spec)
    cards = sorted(layout.of("exploration"), key=lambda b: b.index)
    grid = layout.of("weapons")[0].rect
    assert len(cards) == 6 and [b.index for b in cards] == list(range(6))   # đủ mọi thẻ, không bỏ thẻ nào
    assert layout.problems(spec) == []
    assert all(b.rect.bottom + layout.gap <= grid.y for b in cards)         # dải ngang TRÊN khối vũ khí
    assert layout.weapon_plan == without.weapon_plan                        # lưới vũ khí giữ nguyên
    assert not without.of("exploration")


@pytest.mark.parametrize("side,family", [(None, "right"), ("right", "left")])
def test_exploration_all_cards_in_side_families(side, family):
    # lưới vũ khí 1 hàng quá thấp cho cột thẻ cạnh nó → cả 3 thẻ nằm thành dải trên khối vũ khí
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 25, weapons=[CARD] * 6,
                      explorations=[EXPLORE] * 3, code="#1", subject_side=side)
    layout = compute_layout(spec)
    assert layout.family == family and layout.problems(spec) == []
    grid = layout.of("weapons")[0].rect
    cards = layout.of("exploration")
    assert len(cards) == 3 and all(b.rect.bottom + layout.gap <= grid.y for b in cards)
    narrow = LayoutSpec(profile=(600, 222), characters=[CARD] * 25, weapons=[CARD] * 16,
                        explorations=[EXPLORE] * 3, code="#1", subject_side=side)
    layout = compute_layout(narrow)
    assert layout.problems(narrow) == [] and len(layout.of("exploration")) == 3


def test_wide_single_block_mirrors_to_right_when_subject_left():
    base = dict(profile=(600, 222), characters=[CARD] * 40, weapons=[CARD] * 27, code="#1")
    normal = compute_layout(LayoutSpec(subject_side="right", **base), "wide")
    mirrored = compute_layout(LayoutSpec(subject_side="left", **base), "wide")
    assert normal.of("characters")[0].rect.x < W // 2 < mirrored.of("characters")[0].rect.x
    assert mirrored.problems(LayoutSpec(subject_side="left", **base)) == []


def test_weapon_plan_prefers_full_rows_and_stays_within_limit():
    # bề rộng truyền vào là bề rộng được dùng (đã nhân WEAPON_SPAN / trừ cột thẻ khám phá)
    for n in range(1, 70):
        plan = _weapon_plan(n, 1400, 450, 1.0, 0.8)
        assert plan is not None and plan.cell <= WEAPON_CELL_MAX
        gw = 2 * PAD + plan.cols * plan.cell + (plan.cols - 1) * round(plan.cell * WEAPON_GAP_RATIO)
        assert gw <= 1400
    assert _weapon_plan(36, 1400, 450, 1.0, 0.8).cols in (9, 12)    # chia hết 36
    assert _weapon_plan(0, 1400, 450, 1.0, 0.8) is None


def test_cards_keep_aspect_ratio():
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 30, weapons=[CARD] * 16)
    for b in compute_layout(spec).of("character"):
        assert abs(b.rect.w / b.rect.h - CARD[0] / CARD[1]) < 0.03


def test_panels_keep_aspect_and_same_width():
    panels = [(420, 145), (420, 220), (420, 295), (420, 145)]
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 25, weapons=[CARD] * 16, panels=panels)
    boxes = sorted(compute_layout(spec).of("panel"), key=lambda b: b.index)
    assert len({b.rect.w for b in boxes}) == 1
    for b, (w, h) in zip(boxes, panels):
        assert abs(b.rect.h - b.rect.w * h / w) <= 1


def test_many_panels_overflow_to_second_column():
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 30, weapons=[CARD] * 16,
                      panels=[(420, 117)] * 11, code="#1043")
    layout = compute_layout(spec, "right")
    assert layout.problems(spec) == []
    assert len({b.rect.x for b in layout.of("panel")}) == 2


def test_right_family_sides_and_texts():
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 25, weapons=[CARD] * 16,
                      panels=[(420, 145)] * 3, items=[(100, 120)], code="#1043",
                      uid_line="CHECK BUILD UID 1", watermark="@hoavu", subject_side="left")
    layout = compute_layout(spec)
    assert layout.family == "right"
    profile = layout.of("profile")[0].rect
    code = layout.of("code")[0].rect
    assert profile.x > W / 2 and code.right < W / 2           # mã acc đối diện hồ sơ
    assert layout.of("characters")[0].rect.x > W / 2
    assert all(b.rect.right < W / 2 for b in layout.of("panel"))
    wm = layout.of("watermark")[0].rect
    assert abs(wm.x + wm.w / 2 - W / 2) <= 1 and wm.bottom > H - MARGIN - 5
    weapons = layout.of("weapons")[0].rect
    uid = layout.of("uid")[0].rect
    item = layout.of("item")[0].rect
    assert uid.bottom <= weapons.y and item.bottom <= weapons.y - GAP + 1   # dải ngay trên vũ khí


def test_left_is_mirror_but_keeps_reading_order():
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 8, weapons=[CARD] * 16,
                      items=[(100, 120), (170, 96)], code="#1", subject_side="right")
    layout = compute_layout(spec)
    assert layout.family == "left"
    assert layout.of("profile")[0].rect.right < W / 2
    assert layout.of("code")[0].rect.x > W / 2
    chars = sorted(layout.of("character"), key=lambda b: b.index)
    assert chars[0].rect.x < chars[1].rect.x                   # đọc trái → phải
    assert chars[5].rect.x == chars[0].rect.x                  # hàng 2 bắt đầu ở cột trái
    items = sorted(layout.of("item"), key=lambda b: b.index)
    assert items[0].rect.x < items[1].rect.x


def test_wide_family_regions():
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 40, weapons=[CARD] * 24,
                      panels=[(420, 145)] * 8, code="#1061", uid_line="CHECK BUILD UID 1")
    layout = compute_layout(spec)
    assert layout.family == "wide"
    chars = layout.of("characters")[0].rect
    profile = layout.of("profile")[0].rect
    weapons = layout.of("weapons")[0].rect
    assert chars.x == layout.margin and chars.h > H * 0.75
    assert profile.x > chars.right and profile.y < weapons.y
    assert max(b.rect.right for b in layout.of("panel")) == W - layout.margin
    assert layout.of("code")[0].rect.x > W / 2
    uid = layout.of("uid")[0].rect                              # ngay trên vũ khí, canh trái theo nó
    assert uid.x == weapons.x - TEXT_STROKE and weapons.y - 40 < uid.bottom < weapons.y


@pytest.mark.parametrize("spec", [
    LayoutSpec(),
    LayoutSpec(profile=(600, 222)),
    LayoutSpec(characters=[CARD] * 3),
    LayoutSpec(weapons=[CARD] * 5, code="#1"),
    LayoutSpec(panels=[(420, 145)] * 2, watermark="@x"),
    LayoutSpec(items=[(100, 120)] * 3, uid_line="CHECK BUILD UID 1"),
    LayoutSpec(characters=[CARD] * 8, weapons=[CARD] * 16),
])
def test_sparse_content(spec):
    layout = compute_layout(spec)
    assert layout.problems(spec) == []


def test_all_blocks_inside_margins():
    spec = _spec(random.Random(5), characters=[CARD] * 40, weapons=[CARD] * 48, panels=[(420, 220)] * 14)
    layout = compute_layout(spec)
    for b in layout.boxes:
        if b.kind in BLOCK_KINDS:
            assert MARGIN <= b.rect.x and b.rect.right <= W - MARGIN
            assert MARGIN <= b.rect.y and b.rect.bottom <= H - MARGIN


def test_sparse_content_gets_widest_spacing_tier():
    # như fixture #1056 (không panel). Có thêm panel thì vũ khí (ô tối đa 100) bị panel ép ngang
    # → bậc chật hơn được chọn để vũ khí to hơn.
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 8, weapons=[CARD] * 16,
                      items=[(170, 96)], code="#1056")
    layout = compute_layout(spec)
    assert (layout.margin, layout.gap) == SPACING_TIERS[0]
    assert layout.problems(spec) == []


def test_dense_content_falls_back_to_tight_tier_before_shrinking():
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 35, weapons=[CARD] * 32,
                      panels=[(420, 145)] * 2, code="#1075", uid_line="CHECK BUILD UID 1")
    layout = compute_layout(spec)
    assert (layout.margin, layout.gap) != SPACING_TIERS[0] and layout.scale == 1.0   # ép lề trước khi thu nhỏ
    assert layout.problems(spec) == []


@pytest.mark.parametrize("panels", [3, 11])
def test_bottoms_aligned_and_code_on_panel_edge(panels):
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 30, weapons=[CARD] * 16,
                      panels=[(420, 117)] * panels, code="#1043", uid_line="CHECK BUILD UID 1")
    layout = compute_layout(spec, "right")
    chars = layout.of("characters")[0].rect
    weapons = layout.of("weapons")[0].rect
    lowest_panel = max(b.rect.bottom for b in layout.of("panel"))
    assert chars.bottom == weapons.bottom == lowest_panel == H - layout.margin
    code = layout.of("code")[0].rect
    # glyph thẳng mép cột panel (viền chữ ra ngoài như viền khung); đỉnh glyph ngang đỉnh hồ sơ − 17
    profile = layout.of("profile")[0].rect
    assert code.y == max(CODE_Y, profile.y - CODE_ABOVE_PROFILE - TEXT_STROKE)
    assert code.x + TEXT_STROKE == min(b.rect.x for b in layout.of("panel")) == layout.margin
    assert code.bottom + layout.gap <= min(b.rect.y for b in layout.of("panel") if b.rect.x == layout.margin)


@pytest.mark.parametrize("kind,ratio", [("character", CHAR_GAP_RATIO), ("weapon", WEAPON_GAP_RATIO)])
def test_card_gaps_proportional_to_card_width(kind, ratio):
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 25, weapons=[CARD] * 16)
    layout = compute_layout(spec)
    cells = sorted(layout.of(kind), key=lambda b: b.index)
    a, b = cells[0].rect, cells[1].rect
    assert b.x - a.right == round(ratio * a.w)
    frame = layout.of(kind + "s")[0].rect
    assert a.x - frame.x == PAD and a.y - frame.y == PAD


def _dep_spec(**over) -> LayoutSpec:
    """Như dep.psd (acc 887630784): 25 nhân vật, 16 vũ khí, 1 chân dung, hồ sơ, mã + UID."""
    spec = LayoutSpec(profile=(1307, 502), characters=[(186, 230)] * 25, weapons=[(186, 230)] * 16,
                      items=[(390, 486)], portrait_items=frozenset({0}), code="#1116",
                      uid_line="CHECK BUILD UID 887630784")
    for k, v in over.items():
        setattr(spec, k, v)
    return spec


def test_dep_psd_geometry():
    layout = compute_layout(_dep_spec(), "right")
    assert layout.problems(_dep_spec()) == [] and layout.scale == 1.0
    profile = layout.of("profile")[0].rect
    chars = layout.of("characters")[0].rect
    weapons = layout.of("weapons")[0].rect
    item = layout.of("item")[0].rect
    uid = layout.of("uid")[0].rect
    code = layout.of("code")[0].rect
    assert 515 <= chars.w <= 525 and abs(profile.w - chars.w) <= 2       # dep: 523
    assert chars.y - profile.bottom == PROFILE_GAP                      # dep: 17
    assert 900 <= weapons.w <= 960                                       # dep: 927
    assert chars.bottom == weapons.bottom
    assert abs(item.h - 2.4 * ITEM_H) <= 1 and item.right == weapons.right   # dep: 195×243, mép phải trùng
    assert uid.x + TEXT_STROKE == weapons.x and weapons.y - uid.bottom == UID_ABOVE
    assert uid.right < item.x
    assert code.y + TEXT_STROKE == profile.y - CODE_ABOVE_PROFILE and code.x + TEXT_STROKE == layout.margin


def test_uid_goes_above_strip_when_items_cover_weapons_left():
    items = [(240, 90)] * 4
    spec = _dep_spec(items=items, portrait_items=frozenset(), weapons=[(186, 230)] * 6)
    layout = compute_layout(spec, "right")
    assert layout.problems(spec) == []
    uid = layout.of("uid")[0].rect
    assert uid.bottom <= min(b.rect.y for b in layout.of("item"))


def test_left_family_uid_mirrors_to_weapons_outer_edge():
    spec = _dep_spec(subject_side="right")
    layout = compute_layout(spec)
    assert layout.family == "left"
    weapons = layout.of("weapons")[0].rect
    uid = layout.of("uid")[0].rect
    assert uid.right - TEXT_STROKE == weapons.right and uid.bottom < weapons.y


@pytest.mark.parametrize("side,family", [(None, "right"), ("right", "left")])
def test_uid_slides_beside_strip_above_weapons(side, family):
    # như #1075: chân dung + ô banner che phần cuối dòng UID → dòng UID vẫn ngay trên vũ khí,
    # đuôi canh tới mép trái dải − gap (lấn qua mép vũ khí), không bay lên trên dải
    spec = LayoutSpec(profile=(600, 222), characters=[CARD] * 35, weapons=[CARD] * 32,
                      panels=[(420, 145)] * 2, items=[(180, 230), (240, 90)],
                      portrait_items=frozenset({0}), code="#1075",
                      uid_line="CHECK BUILD UID 800528903", subject_side=side)
    layout = compute_layout(spec)
    assert layout.family == family and layout.problems(spec) == []
    weapons = layout.of("weapons")[0].rect
    uid = layout.of("uid")[0].rect
    items = [b.rect for b in layout.of("item")]
    assert weapons.y - uid.bottom == UID_ABOVE
    if family == "right":
        assert uid.right == min(r.x for r in items) - layout.gap and uid.x + TEXT_STROKE < weapons.x
    else:
        assert uid.x == max(r.right for r in items) + layout.gap and uid.right - TEXT_STROKE > weapons.right


# ------------------------------------------------------------------ lưới nhân vật tách đôi (> 50)
@pytest.mark.parametrize("n,groups", [(0, []), (30, [30]), (50, [50]), (51, [30, 21]), (55, [30, 25]),
                                      (65, [35, 30]), (70, [35, 35]), (71, [40, 31]), (100, [50, 50])])
def test_char_groups_split_by_rows(n, groups):
    assert _char_groups(n) == groups and sum(groups) == n
    if n > CHAR_SPLIT_THRESHOLD:
        assert groups[0] % 5 == 0 and groups[0] >= groups[1]          # khối đầu đủ hàng, không nhỏ hơn


def _split_spec(n_chars: int, **over) -> LayoutSpec:
    spec = LayoutSpec(profile=(874, 180), characters=[(228, 300)] * n_chars, weapons=[(248, 304)] * 60,
                      code="#TEST", uid_line="CHECK BUILD UID 800000001", watermark="@sora kiwi")
    for k, v in over.items():
        setattr(spec, k, v)
    return spec


def _blocks(layout):
    frames = sorted(layout.of("characters"), key=lambda b: b.rect.x)
    cells = [sorted((c for c in layout.of("character") if f.rect.x <= c.rect.x and c.rect.right <= f.rect.right),
                    key=lambda b: b.index) for f in frames]
    return cells, [f.rect for f in frames]


def test_seventy_characters_split_into_two_balanced_blocks():
    spec = _split_spec(70)
    layout = compute_layout(spec)
    assert layout.family == "wide" and layout.problems(spec) == []
    cells, frames = _blocks(layout)
    assert [len(c) for c in cells] == [35, 35]
    assert [b.index for b in cells[0]] == list(range(35)) and [b.index for b in cells[1]] == list(range(35, 70))
    assert all(_cols(c) == 5 for c in cells)
    left, right = frames
    assert left.x == layout.margin and right.right == W - layout.margin          # mép trái + mép phải
    assert (left.w, left.h) == (right.w, right.h) and left.bottom == right.bottom
    assert cells[0][0].rect.w == cells[1][0].rect.w                              # cùng cỡ thẻ
    grid = layout.of("weapons")[0].rect
    assert left.right < grid.x and grid.right < right.x                          # vũ khí ở giữa
    assert abs((grid.x - left.right) - (right.x - grid.right)) <= 2              # cân hai bên
    assert grid.w >= WEAPON_MIN_ASPECT * grid.h
    for kind in ("profile", "uid"):
        r = layout.of(kind)[0].rect
        assert left.right < r.x and r.right < right.x


def test_odd_rows_split_puts_larger_block_first():
    spec = _split_spec(65)
    layout = compute_layout(spec)
    assert layout.problems(spec) == []
    cells, frames = _blocks(layout)
    assert [len(c) for c in cells] == [35, 30] and frames[0].h > frames[1].h


def test_fifty_characters_stay_one_wider_column():
    # ≤ 50: một cột; 10 hàng → cột ≈ 377 px, mép phải ≈ 420 (chủ dự án: ≈ 425; 65 nhân vật một cột cũ ≈ 300)
    spec = _split_spec(50)
    layout = compute_layout(spec)
    assert layout.problems(spec) == [] and len(layout.of("characters")) == 1
    chars = layout.of("characters")[0].rect
    assert chars.w >= 370 and 400 <= chars.right <= 440
    split = compute_layout(_split_spec(65))
    assert all(f.rect.w >= 370 for f in split.of("characters"))                 # tách đôi: không bé lại


@pytest.mark.parametrize("extra", [
    {"explorations": [EXPLORE] * 3},
    {"items": [(181, 100)], "explorations": [EXPLORE] * 3},
    {"panels": [(420, 145)] * 3},
    {"subject_side": "right", "extra_lines": ["DIE ID APPLE"]},
])
def test_split_layout_keeps_texts_items_explorations_clear(extra):
    for n in (55, 70, 100):
        spec = _split_spec(n, **extra)
        layout = compute_layout(spec)
        assert layout.problems(spec) == [] and len(layout.of("characters")) == 2
        if spec.explorations:
            assert layout.of("exploration")
