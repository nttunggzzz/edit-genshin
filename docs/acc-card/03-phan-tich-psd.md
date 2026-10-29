# gacard: phân tích 3 template PSD của chủ dự án

Ngày: 2026-10-27. Nguồn: `data/acc-card/psd/{nho,tam,alice}.psd` (git bỏ qua, tài sản riêng, không commit).
Công cụ: `scripts/acc_card/inspect_psd.py` ghi `data/acc-card/psd/<tên>.layers.json`, `<tên>/composite.png` và ảnh từng layer.
Mọi số đo dưới đây đã quy về **canvas 1920×1080**. PSD 4800×2700 nhân 0,4. alice 1920×1780 giữ nguyên.
Đơn vị là px, bbox `[x0, y0, x1, y1]`. Bbox của layer pixel **không** tính viền stroke nằm ngoài, nên khoảng trống nhìn thấy = khoảng cách bbox − 2 × viền.

## 1. Từng file

### nho.psd: kiểu `left`, khung hồng, 18 layer, không có group

| Layer | Loại | bbox @1920 | Ghi chú |
|---|---|---|---|
| Background | pixel | toàn canvas | nền trơn, bị che hết |
| HUTAO 2 | smart object | `[0,0,1920,1080]` | ảnh nền phủ kín canvas (cover), không chỉnh màu, không vignette |
| Layer 3 (hồ sơ) | pixel | `[57,178,544,355]` 486×177 | |
| Layer 1 (lưới nhân vật 5×5) | pixel | `[57,398,544,988]` 486×591 | **một ảnh chụp nguyên khối** từ game, nền giữa các thẻ là nền mờ của game |
| Layer 4/5/6 (3 ô nhỏ) | pixel | y `608–712`, x `582/700/815`, mỗi ô ≈84×104 | dãy ô canh trái theo mép trái lưới vũ khí |
| Layer 2 (vũ khí 2×8) | pixel | `[582,747,1420,988]` 838×242 | |
| Layer 7 (panel cung mệnh) | pixel | `[1458,507,1844,988]` 386×482 | một panel cao (màn cung mệnh đầy đủ) |
| Hu_Tao (ảnh nhân vật tách nền) | smart object | `[1590,505,1941,986]` | **ảnh nhân vật cắt nền đè lên nửa phải panel**, tràn ra ngoài canvas; DropShadow (9,6 px, lệch 3,6, 39 %, multiply) + OuterGlow #CB5454 (9,6 px, 35 %, screen) |
| CHECK BUILD UID … | text | `[957,673,1424,700]` | canh phải theo mép phải lưới vũ khí |
| CN#1076 | text | `[1721,180,1867,209]` | góc trên phải, đỉnh ngang đỉnh hồ sơ |
| 6, 1, 1, 2, 2, 2 | text | ví dụ «6» `[416,476,441,511]` | số cung mệnh trên thẻ nhân vật |

Mọi khối (kể cả chữ) dùng **cùng một layer effect**: Stroke Outside 3,2 px, tô gradient tuyến tính dọc «Red_05» `#B6359C` (đáy) → `#EF0A6A` (đỉnh), angle 90°, align with layer. Effect thứ hai (gradient «Pink_01») bị tắt.

### tam.psd: kiểu `right`, khung cyan, 24 layer, không có group

| Layer | bbox @1920 | Ghi chú |
|---|---|---|
| coum 3 (smart object) | toàn canvas | ảnh nền, cover |
| Layer 6 (hồ sơ) | `[1313,112,1794,286]` 481×174 | |
| Layer 2 (nhân vật 6×5) | `[1313,302,1794,1005]` 481×703 | khoảng cách hồ sơ → lưới chỉ 16 (6 hàng nên chật) |
| Layer 3 (vũ khí 3×8) | `[490,661,1267,1005]` 778×344 | |
| Layer 15/13/11/9/7 (5 panel) | x `75–441` (rộng 366); cao 81 / 112 / 91 / 122 / 170 | cột panel canh đáy ở 1004, khoảng cách bbox 37–50 |
| Layer 16/14/12/10/8 | 54×54, ở mép phải đáy mỗi panel | icon nhân vật (side icon) dán riêng, cũng có viền |
| keqing/nilou/Ayaka/ganyu skin (smart object) | lưới 2×2, mỗi ô ≈145×188, x `490–819`, y `227–635` | ô «chân dung» trang phục |
| Layer 5 (ô nhựa) | `[1194,475,1267,566]` 73×91 | canh phải theo mép phải lưới vũ khí |
| CHECK BUILD UID … | `[838,605,1266,628]` | canh phải theo mép phải lưới vũ khí, cách đỉnh vũ khí 33 |
| #1060 | `[76,38,188,67]` | góc trên trái, mép trái trùng cột panel (75) |
| 3, 5 | «3» `[239,406,276,460]` trong panel cao 112 | số cung mệnh **giữa panel** |

