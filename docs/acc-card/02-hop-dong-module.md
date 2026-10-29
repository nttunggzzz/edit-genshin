# gacard — Hợp đồng module v1

Ngày: 2026-10-21. Tài liệu này là **nguồn sự thật** cho các subagent làm song song. Đổi hợp đồng phải qua agent chính.

## 1. Những gì 21 ảnh mẫu cho thấy

Danh sách và nhận xét từng ảnh: `data/acc-card/reference/manifest.json` (git bỏ qua).

- **Bố cục KHÔNG cố định.** Có 3 kiểu:
  - `right` (12/21): hồ sơ AR ở góc trên phải, lưới nhân vật ở cột phải; panel cung mệnh ở cột trái; lưới vũ khí ở đáy giữa; dòng UID và các ô nhỏ nằm ngay trên lưới vũ khí; mã acc ở góc trên đối diện.
  - `left` (7/21): đối xứng của `right`; panel nằm ở cột phải.
  - `wide` (2/21): rất nhiều thứ (≥ 40 nhân vật, ≥ 3 hàng vũ khí, ≥ 8 panel): nhân vật ở cột trái cao hết ảnh, hồ sơ ở giữa trên, vũ khí ở giữa dưới, panel ở cột phải (có thể 2–3 cột).
- Số lượng thay đổi: nhân vật 8–40 (luôn **5 cột**), vũ khí 16–48 (**8 cột**, 2–4 hàng; nhiều quá thì 16 cột), panel 0–14 với chiều cao khác nhau (1–3 dòng cung mệnh trong một panel).
- **Màu khung đổi theo nền** (cyan, đỏ, hồng, tím, xanh lá). Chữ trắng có viền cùng tông.
- **Số cung mệnh do người dùng tự điền**: chữ số to, trắng viền xanh/tông khung; đặt ở góc dưới phải thẻ nhân vật, hoặc giữa panel.
- Ô nhỏ (`items`): đếm nhựa (trăng lưỡi liềm 4★), Mối Duyên (cầu hồng 5★), vật phẩm 5★ khác, ô **đếm lượt roll banner** (ảnh nhân vật + `< N >`, có thể có nhãn "Dehya (5 sao)"), ô **liên kết tài khoản** ("Tên Người Dùng / Chưa Liên Kết", "Google"), **chân dung** nhân vật lớn.
- Chữ tự do: mã acc (`#1043`, `CN#1076`), `CHECK BUILD UID <uid>`, dòng thêm (`DIE ID APPLE`), watermark `@sora kiwi`.
- Đầu ra: 16:9, 1920×1080 (tuỳ chọn 1280×720).
- Lưới nhân vật và vũ khí trong ảnh mẫu là **khối cắt nguyên từ game** (thấy cả thanh lọc "Cấp" ở #1056). Gacard sẽ tách **từng thẻ** rồi xếp lại, để ghép được nhiều ảnh cuộn và đè được số cung mệnh.

## 2. Gói và thư mục

```
src/gacard/
  __init__.py        (agent chính)
  models.py          A — đúng như mục 3
  theme.py           A — chọn màu khung từ nền / tuỳ chọn
  layout.py          A — tự xếp bố cục: right / left / wide
  compose.py         A — vẽ: nền, khung, mảnh, chữ, số đè
  export.py          A — JPG
  backgrounds.py     A — thư viện nền
  render.py          A — hàm công khai render_sheet()
  cli.py             A — gacard render / gacard sheet-check
  grid.py            B — tách thẻ trong một khối lưới
  bot/               C — Telegram (aiogram 3)
  enka.py            D — tra Enka theo UID
  characters.py      D — bảng nhân vật + điểm hype
scripts/acc_card/build_characters.py    D — sinh assets/acc-card/characters.json + hype.json mặc định
scripts/acc_card/extract_reference.py   B — cắt mảnh từ ảnh mẫu đã chỉnh → sheet giả lập
tests/acc_card/      mỗi agent một file test riêng: test_<module>.py
assets/acc-card/fonts/   A — font OFL (Be Vietnam Pro hoặc tương đương, có đủ dấu)
data/acc-card/       (git bỏ qua) reference/, fixtures/<ma>/, backgrounds/, out/, sessions/
```

Mỗi agent **chỉ sửa file của mình**. Cần thêm thư viện → báo agent chính, không tự sửa `pyproject.toml`.

## 3. Mô hình dữ liệu (models.py — A viết đúng như dưới)

```python
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
    character: str | None = None       # slug nhân vật (vd "columbina") nếu nhận ra được; dùng để gắn cung mệnh từ Enka và chọn nền

class Item(Piece):
    kind: ItemKind = ItemKind.OTHER

class AccountSheet(BaseModel):
    version: int = 1
    profile: Piece | None = None       # ô Hạng Mạo Hiểm / Cấp Thế Giới / Sinh Nhật
    characters: list[Card] = Field(default_factory=list)   # thứ tự đọc
    weapons: list[Card] = Field(default_factory=list)
    panels: list[Piece] = Field(default_factory=list)      # panel cung mệnh, chiều cao tuỳ ý
    items: list[Item] = Field(default_factory=list)
    # chế độ HoYoLAB: thẻ khám phá vùng < 40 % (% tăng dần); cột cạnh lưới vũ khí, đáy trùng đáy lưới,
    # thiếu chỗ thì bỏ thẻ cuối. Ảnh chụp: luôn rỗng.
    explorations: list[Piece] = Field(default_factory=list)

class RenderOptions(BaseModel):
    code: str | None = None            # "1043" → vẽ "#1043"; chuỗi có sẵn "#" (vd "CN#1076") vẽ nguyên văn
    uid: str | None = None             # → "CHECK BUILD UID <uid>"
    extra_lines: list[str] = Field(default_factory=list)   # vd "DIE ID APPLE"
    background: str | None = None      # slug nền; None = nền mặc định
    family: str = "auto"               # auto | right | left | wide
    frame_color: str | None = None     # "#RRGGBB"; None = tự chọn theo nền
    watermark: str | None = None
    height: int = 1080                 # 1080 hoặc 720; rộng = 16:9
```

Một **sheet** trên đĩa = thư mục có `sheet.json` (`AccountSheet.model_dump_json`) + các PNG mà nó trỏ tới.
Gợi ý `options.json` (RenderOptions) nằm cùng thư mục khi cần.

## 4. API công khai

```python
# render.py (A)
def render_sheet(sheet: AccountSheet, base_dir: Path, options: RenderOptions,
                 backgrounds: "BackgroundLibrary | None" = None) -> bytes   # JPEG bytes
def load_sheet(sheet_dir: Path) -> tuple[AccountSheet, Path]               # (sheet, base_dir)

# grid.py (B)
def split_cards(block: "PIL.Image.Image", *, expected_cols: int | None = None) -> list["PIL.Image.Image"]
    # tách các thẻ trong một khối lưới (nền thẻ cam/tím/xanh/đỏ...), thứ tự hàng→cột,
    # bỏ ô trống; không phụ thuộc màu khung bên ngoài.

# bot (C) chỉ gọi:  load_sheet, render_sheet, và một "Cropper" (Protocol) dưới đây
class Cropper(Protocol):
    def build_sheet(self, images: list[Path], work_dir: Path) -> AccountSheet: ...
```

Trước khi có ảnh gốc: bot dùng `FixtureCropper` (trả về một sheet giả lập có sẵn trong `data/acc-card/fixtures/`).

## 4b. Mục tiêu cuối (chủ dự án chốt 2026-10-21)

Người dùng gửi bot **6–7 ảnh gốc** (vũ khí, nhân vật, hồ sơ...) + **UID**. Bot:
1. cắt các mảnh từ ảnh gốc;
2. **tự tra Enka.Network theo UID** để lấy cung mệnh (và AR, tên, nhân vật trưng bày), rồi đè số cung mệnh lên đúng thẻ;
3. **tự chọn ảnh nền** trong kho nền mà người dùng đã gửi cho bot. Ưu tiên nhân vật **nổi bật, được săn đón nhất** có trong acc (vd Zibai, Columbina);
4. ghép thành ảnh hoàn chỉnh.

Giới hạn đã biết của Enka: chỉ trả chi tiết (gồm cung mệnh) cho các nhân vật **đang đặt trong Tủ trưng bày** và khi chủ acc bật "hiện chi tiết".
Nhân vật không trưng bày thì không có cung mệnh → người dùng điền tay (`/cm`). Bot phải báo rõ nhân vật nào lấy được, nhân vật nào không.

## 4c. Enka (D) — `src/gacard/enka.py`, `src/gacard/characters.py`

```python
class EnkaAvatar(BaseModel):
    avatar_id: int
    character: str | None          # slug, tra từ bảng nhân vật
    level: int | None
    constellation: int | None      # len(talentIdList); None nếu Enka không trả chi tiết

class EnkaProfile(BaseModel):
    uid: str
    nickname: str | None
    adventure_rank: int | None
    world_level: int | None
    avatars: list[EnkaAvatar]      # từ avatarInfoList (có chi tiết) ∪ showAvatarInfoList (chỉ level)
    fetched_at: datetime

class EnkaClient:
    def __init__(self, cache_dir: Path, user_agent: str, timeout: float = 10.0): ...
    async def fetch(self, uid: str) -> EnkaProfile      # tôn trọng "ttl" Enka trả về; cache trên đĩa
    # lỗi rõ ràng: UID sai (400), không tồn tại (404), bảo trì (424), rate limit (429), mạng → EnkaError(kind, message_vi)
```

`characters.py`: bảng nhân vật `assets/acc-card/characters.json` (avatar_id → slug, tên tiếng Việt, độ hiếm, nguyên tố, ngày ra mắt, tên file icon), sinh từ dữ liệu store công khai của Enka bằng `scripts/acc_card/build_characters.py`.

## 4d. Kho nền & chọn nền (A: `backgrounds.py`; C: lệnh bot)

- Người dùng gửi ảnh nền cho bot kèm caption tên nhân vật (`nen columbina`, hoặc lệnh `/themnen columbina`). Bot lưu vào
  `data/acc-card/backgrounds/<slug>/<id>.jpg` + cập nhật `data/acc-card/backgrounds/index.json`
  (`id, character, file, width, height, subject_side, added_at, used_count, last_used_at`). Nền < 1920×1080 → từ chối, báo lý do.
- `subject_side` (left/center/right): tự đoán bằng độ tương phản/độ chi tiết theo cột (đơn giản, có thể sai), người dùng sửa được.
- **Độ săn đón**: `assets/acc-card/hype.json` — danh sách slug có điểm (0–100), chủ dự án sửa được (lệnh `/uutien`).
  Mặc định: nhân vật 5★ giới hạn mới ra điểm cao hơn; danh sách "hot" do chủ dự án đưa vào đứng đầu.
- `BackgroundLibrary.pick(characters_in_account: list[str]) -> Background | None`: lấy nhân vật có trong acc **có nền trong kho** với điểm hype cao nhất; nhiều nền thì chọn nền ít dùng nhất / lâu chưa dùng nhất. Không có nhân vật khớp thì dùng nền mặc định. `/nen <ten>` luôn ghi đè lựa chọn tự động.
- Nguồn danh sách nhân vật có trong acc: Enka (trưng bày) ∪ thẻ đã nhận diện (`Card.character`). Nhận diện thẻ bằng so khớp icon sẽ làm ở P3, sau khi có ảnh gốc.

## 5. Quy tắc tự xếp bố cục (layout.py — A)

- Canvas logic 1920×1080, lề ngoài ≥ 24 px, khoảng cách giữa khối ≥ 16 px. Không khối nào chồng lên nhau, ngoại trừ chữ trang trí đặt lên nền.
- `family=auto` (sửa 2026-10-21 sau review, thay cho luật đếm cũ vì luật cũ đẩy #1043 và #1075 sang `wide` trái với ảnh mẫu):
  `wide` khi có ≥ 36 nhân vật (> 7 hàng). Nếu không, thử `right`/`left` theo `subject_side` của nền (nhân vật nền bên phải → `left`;
  trái/giữa/không rõ → `right`), giữ kiểu đó nếu vừa khít ở tỉ lệ 1.0 / 0.95 / 0.9, không vừa thì `wide`. Hằng số: `WIDE_MIN_CHARS`, `SIDE_SCALES` trong layout.py.
- Số đè: **không vẽ khi badge = 0** (C0); 0 vẫn là dữ liệu hợp lệ để báo cáo.
- **Cập nhật theo PSD của chủ dự án (2026-10-27, chi tiết `docs/acc-card/03-phan-tich-psd.md`)**: số trên thẻ cao ≈ 33% thẻ, sát góc dưới phải;
  số trên panel cao ≈ 0,145 × rộng panel, ở giữa. Viền khung 4 px vẽ ra ngoài, tô gradient dọc 2 màu; chữ trắng viền cùng gradient.
  Lề và khoảng cách co theo nội dung (64/36 → 44/26 → 24/16). Font trang trí: HYWenHei (`GACARD_DISPLAY_FONT`, không commit).
- Lưới nhân vật: 5 cột, số hàng = ceil(n/5). Kích thước thẻ co sao cho cả lưới vừa cột (dưới ô hồ sơ, đến lề dưới). Thẻ giữ nguyên tỉ lệ ảnh gốc.
- Lưới vũ khí: 8 cột; nếu nhiều hơn 4 hàng thì 16 cột. Co theo chiều rộng vùng giữa.
- Panel: xếp dọc, giữ tỉ lệ, cùng chiều rộng; tràn thì sang cột thứ hai.
- Ô nhỏ + dòng UID: một dải ngay trên lưới vũ khí. Thứ tự: portrait, banner_counter, resin, fate, other, account_link.
- Mã acc: góc trên phía **đối diện** khối hồ sơ. Watermark: giữa đáy.
- Số đè: chữ số đậm, trắng viền màu khung đậm; cao ≈ 45% chiều cao thẻ, ở góc dưới phải thẻ; với panel thì cao ≈ 60% chiều cao panel, ở giữa.
- Khung: viền 3 px màu khung quanh mỗi khối (hồ sơ, lưới, panel, item), bo góc 4–6 px.

## 6. Chất lượng bắt buộc

- `pytest tests/acc_card -q` xanh; không test nào gọi mạng hay Telegram thật.
- Render 1 sheet ≤ 3 giây; JPG 1920×1080 ≤ 2 MB; chữ tiếng Việt đủ dấu.
- Script in tiếng Việt: `sys.stdout.reconfigure(encoding="utf-8")`; mở file `encoding="utf-8"`.
- Không commit ảnh có UID thật hay ảnh nền; fixtures commit được chỉ là ảnh tổng hợp do code vẽ.
