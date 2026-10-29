"""Kiểm tra logic thuần của webapp và web_xlsx (không cần đăng nhập thật)."""

from __future__ import annotations

import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from PIL import Image

from gacard.backgrounds import BackgroundError, BackgroundLibrary
from gacard.web_xlsx import CODE_RE, parse_batch_rows
from gacard.webapp import (
    load_web_settings,
    pick_background_for_acc,
    save_web_settings,
)


class TestWebApp(unittest.TestCase):
    def test_code_regex(self):
        self.assertTrue(CODE_RE.match("11171"))
        self.assertTrue(CODE_RE.match("CN#1076"))
        self.assertTrue(CODE_RE.match("acc-01"))
        self.assertTrue(CODE_RE.match("acc_02"))
        self.assertFalse(CODE_RE.match(""))
        self.assertFalse(CODE_RE.match("code with space"))
        self.assertFalse(CODE_RE.match("too_long_code_exceeds_16_chars"))

    def test_web_xlsx_sample_rows(self):
        csv_content = """Mã acc,Tài khoản,Mật khẩu,Nền,Màu khung
11171,user1@gmail.com,pass123,columbina,cyan
11172,user2@gmail.com,,flins,
mã có dấu cách,user3@gmail.com,pass456,,
""".encode("utf-8")

        rows = parse_batch_rows(csv_content, "sample.csv")
        self.assertEqual(len(rows), 3)

        # Hàng 1 (stt 2): Hợp lệ, chờ chạy
        self.assertEqual(rows[0].row_num, 2)
        self.assertEqual(rows[0].code, "11171")
        self.assertTrue(rows[0].is_valid)
        self.assertIsNone(rows[0].error)

        # Hàng 2 (stt 3): Thiếu mật khẩu
        self.assertEqual(rows[1].row_num, 3)
        self.assertEqual(rows[1].code, "11172")
        self.assertFalse(rows[1].is_valid)
        self.assertIn("Thiếu mật khẩu", rows[1].error or "")

        # Hàng 3 (stt 4): Mã sai regex
        self.assertEqual(rows[2].row_num, 4)
        self.assertFalse(rows[2].is_valid)
        self.assertIn("không hợp lệ", rows[2].error or "")

    def test_pick_background_rules(self):
        class DummyCharSvc:
            def resolve_character(self, name):
                return name.lower()

            def display_name(self, slug):
                return slug.capitalize()

        with tempfile.TemporaryDirectory() as tmp:
            bg_root = Path(tmp) / "backgrounds"
            bg_root.mkdir()
            lib = BackgroundLibrary(bg_root)

            # Tạo ảnh mẫu 1920x1080
            def make_img_bytes():
                buf = io.BytesIO()
                img = Image.new("RGB", (1920, 1080), color="blue")
                img.save(buf, format="JPEG")
                return buf.getvalue()

            # Thêm 2 nền: flins và zibai
            bg_flins = lib.add(make_img_bytes(), "flins", source="test")
            bg_zibai = lib.add(make_img_bytes(), "zibai", source="test")

            chars_svc = DummyCharSvc()

            # 1. Acc chỉ có Flins: pool = {flins} -> TUYỆT ĐỐI không được chọn zibai
            bg_id, desc = pick_background_for_acc(lib, chars_svc, ["flins"], bg_mode="auto")
            self.assertEqual(bg_id, bg_flins.id)
            self.assertNotEqual(bg_id, bg_zibai.id)

            # 2. Acc có Zibai: pool = {zibai}
            bg_id2, _ = pick_background_for_acc(lib, chars_svc, ["zibai"], bg_mode="auto")
            self.assertEqual(bg_id2, bg_zibai.id)

            # 3. Chọn tay nen <zibai> kể cả khi acc chỉ có flins
            bg_id3, _ = pick_background_for_acc(lib, chars_svc, ["flins"], bg_mode="character", bg_character="zibai")
            self.assertEqual(bg_id3, bg_zibai.id)

            # 4. Reroll loại trừ id đang dùng
            bg_id4, _ = pick_background_for_acc(lib, chars_svc, ["flins"], bg_mode="auto", exclude_id=bg_flins.id)
            # Vì chỉ có 1 nền flins, loại trừ rồi thì rơi về nền mặc định (None)
            self.assertIsNone(bg_id4)

    def test_background_add_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bg_root = Path(tmp) / "backgrounds"
            bg_root.mkdir()
            lib = BackgroundLibrary(bg_root)

            # Ảnh nhỏ hơn 1920x1080 -> phải bị từ chối
            small_buf = io.BytesIO()
            Image.new("RGB", (1280, 720), color="red").save(small_buf, format="JPEG")
            with self.assertRaises(BackgroundError) as ctx:
                lib.add(small_buf.getvalue(), "columbina")
            self.assertIn("1920", ctx.exception.message_vi)

            # Ảnh đúng kích thước >= 1920x1080 -> thêm thành công
            ok_buf = io.BytesIO()
            Image.new("RGB", (1920, 1080), color="green").save(ok_buf, format="JPEG")
            added = lib.add(ok_buf.getvalue(), "columbina")
            self.assertEqual(added.character, "columbina")
            self.assertEqual(added.width, 1920)
            self.assertEqual(added.height, 1080)

    def test_settings_load_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            settings = load_web_settings(data_dir)
            self.assertEqual(settings["height"], 1080)

            # Cập nhật và lưu lại
            settings["frame_color"] = "red"
            settings["watermark"] = "@shop_test"
            save_web_settings(data_dir, settings)

            loaded = load_web_settings(data_dir)
            self.assertEqual(loaded["frame_color"], "red")
            self.assertEqual(loaded["watermark"], "@shop_test")

    def test_captcha_web_server(self):
        import urllib.request
        from unittest.mock import MagicMock
        from gacard.login_web import CaptchaWebServer, make_placeholder_png

        # 1. Kiểm tra ảnh placeholder luôn hợp lệ
        placeholder = make_placeholder_png("Test")
        self.assertTrue(placeholder.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(placeholder), 500)

        # 2. Khởi tạo server với mock page
        mock_page = MagicMock()
        fake_png = b"\x89PNG\r\n\x1a\n" + b"x" * 1024
        mock_page.screenshot.return_value = fake_png
        mock_page.viewport_size = {"width": 1280, "height": 800}

        server = CaptchaWebServer(mock_page, port=8799)
        url = server.start()
        self.assertIn("8799", url)
        self.assertTrue(server.token)

        try:
            # 3. Ban đầu khi chưa tick, get_screenshot trả placeholder hợp lệ
            initial_shot, fails = server.get_screenshot()
            self.assertTrue(initial_shot.startswith(b"\x89PNG\r\n\x1a\n"))

            # 4. Gọi tick() trên luồng chính mô phỏng luồng Playwright
            server.tick()
            mock_page.screenshot.assert_called_once()
            shot_after_tick, fails = server.get_screenshot()
            self.assertEqual(shot_after_tick, fake_png)
            self.assertEqual(fails, 0)

            # 5. GET / với token đúng -> 200 HTML
            req = urllib.request.Request(f"http://127.0.0.1:8799/?t={server.token}")
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("text/html", resp.headers.get("Content-Type", ""))
                html = resp.read().decode("utf-8")
                self.assertIn("K\u00e9o slider captcha HoYoLAB", html)

            # 6. GET / với token sai -> 403
            try:
                urllib.request.urlopen("http://127.0.0.1:8799/?t=invalid_token")
                self.fail("Phải trả về 403 khi sai token")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403)

            # 7. GET /shot với token đúng kèm param x chống cache -> 200 PNG
            req = urllib.request.Request(f"http://127.0.0.1:8799/shot?t={server.token}&x=1730000000")
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "image/png")
                self.assertEqual(int(resp.headers.get("Content-Length")), len(fake_png))
                body = resp.read()
                self.assertEqual(body, fake_png)

            # 8. POST /drag gửi tọa độ kéo chuột
            drag_payload = json.dumps({"t": server.token, "x0": 100, "y0": 200, "x1": 500, "y1": 200}).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:8799/drag",
                data=drag_payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)

            # Sau khi post, gọi tick() để Playwright thực thi
            server.tick()
            mock_page.mouse.move.assert_called()
            mock_page.mouse.down.assert_called_once()
            mock_page.mouse.up.assert_called_once()

            # 9. Thử nghiệm khi chụp màn hình lỗi 3 lần liên tiếp -> trả placeholder, không trả byte rỗng
            mock_page.screenshot.side_effect = RuntimeError("Playwright error")
            server.tick()
            server.tick()
            server.tick()
            err_shot, fails = server.get_screenshot()
            self.assertGreaterEqual(fails, 3)
            self.assertTrue(err_shot.startswith(b"\x89PNG\r\n\x1a\n"))

            req = urllib.request.Request(f"http://127.0.0.1:8799/shot?t={server.token}")
            with urllib.request.urlopen(req) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "image/png")
                self.assertEqual(int(resp.headers.get("Content-Length")), len(err_shot))
                body = resp.read()
                self.assertEqual(body, err_shot)

        finally:
            server.stop()



if __name__ == "__main__":
    unittest.main()
