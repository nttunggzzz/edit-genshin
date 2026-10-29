"""Mô hình dữ liệu gacard (hợp đồng v1, mục 3 của docs/acc-card/02-hop-dong-module.md)."""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field


class ItemKind(StrEnum):
    RESIN = "resin"                    # nhựa
    FATE = "fate"                      # Mối Duyên / vé quay
    BANNER_COUNTER = "banner_counter"  # ô < N > lượt roll banner
    ACCOUNT_LINK = "account_link"      # Tên Người Dùng / Google / Apple...
    PORTRAIT = "portrait"              # chân dung nhân vật lớn
    OTHER = "other"


class Piece(BaseModel):
    image: Path                        # PNG, tương đối với thư mục chứa sheet.json
    badge: int | None = None           # số đè (cung mệnh); None = không đè


class Card(Piece):                     # một thẻ nhân vật/vũ khí đã tách
    rarity: int | None = None          # 5, 4, 3... nếu biết
    character: str | None = None       # slug nhân vật (vd "columbina") nếu nhận ra được


class Item(Piece):
    kind: ItemKind = ItemKind.OTHER
    own_frame: bool = False            # khung đã nằm trong ảnh (mẫu PSD của chủ dự án) → không vẽ thêm khung theme


class AccountSheet(BaseModel):
    version: int = 1
    profile: Piece | None = None       # ô Hạng Mạo Hiểm / Cấp Thế Giới / Sinh Nhật
    characters: list[Card] = Field(default_factory=list)   # thứ tự đọc
    weapons: list[Card] = Field(default_factory=list)
    panels: list[Piece] = Field(default_factory=list)      # panel cung mệnh, chiều cao tuỳ ý
    items: list[Item] = Field(default_factory=list)
    # thẻ khám phá bản đồ (chế độ HoYoLAB: vùng < 40 %), % tăng dần; bố cục bỏ thẻ cuối nếu thiếu chỗ
    explorations: list[Piece] = Field(default_factory=list)


class RenderOptions(BaseModel):
    code: str | None = None            # "1043" → "#1043"; chuỗi có sẵn "#" (vd "CN#1076") vẽ nguyên văn
    uid: str | None = None             # → "CHECK BUILD UID <uid>"
    extra_lines: list[str] = Field(default_factory=list)   # vd "DIE ID APPLE"
    background: str | None = None      # slug nền; None = nền mặc định
    family: str = "auto"               # auto | right | left | wide
    frame_color: str | None = None     # "#RRGGBB"; None = cyan mặc định (hoặc GACARD_FRAME_COLOR); "auto" = theo nền
    watermark: str | None = None
    height: int = 1080                 # 1080 hoặc 720; rộng = 16:9
