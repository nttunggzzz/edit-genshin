# Genshin Impact — API Battle Chronicle (đã xác minh bằng HAR)

Cập nhật: 2026-10-28, từ HAR do người dùng xuất từ trình duyệt của chính họ (22:49 +07).
File này CHỈ có Genshin, không làm script, không làm HSR/ZZZ trong lần này.
File này KHÔNG chứa tài khoản, mật khẩu, token, cookie, UID, tên tài khoản hay tên nhân vật/vũ khí cụ thể nào. Mọi giá trị riêng tư trong HAR đã được lược bỏ, chỉ giữ method, host/path, tên tham số và tên trường.

## Host và khung chung

- Host game-record thực tế trang dùng: `sg-act-public-api.hoyolab.com`
- Mọi response game-record đều có khung chung: `retcode`, `message`, `data`.
- Trang Battle Chronicle Genshin: https://act.hoyolab.com/app/community-game-records-sea/index.html?bbs_presentation_style=fullscreen&v=350&gid=2&utm_source=hoyolab&utm_medium=tools&bbs_theme=light&bbs_theme_device=1#/ys

## Endpoint chính cho tool ảnh acc

### 1. GET /event/game_record/genshin/api/index
- Query: `avatar_list_type`, `server`, `role_id`
- `data.role`: `AvatarUrl`, `nickname`, `region`, `level`, `game_head_icon`
- `data.avatars[]`: `id`, `image`, `name`, `element`, `fetter`, `level`, `rarity`, `actived_constellation_num`, `card_image`, `is_chosen`, `weapon`, `relics`
- `data.stats`: `active_day_number`, `achievement_number`, `avatar_number`, `way_point_number`, `domain_number`, `spiral_abyss`, các số rương (`precious_chest_number`, `luxurious_chest_number`, `exquisite_chest_number`, `common_chest_number`, `magic_chest_number`), các số Thần Đồng theo vùng (`anemoculus_number`, `geoculus_number`, `electroculus_number`, `dendroculus_number`, `hydroculus_number`, `pyroculus_number`, `iceculus_number`, `moonoculus_number`), `full_fetter_avatar_num`, `role_combat`, `hard_challenge`
- `data.world_explorations[]`: `level`, `exploration_percentage`, `icon`, `name`, `type`, `offerings[]` (`name`, `level`, `icon`, `open_state`), `id`, `parent_id`, `map_url`, `strategy_url`, `background_image`, `inner_icon`, `cover`

### 2. POST /event/game_record/genshin/api/character/list
- Body: `server`, `role_id`, và ở một lần gọi khác thêm `sort_type`
- `data.list[]`: `id`, `icon`, `name`, `element`, `fetter`, `level`, `rarity`, `actived_constellation_num`, `image`, `is_chosen`, `side_icon`, `weapon_type`, `weapon`
- `weapon` trong list (bản rút gọn): `id`, `icon`, `type`, `rarity`, `level`, `affix_level`, `name`

### 3. POST /event/game_record/genshin/api/character/detail
- Body: `server`, `role_id`, `character_ids[]` (gọi theo từng nhân vật, 1 id / lần trong HAR này)
- `data.list[]`: `base`, `weapon`, `relics`, `constellations`, `costumes`, `selected_properties`, `base_properties`, `extra_properties`, `element_properties`, `skills`, `recommend_relic_property`, `weapon_skin`, `unlock_tps`
- `base`: giống trường của character/list, thêm `weapon_type`
- `weapon` (bản đầy đủ): `id`, `name`, `icon`, `type`, `rarity`, `level`, `promote_level`, `type_name`, `desc`, `affix_level`, `main_property`, `sub_property`
- `relics[]`: `id`, `name`, `icon`, `pos`, `rarity`, `level`, `set`, `pos_name`, `main_property`, `sub_property_list`
- `constellations[]`: `id`, `name`, `icon`, `effect`, `is_actived`, `pos`, `is_enhanced`, `enhanced_effect`, `can_enhanced`
- `costumes[]`: `id`, `name`, `icon`
- Các nhóm chỉ số: `selected_properties[]`, `base_properties[]`, `extra_properties[]`, `element_properties[]`, mỗi phần tử có `property_type`, `base`, `add`, `final`
- `skills[]`: `skill_id`, `skill_type`, `level`, `desc`, `skill_affix_list`, `icon`, `is_unlock`, `name`, `is_enhanced`, `enhanced_desc`, `before_enhanced_skill_attr_index`, `after_enhanced_skill_attr_index`, `can_enhanced`
- `weapon_skin`: `weapon_skin_icon`, `weapon_skin_rarity`, `weapon_skin_name`
- Ngoài `data.list`, response còn có `property_map`, `relic_property_options` (`sand_main_property_list`, `goblet_main_property_list`, `circlet_main_property_list`, `sub_property_list`), `relic_wiki`, `weapon_wiki`, `avatar_wiki` (chỉ giữ tên trường, không giữ id cụ thể)