Effect chung: Stroke Outside 4,0 px, gradient dọc «Blue_16» `#209CFF` (đáy) → `#68E0CF` (đỉnh). Chữ trắng `#FCFAFA`.

### alice.psd: không phải thẻ acc mà là **bảng giá nạp**, 1920×1780, 62 layer

Có shape bo tròn lớn, viền gradient 3 màu `#FF8E44→#F91362→#000` ở chế độ PIN_LIGHT, chữ Times/GreatVibes tô Gradient Overlay, ngôi sao có OuterGlow. Template này **không liên quan bố cục acc**. Nó còn chứa thông tin liên hệ và tài khoản ngân hàng của chủ dự án, nên **không đưa vào repo hay tài liệu công khai**. Chỉ giữ lại một ý: chủ dự án thích viền gradient và glow.

### ref-22.jpg (mới nhất, 2000×1125, kiểu `left`, khung xanh)

Cấu trúc giống nho. Panel xếp thành 2 cột ở bên phải, panel cao nhất nằm ở cột ngoài cùng. **Ảnh nhân vật tách nền** (nhân vật của panel đó) đè lên panel cao, có viền ngoài ≈4 px màu khung quanh hình bóng nhân vật, cao ≈ bằng panel và tràn mép phải ≈60 px. Lưới đặc hơn nên lề và khoảng cách nhỏ lại (lề ≈55, khoảng cách khối 12–20).

## 2. Cách chủ dự án dựng một thẻ

1. Kéo ảnh nền vào dạng smart object, phủ kín canvas, không làm tối, không vignette, không gradient phủ.
2. Dán **ảnh chụp nguyên khối** từ game: hồ sơ, lưới nhân vật, lưới vũ khí, từng panel, từng ô nhỏ. Mỗi khối là một layer pixel, opacity 100 %, blend Normal, không mask.
3. Gán **một style duy nhất** (Stroke Outside, gradient dọc 2 màu) cho mọi khối **và** mọi chữ. Không có khối nền mờ phía sau, không đổ bóng khối.
4. Gõ chữ bằng HYWenHei-HEW: mã acc, `CHECK BUILD UID`, các số cung mệnh. Chữ trắng, viền chính là style khung.
5. Tuỳ chọn: thêm ảnh nhân vật tách nền đè lên panel cung mệnh lớn (nho: shadow + glow; ref-22: viền màu khung).

## 3. Style spec ở 1920×1080

### Khung (viền khối)

| Thuộc tính | Giá trị PSD | Ghi chú |
|---|---|---|
| Vị trí | **Outside**, nội dung không bị che | |
| Độ dày | 3,2 (nho) / 4,0 (tam) → **4 px** | |
| Màu | gradient tuyến tính dọc 2 màu, trải theo **từng khối**; màu đầu ở **đáy** | cyan: đáy `#209CFF`, đỉnh `#68E0CF`. hồng: đáy `#B6359C`, đỉnh `#EF0A6A` |
| Bo góc | góc trong vuông (ảnh không bị bo); góc ngoài bo = độ dày viền (≈4) do stroke outside | |
| Bóng / nền mờ | **không có** | |

### Lề và khoảng cách (thẻ thưa: nho, tam)

