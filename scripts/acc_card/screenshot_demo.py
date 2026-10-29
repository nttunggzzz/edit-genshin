"""Demo: ghep acc 887630784 tu cac manh cat THAT tu game (data/acc-card/inbox/887630784/).

Doc chars.webp (khoi nhan vat), weapons.png (khoi vu khi), profile.png (o AR), portrait.png (chan dung) va
background.jpg; tach the bang gacard.grid.split_cards, ghi sheet vao
data/acc-card/out/887630784/screenshot_sheet/ va render ra data/acc-card/out/887630784/screenshot.jpg.
Ket qua ghep tay cua chu du an de so sanh: data/acc-card/psd/dep.psd.

Chay:  .venv\\Scripts\\python.exe scripts\\acc_card\\screenshot_demo.py [--inbox DIR] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from gacard.grid import detect_cards, split_cards  # noqa: E402
from gacard.models import AccountSheet, Card, Item, ItemKind, Piece, RenderOptions  # noqa: E402
from gacard.render import render_sheet  # noqa: E402

UID = "887630784"
CODE = "1116"            # theo dep.psd: "#1116"
EXPECTED = {"chars": (5, 25), "weapons": (8, 16)}


def contact_sheet(rows: list[list[Image.Image]], bg=(40, 40, 48), gap=6) -> Image.Image:
    heights = [max(c.height for c in r) for r in rows if r]
    width = max(sum(c.width for c in r) + gap * (len(r) + 1) for r in rows if r)
    out = Image.new("RGB", (width, sum(heights) + gap * (len(heights) + 1)), bg)
    y = gap
    for r, h in zip([r for r in rows if r], heights):
        x = gap
        for c in r:
            out.paste(c, (x, y))
            x += c.width + gap
        y += h + gap
    return out


def split_block(path: Path, key: str, out_dir: Path, sub: str) -> tuple[list[Path], list[list[Image.Image]], list[str]]:
    cols, want = EXPECTED[key]
    block = Image.open(path).convert("RGB")
    boxes = detect_cards(block, expected_cols=cols)
    cards = split_cards(block, expected_cols=cols)
    # ve khung phat hien len khoi de debug
    dbg = block.copy()
    d = ImageDraw.Draw(dbg)
    for b in boxes:
        d.rectangle(b.box, outline=(255, 0, 255), width=2)
    dbg.save(out_dir / f"_{key}_detect.png")
    info = [f"{key}: block {block.size}, {len(cards)} the (ky vong {want}), "
            f"cot={sorted({b.col for b in boxes})}, hang={sorted({b.row for b in boxes})}"]
    sizes = sorted({c.size for c in cards})
    info.append(f"{key}: kich thuoc the {sizes}")
    info.append(f"{key}: pad_top={[b.pad_top for b in boxes if b.pad_top]}")
    (out_dir / sub).mkdir(parents=True, exist_ok=True)
    paths, rows = [], []
    last = None
    for i, (b, c) in enumerate(zip(boxes, cards), 1):
        p = out_dir / sub / f"{i:02d}.png"
        c.save(p)
        paths.append(Path(sub) / f"{i:02d}.png")
        if last != b.row:
            rows.append([])
            last = b.row
        rows[-1].append(c)
    return paths, rows, info


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inbox", type=Path, default=ROOT / "data/acc-card/inbox" / UID)
    ap.add_argument("--out", type=Path, default=ROOT / "data/acc-card/out" / UID)
    args = ap.parse_args()
    inbox, out = args.inbox, args.out
    sheet_dir = out / "screenshot_sheet"
    sheet_dir.mkdir(parents=True, exist_ok=True)

    char_paths, char_rows, info1 = split_block(inbox / "chars.webp", "chars", sheet_dir, "chars")
    weap_paths, weap_rows, info2 = split_block(inbox / "weapons.png", "weapons", sheet_dir, "weapons")
    for line in info1 + info2:
        print(line)

    Image.open(inbox / "profile.png").convert("RGBA").save(sheet_dir / "profile.png")
    Image.open(inbox / "portrait.png").convert("RGBA").save(sheet_dir / "portrait.png")

    sheet = AccountSheet(
        profile=Piece(image=Path("profile.png")),
        characters=[Card(image=p) for p in char_paths],
        weapons=[Card(image=p) for p in weap_paths],
        items=[Item(image=Path("portrait.png"), kind=ItemKind.PORTRAIT)],
    )
    (sheet_dir / "sheet.json").write_text(sheet.model_dump_json(indent=2), encoding="utf-8")
    options = RenderOptions(code=CODE, uid=UID, family="auto")
    (sheet_dir / "options.json").write_text(options.model_dump_json(indent=2), encoding="utf-8")

    contact_sheet(char_rows + weap_rows).save(out / "screenshot_contact.png")

    data = render_sheet(sheet, sheet_dir, options, background_file=inbox / "background.jpg")
    (out / "screenshot.jpg").write_bytes(data)
    print(f"da ghi {out / 'screenshot.jpg'} ({len(data) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
