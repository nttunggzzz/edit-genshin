"""Dòng lệnh: `gacard render <sheet_dir> ...` và `gacard sheet-check <sheet_dir>`."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PIL import Image

from gacard.backgrounds import BackgroundError, BackgroundLibrary
from gacard.render import SheetError, iter_pieces, load_options, load_sheet, open_piece, render_sheet

DEFAULT_BG_ROOT = Path("data/acc-card/backgrounds")

def _cmd_render(args: argparse.Namespace) -> int:
    sheet, base = load_sheet(Path(args.sheet_dir))
    options = load_options(Path(args.sheet_dir))
    updates = {k: v for k, v in {
        "code": args.code, "uid": args.uid, "family": args.family, "frame_color": args.frame_color,
        "watermark": args.watermark, "height": args.height, "background": args.bg}.items() if v is not None}
    if args.extra:
        updates["extra_lines"] = args.extra
    options = options.model_copy(update=updates)
    library = BackgroundLibrary(Path(args.bg_root)) if args.bg_root and Path(args.bg_root).is_dir() else None
    t0 = time.perf_counter()
    data = render_sheet(sheet, base, options, library,
                        background_file=Path(args.bg_file) if args.bg_file else None)
    elapsed = time.perf_counter() - t0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    w = round(options.height * 16 / 9)
    print(f"Đã lưu ảnh: {out} ({w}×{options.height}, {len(data) / 1024:.0f} KB, {elapsed:.2f} giây)")
    return 0


def _cmd_sheet_check(args: argparse.Namespace) -> int:
    sheet, base = load_sheet(Path(args.sheet_dir))
    errors = []
    for label, piece in iter_pieces(sheet):
        try:
            open_piece(base, piece)
        except SheetError as exc:
            errors.append(f"  - {label}: {exc.message_vi}")
    print(f"Sheet: {base}")
    print(f"Hồ sơ: {'có' if sheet.profile else 'không'} · Nhân vật: {len(sheet.characters)} · "
          f"Vũ khí: {len(sheet.weapons)} · Panel: {len(sheet.panels)} · Ô nhỏ: {len(sheet.items)}")
    badges = sum(1 for _, p in iter_pieces(sheet) if p.badge is not None)
    named = sum(1 for c in sheet.characters if c.character)
    print(f"Số đè (cung mệnh): {badges} · Thẻ đã nhận ra nhân vật: {named}")
    if errors:
        print(f"Lỗi ({len(errors)}):")
        print("\n".join(errors))
        return 1
    print("Sheet hợp lệ, đủ file ảnh.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gacard", description="Ghép ảnh mô tả acc Genshin")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("render", help="ghép sheet thành ảnh JPG")
    r.add_argument("sheet_dir")
    r.add_argument("--out", required=True, help="file JPG đầu ra")
    r.add_argument("--code", help='mã acc, vd 1043 hoặc "CN#1076"')
    r.add_argument("--uid")
    r.add_argument("--extra", action="append", help="dòng chữ thêm dưới UID (lặp được)")
    r.add_argument("--bg-file", help="ảnh nền lấy thẳng từ file")
    r.add_argument("--bg", help="mã nền hoặc slug nhân vật trong kho nền")
    r.add_argument("--bg-root", default=str(DEFAULT_BG_ROOT), help="thư mục kho nền")
    r.add_argument("--family", choices=["auto", "right", "left", "wide"])
    r.add_argument("--frame-color", help="#RRGGBB")
    r.add_argument("--watermark")
    r.add_argument("--height", type=int, choices=[1080, 720])
    r.set_defaults(func=_cmd_render)

    c = sub.add_parser("sheet-check", help="kiểm tra sheet.json và các file ảnh")
    c.add_argument("sheet_dir")
    c.set_defaults(func=_cmd_sheet_check)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (SheetError, BackgroundError) as exc:
        print(f"Lỗi: {exc.message_vi}", file=sys.stderr)
        return 1
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