### 4. POST /event/game_record/genshin/api/avatarBasicInfo
- Body: `character_ids[]`
- `data.avatars[]`: `id`, `image`, `icon`, `name`, `element`, `rarity`, `weapon_type`, `weapon_type_name`

## Endpoint phụ đã thấy trong HAR

- GET `/event/game_record/genshin/api/spiralAbyss` — query: `server`, `role_id`, `schedule_type`
- GET `/event/game_record/genshin/api/role_combat`
- GET `/event/game_record/genshin/api/hard_challenge`
- GET `/event/game_record/genshin/api/hard_challenge/popularity`
- GET `/event/game_record/genshin/api/gcg/basicInfo`
- GET `/event/game_record/genshin/api/external_content`
- GET `/event/game_record/genshin/api/char_master`
- GET `/event/game_record/genshin/api/tool_oversea`
- POST `/event/game_record/genshin/api/act_calendar`
- GET `/event/game_record/card/wapi/getGameRecordCard` — query: `uid`; `data.list[]`: `has_role`, `game_id`, `game_role_id`, `nickname`, `region`, `level`, `background_image`, `is_public`, `data[]` (`name`, `type`, `value`), `region_name`, `url`, `data_switches[]` (`switch_id`, `is_public`, `switch_name`), `background_color`, `background_image_v2`, `logo`, `game_name`

## Cách đếm cho tool ảnh acc

- Tổng nhân vật = `data.stats.avatar_number` từ `index`, hoặc `len(data.list)` từ `character/list`.
- Tổng vũ khí đang trang bị = số phần tử trong `character/list` có `weapon` (1 vũ khí / 1 nhân vật).
- Không suy tổng kho vũ khí chưa trang bị từ danh sách nhân vật, vì API này chỉ trả vũ khí đang gắn.
- Muốn cấp, cung mệnh, vũ khí đầy đủ thì gọi `character/detail` theo từng id lấy từ `character/list`.
- Cấp Thế Giới: KHÔNG có trong `index` (`role` chỉ có `nickname`, `region`, `level`, `game_head_icon`) và
  cũng không có trong `getGameRecordCard` (bbs-api-os.hoyolab.com, `data[]` chỉ: ngày hoạt động, số nhân
  vật, thành tựu, La Hoàn) — đã kiểm bằng gọi thật 2026-10-29. Tool lấy `playerInfo.worldLevel` (và
  `nameCardId` làm nền ô hồ sơ) từ Enka cho cùng UID; không có thì bỏ hàng Cấp Thế Giới.

## Flow tool đã chốt (Genshin) — sửa 2026-10-29

1. Chủ dự án tự đăng nhập HoYoLAB trên trình duyệt của mình (tự giải captcha nếu có).
2. Chép cookie (cần `ltuid_v2`, `ltoken_v2`) vào `GACARD_HOYOLAB_COOKIE` trong `.env`. Tool KHÔNG nhận tài khoản/mật khẩu, không tự đăng nhập.
3. Với cookie đó, tool tra Battle Chronicle của UID bất kỳ đã bật công khai Sổ Tay Hành Trình.
4. Nếu API đòi xác minh, tool mở trang Battle Chronicle trên trình duyệt của người dùng; người dùng tự kéo captcha trên trang thật rồi nhấn Enter; tool thử lại đúng 1 lần. Tool không đọc, cắt hay chuyển tiếp captcha.
5. Lệnh: `python -m gacard.hoyolab <uid> [--code N]`, ảnh lưu vào `D:\Claude\genshin\output`.