| Đại lượng | nho | tam | Đề xuất |
|---|---|---|---|
| Lề trái | 57 | 75 | 56–72 |
| Lề phải | 76 (khối), 53 (chữ mã) | 126 | ≥ 56 |
| Lề trên (khối) | 178 | 112 | không cố định, khối canh đáy |
| Lề trên (chữ mã) | 180 | 38 | ≈ 40 |
| Lề dưới | 92 | 75 | 72–90 |
| Khoảng cách giữa khối (bbox) | 34–42 | 16–50, phần lớn 37–46 | **≈ 36** (nhìn thấy ≈ 28); chật thì xuống 16 |
| Ô nhỏ ↔ ô nhỏ | 34 | – | 32 |
| UID ↔ đỉnh lưới vũ khí | 46 | 33 | ≈ 36 |

Thẻ đặc (ref-22) co lề còn ≈ 55/65/34 (trái/trên/dưới) và khoảng cách 12–20. Như vậy **khoảng cách thay đổi theo lượng nội dung**: còn chỗ thì để rộng, hết chỗ thì ép sát.

**Canh đáy chung**: các đáy hồ sơ+lưới nhân vật, lưới vũ khí, cột panel trùng một đường (988 ở nho, 1004 ở tam). Khoảng trống dư dồn lên **phía trên**, nơi đặt chữ mã.

### Kích thước khối

| Khối | PSD | Ghi chú |
|---|---|---|
| Cột chính (hồ sơ = lưới nhân vật) | rộng **481–486** | hồ sơ và lưới cùng rộng, cùng x |
| Hồ sơ | 481–486 × 174–177 (tỉ lệ ≈ 2,76) | |
| Thẻ nhân vật | ≈ 86 × 108, khoảng giữa thẻ ngang 12 / dọc 10, đệm mép ≈ 2–3 | khoảng giữa thẻ ≈ 0,14 × rộng thẻ |
| Thẻ vũ khí | 83–89 rộng, khoảng ngang 16–18, bước hàng 117–127 | khoảng ≈ 0,2 × rộng thẻ |
| Lưới vũ khí 8 cột | 778–838 rộng | |
| Panel | rộng **366–386**, cao theo ảnh | |
| Ô nhỏ (nhựa, Mối Duyên…) | 73–84 × 91–104 | |
| Chân dung (skin) | 142–149 × 185–192 (≈ 1,9 × ô nhỏ) | |
| Icon nhân vật trong panel | 54 × 54, góc dưới phải panel | |

### Chữ (font HYWenHei-HEW, chữ trắng `#FFFFFF`/`#FCFAFA`, viền = style khung 3,2–4 px outside)

| Chữ | Chiều cao chữ hoa | Rộng | Cỡ font ước tính @1920 | Vị trí |
|---|---|---|---|---|
| `CHECK BUILD UID 9 số` | 23–28 | 428–467 | **≈ 29** | ngay trên lưới vũ khí, canh phải theo mép vũ khí |
| Mã acc `#1060` / `CN#1076` | 29 | 112 / 146 | **≈ 36** | góc trên phía đối diện cột chính; mép ngoài trùng mép cột panel bên dưới |
| Số trên thẻ nhân vật | 34–37 (= **0,32–0,34 × cao thẻ**) | – | 42–46 | **sát góc dưới phải thẻ**: mép phải chữ cách mép thẻ −2…0, đáy chữ trùng đáy thẻ; chỉ viền tràn ra |
| Số trên panel | 53–54 (= **0,145 × rộng panel**, 0,43–0,48 × cao panel) | – | ≈ 67 | giữa panel theo ngang; theo dọc cao hơn tâm 5–10 |

nho kéo giãn chữ (scale ngang 1,26, dọc 1,39), tam không giãn. Coi kéo giãn là tuỳ hứng, không cần mô phỏng. Không có glow hay shadow trên chữ.

### Nền lưới

Lưới trong PSD là ảnh chụp nguyên khối, nên giữa các thẻ là **nền mờ đục của game** (frosted glass ăn theo cảnh). Màu trung vị đo ở khe thẻ: nho `#713833` (đỏ nâu), tam `#1B509C` (xanh), vũ khí `#38435C` / `#49697F`. Lớp nền này không trong suốt.

## 4. So với code hiện tại, xếp theo mức ảnh hưởng thị giác

