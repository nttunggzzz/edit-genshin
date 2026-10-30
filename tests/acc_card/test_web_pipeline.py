"""Kiểm tra mock toàn bộ pipeline web, bắt lỗi login, pacing stats, Battle Chronicle và clean JSON."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gacard import hoyolab
from gacard.hoyolab_login import (
    BATTLE_CHRONICLE_URL,
    LoginCookies,
    LoginError,
    _bring_page_to_front,
    _detect_login_error,
    login_with_password,
    login_with_password_cdp,
)
from gacard.login_web import CaptchaWebServer
from gacard.webapp import (
    BatchJob,
    SingleJob,
    WebState,
    handle_api_job_bring_to_front,
    handle_api_job_captcha_proof,
    handle_api_job_fallback_web,
    process_single_account_pipeline,
    reroll_account_from_json,
    run_batch_job,
    save_acc_json,
)


class TestWebPipeline(unittest.TestCase):
    def test_detect_login_error_vant_and_too_many(self):
        """Kiểm tra _detect_login_error bắt được toast Vant và thông báo Too many requests."""
        # 1. Page có Vant toast lỗi sai mật khẩu
        mock_el_vant = MagicMock()
        mock_el_vant.is_visible.return_value = True
        mock_el_vant.inner_text.return_value = "Tên người dùng hoặc mật khẩu không chính xác"

        mock_loc_vant = MagicMock()
        mock_loc_vant.count.return_value = 1
        mock_loc_vant.nth.return_value = mock_el_vant

        mock_page_vant = MagicMock()
        mock_page_vant.frames = []
        mock_page_vant.locator.side_effect = lambda sel: mock_loc_vant if sel == ".van-toast__text" else MagicMock(count=lambda: 0)

        err_vant = _detect_login_error(mock_page_vant)
        self.assertIsNotNone(err_vant)
        self.assertIn("không chính xác", err_vant or "")

        # 2. Page có thông báo Too many requests
        mock_el_limit = MagicMock()
        mock_el_limit.is_visible.return_value = True
        mock_el_limit.inner_text.return_value = "Too many requests, please try again later"

        mock_loc_limit = MagicMock()
        mock_loc_limit.count.return_value = 1
        mock_loc_limit.nth.return_value = mock_el_limit

        mock_page_limit = MagicMock()
        mock_page_limit.frames = []
        mock_page_limit.locator.side_effect = lambda sel: mock_loc_limit if sel == '[role="alert"]' else MagicMock(count=lambda: 0)

        err_limit = _detect_login_error(mock_page_limit)
        self.assertIsNotNone(err_limit)
        self.assertIn("Too many requests", err_limit or "")

        # 3. Không có lỗi nào
        mock_page_clean = MagicMock()
        mock_page_clean.frames = []
        mock_page_clean.locator.return_value.count.return_value = 0
        self.assertIsNone(_detect_login_error(mock_page_clean))

    def test_battle_chronicle_navigation_and_cookie_token_v2(self):
        """Chứng minh điều hướng sang Battle Chronicle đặt cookie_token_v2."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_page.context = mock_context
        mock_pw = MagicMock()

        mock_browser = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        with patch("gacard.hoyolab_login._fill_login_form_sync", return_value=True), \
             patch("gacard.hoyolab_login._detect_login_error", return_value=None), \
             patch("gacard.hoyolab_login._captcha_visible", return_value=False):

            # Lần 1 (line 420 check signed): chưa có cookie
            # Lần 2 (line 438 check signed): có ltuid & ltoken -> signed=True
            # Lần 3 (line 516 build_cookie_headers): có cookie_token_v2 sau khi navigate Battle Chronicle
            mock_context.cookies.side_effect = [
                [],
                [
                    {"name": "ltuid_v2", "value": "12345678", "domain": ".hoyolab.com"},
                    {"name": "ltoken_v2", "value": "v2_sample_ltoken", "domain": ".hoyolab.com"},
                ],
                [
                    {"name": "ltuid_v2", "value": "12345678", "domain": ".hoyolab.com"},
                    {"name": "ltoken_v2", "value": "v2_sample_ltoken", "domain": ".hoyolab.com"},
                    {"name": "cookie_token_v2", "value": "v2_sample_cookie_token", "domain": ".hoyolab.com"},
                    {"name": "account_id_v2", "value": "12345678", "domain": ".hoyolab.com"},
                ]
            ]

            with patch("playwright.sync_api.sync_playwright") as mock_sync_pw:
                mock_sync_pw.return_value.__enter__.return_value = mock_pw

                cookies = login_with_password("user_test", "pass_test", timeout_seconds=5)
                # Xác minh đã gọi goto tới BATTLE_CHRONICLE_URL
                mock_page.goto.assert_any_call(BATTLE_CHRONICLE_URL, timeout=15000)
                # Xác minh cookie_token_v2 đã được nạp vào login cookies
                self.assertEqual(cookies.ltuid, "12345678")
                self.assertIn("ltuid_v2=12345678", cookies.cookie)
                self.assertIn("ltoken_v2=v2_sample_ltoken", cookies.cookie)
                self.assertIsNotNone(cookies.cookie_token)
                self.assertIn("cookie_token_v2=v2_sample_cookie_token", cookies.cookie_token or "")

    def test_login_raises_too_many_requests(self):
        """Kiểm tra khi gặp lỗi Too many requests thì dừng ngay và raise đúng mã lỗi."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_page.context = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        with patch("gacard.hoyolab_login._fill_login_form_sync", return_value=True), \
             patch("gacard.hoyolab_login._detect_login_error", return_value="Too many requests, please try again later"), \
             patch("gacard.hoyolab_login._captcha_visible", return_value=False):

            mock_context.cookies.return_value = []
            with patch("playwright.sync_api.sync_playwright") as mock_sync_pw:
                mock_sync_pw.return_value.__enter__.return_value = mock_pw

                with self.assertRaises(LoginError) as ctx:
                    login_with_password("user_test", "pass_test", timeout_seconds=5)
                self.assertEqual(ctx.exception.kind, "too_many_requests")
                self.assertIn("giới hạn tạm thời", str(ctx.exception))

    def test_save_acc_json_clean_data(self):
        """Kiểm tra save_acc_json tạo snapshot sạch, KHÔNG chứa mật khẩu hay cookie/token."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            code = "11171"
            uid = "812345678"
            index_data = {
                "role": {"Nickname": "Aether", "Level": 60},
                "stats": {"active_day_number": 350, "achievement_number": 680},
                "world_explorations": [{"name": "Mondstadt", "exploration_percentage": 1000}],
            }
            list_data = [
                {"id": 10000002, "name": "Kamisato Ayaka", "rarity": 5, "level": 90, "fetter": 10, "actived_constellation_num": 2}
            ]
            extras = {"10000002": {"stats": {"crit_rate": 45.2, "crit_dmg": 220.5}}}
            wish_lines = ["Banner Nhân Vật: 45 roll chưa ra 5*"]
            chars_acc = ["kamisato ayaka"]

            json_path = save_acc_json(
                out_dir, code, uid, index_data, list_data, extras, wish_lines, chars_acc
            )

            self.assertTrue(json_path.exists())
            self.assertEqual(json_path.name, f"acc-{code}-{uid}.json")

            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["code"], "11171")
            self.assertEqual(data["uid"], "812345678")
            self.assertIn("index_data", data)
            self.assertIn("list_data", data)
            self.assertEqual(len(data["list_data"]), 1)
            self.assertEqual(data["list_data"][0]["name"], "Kamisato Ayaka")

            # Tuyệt đối không chứa thông tin nhạy cảm
            raw_content = json_path.read_text(encoding="utf-8").lower()
            self.assertNotIn("password", raw_content)
            self.assertNotIn("ltoken", raw_content)
            self.assertNotIn("ltuid", raw_content)
            self.assertNotIn("cookie_token", raw_content)
            self.assertNotIn("authkey", raw_content)

    def test_pacing_stats_tracking(self):
        """Kiểm tra WebState theo dõi thời gian pacing chuẩn xác."""
        with tempfile.TemporaryDirectory() as tmp:
            state = WebState(Path(tmp))
            # Ban đầu
            stats0 = state.get_pacing_stats()
            self.assertIsNone(stats0["last_login_time"])
            self.assertIsNone(stats0["current_login_time"])
            self.assertEqual(stats0["accs_last_1h"], 0)

            # Login acc 1
            state.record_login_start()
            stats1 = state.get_pacing_stats()
            self.assertIsNone(stats1["last_login_time"])
            self.assertIsNotNone(stats1["current_login_time"])
            self.assertEqual(stats1["accs_last_1h"], 1)

            # Login acc 2
            t1 = stats1["current_login_time"]
            state.record_login_start()
            stats2 = state.get_pacing_stats()
            self.assertEqual(stats2["last_login_time"], t1)
            self.assertIsNotNone(stats2["current_login_time"])
            self.assertEqual(stats2["accs_last_1h"], 2)

    def test_batch_job_halts_on_too_many_requests(self):
        """Kiểm tra BatchJob dừng ngay lập tức khi phát hiện lỗi too_many_requests."""
        with tempfile.TemporaryDirectory() as tmp:
            state = WebState(Path(tmp))
            batch = BatchJob(
                id="batch_test_halt",
                total=3,
                rows=[
                    {"stt": 1, "code": "acc1", "status": "Chờ chạy"},
                    {"stt": 2, "code": "acc2", "status": "Chờ chạy"},
                    {"stt": 3, "code": "acc3", "status": "Chờ chạy"},
                ]
            )
            state.batch_jobs[batch.id] = batch

            raw_items = [
                {"stt": 1, "code": "acc1", "username": "u1", "password": "p1"},
                {"stt": 2, "code": "acc2", "username": "u2", "password": "p2"},
                {"stt": 3, "code": "acc3", "username": "u3", "password": "p3"},
            ]

            # Giả lập pipeline ném lỗi too_many_requests ngay ở acc đầu tiên
            with patch("gacard.webapp.process_single_account_pipeline") as mock_pipeline:
                mock_pipeline.side_effect = LoginError("too_many_requests", "HoYoLAB đang giới hạn tạm thời")

                asyncio.run(run_batch_job(batch, raw_items, state))

                # Batch phải dừng ngay lập tức
                self.assertEqual(batch.status, "stopped")
                self.assertEqual(batch.error_count, 1)
                self.assertEqual(batch.rows[0]["status"], "Lỗi")
                self.assertIn("giới hạn", batch.rows[0]["reason"])
                # Các dòng tiếp theo vẫn giữ nguyên trạng thái hoặc chưa chạy
                self.assertEqual(batch.rows[1]["status"], "Chờ chạy")
                self.assertEqual(batch.rows[2]["status"], "Chờ chạy")
                # Pipeline chỉ được gọi đúng 1 lần, không chạy acc 2 và 3
                mock_pipeline.assert_called_once()

    def test_crawl_random_delay_1_to_5_seconds(self):
        """Kiểm tra pipeline crawl luôn áp dụng delay ngẫu nhiên từ 1 đến 5 giây giữa các bước."""
        with tempfile.TemporaryDirectory() as tmp:
            state = WebState(Path(tmp))
            work_dir = Path(tmp) / "work"
            work_dir.mkdir()

            mock_cookies = LoginCookies(
                cookie="ltuid_v2=12345; ltoken_v2=sample_token",
                cookie_token="cookie_token_v2=sample_cookie_token",
                ltuid="12345",
            )
            mock_client = MagicMock()
            mock_client.index.return_value = {"role": {"Nickname": "Traveler"}}
            mock_client.character_list.return_value = []

            delays_recorded = []
            def fake_sleep(seconds):
                delays_recorded.append(seconds)

            with patch("gacard.webapp.login_with_password", return_value=mock_cookies), \
                 patch("gacard.webapp.discover_uids", return_value=["800111222"]), \
                 patch("gacard.hoyolab.HoyolabClient", return_value=mock_client), \
                 patch("gacard.hoyolab.fetch_enka_extras", return_value={}), \
                 patch("gacard.wish.fetch_wish_history", return_value=MagicMock(records=[])), \
                 patch("gacard.hoyolab.build_sheet_from_data", return_value=(MagicMock(), MagicMock(account_characters=["columbina"]))), \
                 patch("gacard.webapp.render_account_card", return_value=(Path("dummy.jpg"), "dummy.jpg", "dummy.jpg")), \
                 patch("time.sleep", side_effect=fake_sleep):

                process_single_account_pipeline(
                    username="user1",
                    password="pass1",
                    code="111",
                    bg_mode="auto",
                    bg_character="",
                    bg_id="",
                    frame_color=None,
                    height=1080,
                    watermark="",
                    state=state,
                    work_dir=work_dir,
                )

                # Phải có ít nhất 4 lần delay (index, character_list, enka, wishes)
                self.assertGreaterEqual(len(delays_recorded), 4)
                for d in delays_recorded:
                    self.assertGreaterEqual(d, 1.0, f"Delay {d} nhỏ hơn 1.0 giây")
                    self.assertLessEqual(d, 5.0, f"Delay {d} lớn hơn 5.0 giây")

    def test_reroll_account_from_json(self):
        """Kiểm tra Đổi Nền vẽ lại trực tiếp từ file JSON đã lưu, không cào lại HoYoLAB, và chỉ chọn nền trong acc."""
        import io
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmp:
            state = WebState(Path(tmp))
            work_dir = Path(tmp) / "work"
            work_dir.mkdir()

            # Tạo 2 nền giả lập trong kho: 1 columbina, 1 flins
            def make_img_bytes():
                buf = io.BytesIO()
                Image.new("RGB", (1920, 1080), color="blue").save(buf, format="JPEG")
                return buf.getvalue()

            bg1 = state.bg_lib.add(make_img_bytes(), "columbina", source="test1")
            bg2 = state.bg_lib.add(make_img_bytes(), "flins", source="test2")

            # Tạo file JSON snapshot acc đã cào sẵn, sở hữu columbina và flins
            json_file = save_acc_json(
                out_dir=state.out_dir,
                code="11171",
                uid="800111222",
                index_data={"role": {"Nickname": "Aether", "Level": 60}},
                list_data=[{"id": 10000001, "name": "Columbina"}, {"id": 10000002, "name": "Flins"}],
                extras={},
                wish_lines=["Banner: 20 roll"],
                chars_acc=["columbina", "flins"],
            )

            with patch("gacard.hoyolab.build_sheet_from_data", return_value=(MagicMock(), MagicMock())) as mock_sheet, \
                 patch("gacard.webapp.render_account_card", return_value=(Path("reroll.jpg"), "reroll.jpg", "reroll.jpg")) as mock_render, \
                 patch("gacard.webapp.login_with_password") as mock_login:

                # Reroll loại trừ bg1 -> phải chọn bg2 (flins)
                dest_path, filename, dl_name, new_bg_id, new_bg_desc = reroll_account_from_json(
                    json_path=json_file,
                    work_dir=work_dir,
                    state=state,
                    exclude_bg_id=bg1.id,
                )

                # Tuyệt đối không đăng nhập lại
                mock_login.assert_not_called()
                # Nền mới phải là bg2 (flins), loại trừ bg1
                self.assertEqual(new_bg_id, bg2.id)
                self.assertIn("flins", new_bg_desc.lower())
                # Đã gọi build_sheet_from_data và render_account_card
                mock_sheet.assert_called_once()
                mock_render.assert_called_once()

    def test_captcha_mode_local_no_web_server_and_brings_to_front(self):
        """Kiểm tra chế độ local: không bật server port 8766, gọi bring_to_front() và trả link rỗng."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_page.context = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        captcha_links_received = []
        def fake_on_captcha(link):
            captcha_links_received.append(link)

        # Lần 1: captcha hiện, Lần 2: kéo xong -> signed=True
        with patch("gacard.hoyolab_login._fill_login_form_sync", return_value=True), \
             patch("gacard.hoyolab_login._detect_login_error", return_value=None), \
             patch("gacard.hoyolab_login._captcha_visible", side_effect=[True, False]), \
             patch("gacard.hoyolab_login.BATTLE_CHRONICLE_URL", "http://battle-chronicle.local"):

            mock_context.cookies.side_effect = [
                [],  # check ban đầu trước loop: chưa signed
                [],  # vòng lặp 1: đang có captcha, chưa signed
                [    # vòng lặp 2: hết captcha, đã signed
                    {"name": "ltuid_v2", "value": "12345", "domain": ".hoyolab.com"},
                    {"name": "ltoken_v2", "value": "sample_token", "domain": ".hoyolab.com"},
                ],
                [    # sau khi break loop: build_cookie_headers
                    {"name": "ltuid_v2", "value": "12345", "domain": ".hoyolab.com"},
                    {"name": "ltoken_v2", "value": "sample_token", "domain": ".hoyolab.com"},
                ]
            ]

            with patch("playwright.sync_api.sync_playwright") as mock_sync_pw:
                mock_sync_pw.return_value.__enter__.return_value = mock_pw

                with patch("gacard.login_web.CaptchaWebServer") as mock_captcha_server:
                    login_with_password(
                        "user", "pass",
                        captcha_mode="local",
                        on_captcha=fake_on_captcha,
                        timeout_seconds=5
                    )
                    # Tuyệt đối không khởi tạo CaptchaWebServer ở chế độ local
                    mock_captcha_server.assert_not_called()

                # on_captcha được gọi với link rỗng (không mở port 8766)
                self.assertEqual(captcha_links_received, [""])
                # Đã gọi bring_to_front() để đưa cửa sổ trình duyệt lên trước cho người dùng kéo
                mock_page.bring_to_front.assert_called()

    def test_bring_page_to_front_logs_and_returns_status(self):
        """Kiểm tra _bring_page_to_front không bao giờ bỏ qua lỗi im lặng, log rõ thành công và thất bại."""
        mock_page_ok = MagicMock()
        logs_ok = []
        res_ok = _bring_page_to_front(mock_page_ok, on_log=logs_ok.append)
        self.assertTrue(res_ok)
        self.assertEqual(len(logs_ok), 1)
        self.assertIn("thành công", logs_ok[0])

        mock_page_err = MagicMock()
        mock_page_err.bring_to_front.side_effect = RuntimeError("Windows focus blocked")
        logs_err = []
        res_err = _bring_page_to_front(mock_page_err, on_log=logs_err.append)
        self.assertFalse(res_err)
        self.assertEqual(len(logs_err), 1)
        self.assertIn("Không thể đưa cửa sổ", logs_err[0])
        self.assertIn("Windows focus blocked", logs_err[0])

    def test_captcha_mode_local_automatic_fallback_when_bring_front_fails(self):
        """Kiểm tra khi đưa cửa sổ Chromium lên trước thất bại, hệ thống tự động mở server dự phòng ở port 8766."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_page.context = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        # Giả lập bring_to_front bị lỗi trên Windows
        mock_page.bring_to_front.side_effect = RuntimeError("SetForegroundWindow failed")

        captcha_links_received = []
        def fake_on_captcha(link):
            captcha_links_received.append(link)

        with patch("gacard.hoyolab_login._fill_login_form_sync", return_value=True), \
             patch("gacard.hoyolab_login._detect_login_error", return_value=None), \
             patch("gacard.hoyolab_login._captcha_visible", side_effect=[True, False]), \
             patch("gacard.hoyolab_login.BATTLE_CHRONICLE_URL", "http://battle-chronicle.local"):

            mock_context.cookies.side_effect = [
                [],
                [],
                [
                    {"name": "ltuid_v2", "value": "12345", "domain": ".hoyolab.com"},
                    {"name": "ltoken_v2", "value": "sample_token", "domain": ".hoyolab.com"},
                ],
                [
                    {"name": "ltuid_v2", "value": "12345", "domain": ".hoyolab.com"},
                    {"name": "ltoken_v2", "value": "sample_token", "domain": ".hoyolab.com"},
                ]
            ]

            mock_server_instance = MagicMock()
            mock_server_instance.start.return_value = "http://localhost:8765/?t=mock_fallback_token"

            with patch("playwright.sync_api.sync_playwright") as mock_sync_pw:
                mock_sync_pw.return_value.__enter__.return_value = mock_pw

                with patch("gacard.login_web.CaptchaWebServer", return_value=mock_server_instance) as mock_captcha_server:
                    login_with_password(
                        "user", "pass",
                        captcha_mode="local",
                        on_captcha=fake_on_captcha,
                        timeout_seconds=5
                    )
                    # Vì bring_to_front lỗi, CaptchaWebServer PHẢI được khởi động làm phương án dự phòng!
                    mock_captcha_server.assert_called_once_with(mock_page, port=8765)
                    mock_server_instance.start.assert_called_once()

                # on_captcha phải nhận được link dự phòng của server
                self.assertEqual(captcha_links_received, ["http://localhost:8765/?t=mock_fallback_token"])

    def test_api_endpoints_only_set_flags_without_touching_page(self):
        """Kiểm tra API web /bring-to-front và /fallback-web-captcha TUYỆT ĐỐI không gọi trực tiếp page, chỉ đặt cờ."""
        with tempfile.TemporaryDirectory() as tmp:
            state = WebState(Path(tmp))
            job = SingleJob(id="job_test_flags", code="111", username="u", password="p", status="waiting_captcha")
            state.single_jobs[job.id] = job

            self.assertFalse(job.req_bring_to_front)
            self.assertFalse(job.req_fallback_web)

            # 1. Gọi API bring-to-front
            mock_req_bring = MagicMock()
            mock_req_bring.app = {"state": state}
            mock_req_bring.match_info = {"id": job.id}

            resp_bring = asyncio.run(handle_api_job_bring_to_front(mock_req_bring))
            self.assertEqual(resp_bring.status, 200)
            data_bring = json.loads(resp_bring.text)
            self.assertTrue(data_bring["ok"])
            # Xác nhận: API chỉ đặt cờ True, không gọi trực tiếp bất kỳ trang hay hàm Playwright nào
            self.assertTrue(job.req_bring_to_front)

            # 2. Gọi API fallback-web-captcha
            mock_req_fallback = MagicMock()
            mock_req_fallback.app = {"state": state}
            mock_req_fallback.match_info = {"id": job.id}

            resp_fallback = asyncio.run(handle_api_job_fallback_web(mock_req_fallback))
            self.assertEqual(resp_fallback.status, 200)
            data_fallback = json.loads(resp_fallback.text)
            self.assertTrue(data_fallback["ok"])
            # Xác nhận: API chỉ đặt cờ True, không trực tiếp mở server ở luồng web
            self.assertTrue(job.req_fallback_web)

    def test_browser_thread_processes_action_flags_safely(self):
        """Kiểm tra luồng Playwright tự kiểm tra cờ và gọi bring_to_front và CaptchaWebServer trong chính luồng của nó."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_page.context = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        flag_state = {"bring_to_front": True, "fallback_web": True}
        def fake_check_actions():
            res = dict(flag_state)
            flag_state["bring_to_front"] = False
            flag_state["fallback_web"] = False
            return res

        captcha_links = []
        with patch("gacard.hoyolab_login._fill_login_form_sync", return_value=True), \
             patch("gacard.hoyolab_login._detect_login_error", return_value=None), \
             patch("gacard.hoyolab_login._captcha_visible", side_effect=[True, False]), \
             patch("gacard.hoyolab_login.BATTLE_CHRONICLE_URL", "http://battle-chronicle.local"):

            mock_context.cookies.side_effect = [
                [],
                [],
                [
                    {"name": "ltuid_v2", "value": "12345", "domain": ".hoyolab.com"},
                    {"name": "ltoken_v2", "value": "sample_token", "domain": ".hoyolab.com"},
                ],
                [
                    {"name": "ltuid_v2", "value": "12345", "domain": ".hoyolab.com"},
                    {"name": "ltoken_v2", "value": "sample_token", "domain": ".hoyolab.com"},
                ]
            ]

            mock_server = MagicMock()
            mock_server.start.return_value = "http://localhost:8765/?t=flag_test_token"

            with patch("playwright.sync_api.sync_playwright") as mock_sync_pw:
                mock_sync_pw.return_value.__enter__.return_value = mock_pw
                with patch("gacard.login_web.CaptchaWebServer", return_value=mock_server) as mock_server_cls:
                    login_with_password(
                        "user", "pass",
                        captcha_mode="local",
                        on_captcha=captcha_links.append,
                        timeout_seconds=5,
                        check_action_requests=fake_check_actions,
                    )
                    # Xác minh cờ fallback_web đã kích hoạt mở server dự phòng ngay trong luồng Playwright
                    mock_server_cls.assert_called_once_with(mock_page, port=8765)
                    mock_server.start.assert_called_once()
                    self.assertEqual(captcha_links, ["http://localhost:8765/?t=flag_test_token"])

                # Xác minh bring_to_front đã được gọi trong luồng Playwright
                mock_page.bring_to_front.assert_called()

    def test_fake_captcha_page_drag_accuracy_and_human_motion(self):
        """Chứng minh bằng test giả: quy đổi tọa độ 1280x800 chính xác và kéo từng bước nhỏ giống tay người."""
        mock_page = MagicMock()
        mock_page.viewport_size = {"width": 1280, "height": 800}

        moves_recorded = []
        timeouts_recorded = []
        is_mouse_down = False
        mouse_down_called = False
        mouse_up_called = False

        def fake_move(x, y):
            moves_recorded.append((float(x), float(y)))

        def fake_down():
            nonlocal is_mouse_down, mouse_down_called
            is_mouse_down = True
            mouse_down_called = True

        def fake_up():
            nonlocal is_mouse_down, mouse_up_called
            is_mouse_down = False
            mouse_up_called = True

        mock_page.mouse.move = fake_move
        mock_page.mouse.down = fake_down
        mock_page.mouse.up = fake_up
        mock_page.wait_for_timeout = lambda ms: timeouts_recorded.append(ms)

        server = CaptchaWebServer(mock_page, port=8765)

        # Giả lập payload kéo từ trình duyệt web với kích thước ảnh hiển thị 640x400:
        # Người dùng kéo từ x=200, y=250 tới x=500, y=250 trên ảnh hiển thị
        payload = {
            "x0": 200, "y0": 250,
            "x1": 500, "y1": 250,
            "w": 640, "h": 400
        }

        # 1. Thực thi kéo trên trang captcha giả
        server._execute_drag(payload)

        # 2. Kiểm tra quy đổi tọa độ chính xác về 1280x800:
        # start: (200 / 640) * 1280 = 400.0, (250 / 400) * 800 = 500.0
        # end:   (500 / 640) * 1280 = 1000.0, (250 / 400) * 800 = 500.0
        self.assertTrue(mouse_down_called)
        self.assertTrue(mouse_up_called)
        self.assertGreaterEqual(len(moves_recorded), 28)  # Kéo từng bước nhỏ (> 28 bước)

        first_move = moves_recorded[0]
        self.assertAlmostEqual(first_move[0], 400.0, places=1)
        self.assertAlmostEqual(first_move[1], 500.0, places=1)

        last_move = moves_recorded[-1]
        self.assertAlmostEqual(last_move[0], 1000.0, places=1)
        self.assertAlmostEqual(last_move[1], 500.0, places=1)

        # 3. Kiểm tra kéo giống tay người (có biến thiên tốc độ, có rung lắc nhẹ và dừng nhẹ):
        # Tọa độ X phải tăng dần về đích
        x_values = [pt[0] for pt in moves_recorded]
        self.assertTrue(all(x_values[i] <= x_values[i+1] + 1.0 for i in range(len(x_values)-1)))

        # Tọa độ Y có dao động vi mô (tremor) xung quanh 500.0 chứ không phải đường thẳng tuyệt đối
        y_values = [pt[1] for pt in moves_recorded]
        y_deviations = [abs(y - 500.0) for y in y_values]
        self.assertTrue(any(dev > 0.05 for dev in y_deviations))

        # Kiểm tra có dừng nhẹ và độ trễ biến thiên giữa các bước
        self.assertGreaterEqual(len(timeouts_recorded), 28)
        self.assertTrue(any(t >= 40 for t in timeouts_recorded))  # Có bước dừng nhẹ

    def test_jpeg_stream_content_type_and_fast_refresh(self):
        """Kiểm tra máy chủ CaptchaWebServer phục vụ ảnh JPEG nhẹ với header chuẩn b'\\xff\\xd8'."""
        import urllib.request

        mock_page = MagicMock()
        mock_page.viewport_size = {"width": 1280, "height": 800}
        fake_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIFfake_jpeg_stream"
        mock_page.screenshot.return_value = fake_jpeg

        server = CaptchaWebServer(mock_page, port=18765)
        server.tick()
        link = server.start()
        token = server.token

        try:
            # Gửi request lấy ảnh /shot
            shot_url = f"http://127.0.0.1:{server.port}/shot?t={token}"
            req = urllib.request.Request(shot_url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                self.assertEqual(resp.status, 200)
                content_type = resp.headers.get("Content-Type")
                self.assertEqual(content_type, "image/jpeg")
                body = resp.read()
                # Kiểm tra magic bytes của định dạng JPEG
                self.assertTrue(body.startswith(b"\xff\xd8"))
        finally:
            server.stop()

    def test_captcha_proof_screenshot_api_and_job_storage(self):
        """Kiểm tra khi phát hiện Captcha, bot lưu ảnh bằng chứng JPEG và API web 8001 phục vụ ảnh đó."""
        with tempfile.TemporaryDirectory() as tmp:
            state = WebState(Path(tmp))
            job = SingleJob(id="job_proof_test", code="111", username="u", password="p", status="waiting_captcha")
            
            # Giả lập ảnh bằng chứng JPEG thực tế
            fake_proof_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIFfake_proof_data"
            job.captcha_proof_bytes = fake_proof_jpeg
            job.captcha_proof_url = f"/api/jobs/{job.id}/captcha-proof.jpg"
            state.single_jobs[job.id] = job

            # Giả lập gọi API lấy ảnh bằng chứng
            mock_req = MagicMock()
            mock_req.app = {"state": state}
            mock_req.match_info = {"id": job.id}

            resp = asyncio.run(handle_api_job_captcha_proof(mock_req))
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.content_type, "image/jpeg")
            self.assertEqual(resp.body, fake_proof_jpeg)

    def test_cdp_wait_timeout_when_browser_not_opened(self):
        """Kiểm tra chế độ CDP: nếu quá thời gian chờ (120s) mà trình duyệt chưa mở thì hủy job báo lỗi rõ."""
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            with self.assertRaises(LoginError) as ctx:
                login_with_password_cdp("fake_user", "fake_pass", cdp_wait_seconds=0.05)
            self.assertEqual(ctx.exception.kind, "cdp_not_opened")
            self.assertIn("scripts/launch_cdp_browser.bat", ctx.exception.message_vi)

    def test_cdp_connect_fill_form_and_direct_captcha(self):
        """Kiểm tra bot gắn qua CDP, điền form tự động, chờ kéo captcha trực tiếp và lấy cookie_token_v2."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen = MagicMock()
        mock_urlopen.__enter__.return_value = mock_resp

        mock_page = MagicMock()
        mock_page.url = "https://www.hoyolab.com"
        mock_context = MagicMock()
        mock_page.context = mock_context
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        mock_pw = MagicMock()
        mock_pw.chromium.connect_over_cdp.return_value = mock_browser

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_profile = Path(tmp_dir) / "test_cdp_profile"
            temp_profile.mkdir()

            with patch("urllib.request.urlopen", return_value=mock_urlopen), \
                 patch("playwright.sync_api.sync_playwright") as mock_sync_pw, \
                 patch("gacard.hoyolab_login._fill_login_form_sync", return_value=True) as mock_fill, \
                 patch("gacard.hoyolab_login._detect_login_error", return_value=None), \
                 patch("gacard.hoyolab_login._captcha_visible", side_effect=[True, False]), \
                 patch("gacard.hoyolab_login._signed_in", side_effect=[False, True, True]):

                mock_sync_pw.return_value.__enter__.return_value = mock_pw
                mock_context.cookies.return_value = [
                    {"name": "ltuid_v2", "value": "123456"},
                    {"name": "ltoken_v2", "value": "mock_token"},
                    {"name": "cookie_token_v2", "value": "mock_cookie_token"},
                ]

                stages = []
                cookies = login_with_password_cdp(
                    "my_user",
                    "my_pass",
                    cdp_wait_seconds=1.0,
                    captcha_timeout_seconds=5.0,
                    on_stage=lambda s, m: stages.append(s),
                    profile_dir=temp_profile,
                )

                # Xác nhận bot tự động điền form
                mock_fill.assert_called_once_with(mock_page, "my_user", "my_pass", on_log=unittest.mock.ANY)

                # Xác nhận đã điều hướng sang Battle Chronicle lấy cookie_token_v2
                mock_page.goto.assert_any_call(BATTLE_CHRONICLE_URL, timeout=15000)

                # Xác nhận các giai đoạn thông báo
                self.assertIn("waiting_browser", stages)
                self.assertIn("waiting_captcha", stages)
                self.assertIn("fetching_data", stages)

                # Xác nhận cookie trả về đủ
                self.assertIn("ltuid_v2=123456", cookies.cookie)
                self.assertIn("ltoken_v2=mock_token", cookies.cookie)
                self.assertIn("cookie_token_v2=mock_cookie_token", cookies.cookie_token or "")

                # Xác nhận đóng trình duyệt CDP
                mock_browser.close.assert_called()

                # Xác nhận dọn dẹp profile tạm
                self.assertFalse(temp_profile.exists())

    def test_cdp_too_many_requests_stops_immediately(self):
        """Kiểm tra chế độ CDP: gặp Too many requests thì dừng ngay lập tức, không chờ hết timeout."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen = MagicMock()
        mock_urlopen.__enter__.return_value = mock_resp

        mock_page = MagicMock()
        mock_page.url = "https://www.hoyolab.com"
        mock_context = MagicMock()
        mock_page.context = mock_context
        mock_context.pages = [mock_page]

        mock_browser = MagicMock()
        mock_browser.contexts = [mock_context]

        mock_pw = MagicMock()
        mock_pw.chromium.connect_over_cdp.return_value = mock_browser

        with patch("urllib.request.urlopen", return_value=mock_urlopen), \
             patch("playwright.sync_api.sync_playwright") as mock_sync_pw, \
             patch("gacard.hoyolab_login._fill_login_form_sync", return_value=True), \
             patch("gacard.hoyolab_login._detect_login_error", return_value="Too many requests"), \
             patch("gacard.hoyolab_login._signed_in", return_value=False), \
             patch("gacard.hoyolab_login._captcha_visible", return_value=False):

            mock_sync_pw.return_value.__enter__.return_value = mock_pw

            with self.assertRaises(LoginError) as ctx:
                login_with_password_cdp("user", "pass", cdp_wait_seconds=1.0)

            self.assertEqual(ctx.exception.kind, "too_many_requests")
            mock_browser.close.assert_called()

    def test_cdp_cleanup_profile_and_password_in_single_worker(self):
        """Kiểm tra luồng run_single_job_thread dọn sạch mật khẩu và profile CDP trong finally."""
        from gacard.webapp import run_single_job_thread
        with tempfile.TemporaryDirectory() as tmp:
            state = WebState(Path(tmp))
            job = SingleJob(id="job_cdp_clean", code="test_code", username="u", password="secret_pass_123")
            state.single_jobs[job.id] = job

            with patch("gacard.webapp.process_single_account_pipeline", side_effect=RuntimeError("Simulated fail")):
                run_single_job_thread(job, state)

            self.assertEqual(job.status, "error")
            # Mật khẩu và username bị xóa sạch khỏi memory
            self.assertEqual(job.password, "")
            self.assertEqual(job.username, "")


    def test_save_acc_json_with_enka_extras(self):
        """Kiểm tra save_acc_json lưu đúng đối tượng EnkaExtras thành dict 3 trường sạch không chứa secret."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            fake_extras = hoyolab.EnkaExtras(world_level=7, namecard_icon="fake_icon.png", source="Enka")
            json_file = save_acc_json(
                out_dir=out_dir,
                code="mock_acc_99",
                uid="899999999",
                index_data={"role": {"Nickname": "MockUser"}},
                list_data=[],
                extras=fake_extras,
                wish_lines=[],
                chars_acc=["amber"],
            )
            self.assertTrue(json_file.exists())
            raw_text = json_file.read_text(encoding="utf-8")
            data = json.loads(raw_text)
            self.assertIsInstance(data.get("extras"), dict)
            self.assertEqual(data["extras"], {"world_level": 7, "namecard_icon": "fake_icon.png", "source": "Enka"})
            for secret in ("password", "cookie", "token", "authkey", "ltuid_v2", "ltoken_v2"):
                self.assertNotIn(secret, raw_text.lower())

    def test_reroll_account_from_json_restores_enka_extras(self):
        """Kiểm tra reroll_account_from_json dựng lại EnkaExtras từ dict và không gọi HoYoLAB."""
        with tempfile.TemporaryDirectory() as tmp:
            state = WebState(Path(tmp))
            work_dir = Path(tmp) / "work"
            work_dir.mkdir()
            json_path = Path(tmp) / "acc-mock_99-899999999.json"
            json_path.write_text(json.dumps({
                "code": "mock_99",
                "uid": "899999999",
                "chars_in_account": ["amber"],
                "index_data": {"role": {"Nickname": "MockUser"}},
                "list_data": [],
                "extras": {"world_level": 8, "namecard_icon": "mock_icon.png", "source": "Enka"},
                "wish_lines": [],
            }), encoding="utf-8")
            with patch("gacard.hoyolab.build_sheet_from_data", return_value=(MagicMock(), MagicMock())) as mock_build,                  patch("gacard.webapp.render_account_card", return_value=(Path("mock.jpg"), "mock.jpg", "mock.jpg")),                  patch("gacard.webapp.login_with_password") as mock_login:
                reroll_account_from_json(json_path=json_path, work_dir=work_dir, state=state)
                mock_login.assert_not_called()
                passed_extras = mock_build.call_args[0][5]
                self.assertIsInstance(passed_extras, hoyolab.EnkaExtras)
                self.assertEqual(passed_extras.world_level, 8)
                self.assertEqual(passed_extras.namecard_icon, "mock_icon.png")
                self.assertEqual(passed_extras.source, "Enka")


if __name__ == "__main__":
    unittest.main()