| # | Khác biệt | Hiện tại | PSD | Đề xuất cụ thể |
|---|---|---|---|---|
| 1 | Chữ mã và UID quá to | `CODE_SIZE=56` (chữ hoa ≈45, «#1075» rộng 188), `UID_SIZE=40` (rộng 613) | mã: chữ hoa 29, rộng 112; UID: chữ hoa 23–28, rộng 430–470 | `CODE_SIZE = 36`, `UID_SIZE = 29`, `EXTRA_SIZE = 26`, giữ `TEXT_STROKE = 4` |
| 2 | Bố cục sát mép, khối dính nhau | `MARGIN=24`, `GAP=16`, ô to tối đa | lề 57–126, khoảng cách ≈36, khối nhỏ hơn | Thêm bậc khoảng cách: trong `compute_layout`, ở mỗi `s` thử `(MARGIN, GAP)` theo thứ tự `(64, 36) → (44, 26) → (24, 16)` rồi mới giảm `s`. `problems()` dùng giá trị của bậc đang thử |
| 3 | Cột chính, vũ khí, panel to hơn bản tay | `COLUMN_MAX=540`, `WEAPON_CELL_MAX=108`, `PANEL_MAX=340`, `ITEM_H=118` | 486 / 83–89 / 366–386 / 91–104 | `COLUMN_MAX = 490`, `WEAPON_CELL_MAX = 90`, `PANEL_MAX = 380`, `ITEM_H = 100`, `PORTRAIT_FACTOR = 1.9`, `ITEM_GAP = 32` |
| 4 | Khung một màu, vẽ đè lên mép ảnh | `FRAME_WIDTH=3` vẽ **bên trong** rect, màu `theme.frame` phẳng | 4 px **outside**, gradient dọc 2 màu | `FRAME_WIDTH = 4`. Vẽ viền vào vùng `rect` nới ra 4 px. Tô gradient: mask vành × ảnh gradient dọc (đáy→đỉnh) tính theo **từng khối**. `Theme` thêm `frame_top`, `frame_bottom`: cyan `#68E0CF`/`#209CFF`, hồng `#EF0A6A`/`#B6359C`. Các màu khác chưa có trong PSD, tạm suy bằng ±20° hue quanh màu nhấn (đỉnh sáng hơn, đáy đậm hơn) |
| 5 | Viền chữ và số đậm hơn khung | `outline=_darker(frame,.62)`, `badge=_darker(frame,.78)` | viền chữ **chính là** gradient khung | Viền chữ, mã, UID, số dùng cùng gradient khung: vẽ chữ viền vào layer mask rồi tô gradient theo bbox chữ, sau đó vẽ lớp fill trắng lên trên. Không gradient được thì dùng màu giữa `mix(frame_top, frame_bottom)`, **không làm tối** |
| 6 | Số trên thẻ hơi to và tràn | cao 0,36 × thẻ, lệch phải `+stroke+2` | 0,32–0,34 × thẻ, glyph sát mép phải và đáy | `_badge`: `target = r.h * 0.33`; `x = r.right - glyph_w` (không tính viền), `y = r.bottom - glyph_h`, chỉ viền tràn ra; `stroke = FRAME_WIDTH` (4) |
| 7 | Số trên panel | `min(r.h, r.w/3.6) * 0.60` (≈61) | chữ hoa ≈ 0,145 × rộng panel (53), cao hơn tâm 5–10 | `target = min(r.w * 0.145, r.h * 0.5)`, `y` = tâm − 0,08 × chiều cao chữ |
| 8 | Có bóng mềm sau mọi khối | `GaussianBlur(7)`, alpha 110 | không có | Bỏ bóng, hoặc hạ alpha ≤ 40 để tách nền sáng |
| 9 | Nền sau lưới bán trong suốt màu khung | `fill = deep(frame), alpha 150` | nền mờ đục của game | «Frosted»: cắt vùng nền sau lưới, `GaussianBlur(24)`, nhân độ sáng 0,45, trộn 25 % màu khung đậm, alpha 235. Áp cho cả lưới nhân vật và vũ khí |
| 10 | Khoảng giữa thẻ trong lưới | `CELL_GAP=6`, `PAD=6` | 12 (nhân vật) / 16–18 (vũ khí), đệm 2–3 | Tính theo bề rộng thẻ: `CELL_GAP_CHAR = round(0.14*cell)` (≈12), `CELL_GAP_WEAPON = round(0.19*cell)` (≈16), `PAD = 3`. Cần sửa `_grid_dims`, `_cell_for_*` vì khoảng cách phụ thuộc ô |
| 11 | Cột chính không canh đáy | `y = top + (avail-total)//4` | đáy cột chính = đáy vũ khí = đáy panel | `_main_column`: `y = bottom - total` (canh đáy). Chữ mã vẫn ở trên cùng |
| 12 | Vị trí chữ mã | `x = MARGIN+40`, `y = MARGIN+16` | mép ngoài trùng cột panel bên dưới, y ≈ 38 | `x` = mép ngoài cột panel (hoặc `MARGIN`), `y = 38` |
| 13 | Bo góc nội dung | mask bo `RADIUS=5` lên ảnh hồ sơ/panel/ô nhỏ | ảnh vuông, chỉ góc ngoài viền bo ≈4 | `RADIUS = FRAME_WIDTH` cho đường viền ngoài, **bỏ** bo mask nội dung (`rounded=False`) |
| 14 | UID | đặt bên trái dải ô nhỏ | canh theo mép lưới vũ khí, cách 33–46 phía trên | Giữ cách hiện tại, chỉ đổi khoảng UID↔vũ khí thành `GAP` của bậc đang dùng. Cân nhắc canh UID theo mép vũ khí **xa cột chính** khi dải ô nhỏ ngắn (giống nho) |

Hợp đồng `02-hop-dong-module.md` mục 5 ghi «số đè cao ≈ 45 % thẻ, panel ≈ 60 %» và «viền 3 px, bo 4–6». Theo PSD nên sửa thành **33 % thẻ**, **0,145 × rộng panel**, **viền 4 px outside, gradient dọc**.

## 5. Những thứ khó tự động

| Thao tác tay | Có tự động được không | Đề xuất |
|---|---|---|
| Ảnh nhân vật tách nền đè lên panel cung mệnh lớn (nho, ref-22) | Được một phần, cần biết panel thuộc nhân vật nào | Giai đoạn sau: khi panel có `owner` (id nhân vật) và tỉ lệ cao/rộng ≥ 1,1, lấy splash trong suốt từ Enka (`UI_Gacha_AvatarImg_*`), cao = cao panel, canh đáy, mép trái ở 0,34 × rộng panel, cho tràn phải ≤ 0,25 × rộng panel (được tràn ra khỏi canvas như nho). Viền outline 4 px màu khung (kiểu ref-22, dễ làm bằng dilate alpha) và **không** dùng shadow+glow. Chưa có `owner` thì **bỏ qua** |
| Icon nhân vật 54×54 ở góc panel (tam) | Thường đã có sẵn trong ảnh panel người dùng gửi (xem ảnh render #1075) | **Bỏ qua**. Chỉ cân nhắc dán side icon Enka khi panel có `owner` mà ảnh chưa có icon |
| Lưới chân dung 2×2 tự do (tam) | Được | Khi có ≥ 3 chân dung, xếp lưới 2 cột ở vùng trống phía trên vũ khí thay vì một dải ngang. Ưu tiên thấp |
| Kéo giãn chữ (nho 1,26×/1,39×) | Được nhưng không nên | Bỏ qua |
| Chọn gradient khung theo nền | Được | Ánh xạ màu nhấn → cặp gradient (mục 4 #4) |
| Chừa chỗ cho nhân vật trên nền (khối né mặt nhân vật) | Đã có một phần (`subject_side`) | Giữ nguyên. Bậc khoảng cách rộng ở #2 làm lộ nền nhiều hơn |

## 6. Ưu tiên triển khai

1. Cỡ chữ mã và UID (#1), số trên thẻ và panel (#6, #7): chỉ sửa hằng số, rủi ro thấp, thay đổi lớn nhất.
2. Khung 4 px outside + gradient dọc, viền chữ cùng gradient (#4, #5, #13).
3. Bỏ bóng, nền lưới frosted (#8, #9).
4. Bậc lề/khoảng cách và kích thước tối đa mới (#2, #3, #11, #12).
5. Khoảng cách thẻ theo tỉ lệ (#10).
6. (Sau) ảnh nhân vật đè panel khi có `owner`.
