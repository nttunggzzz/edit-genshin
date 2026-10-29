"""Trang web tạm để người dùng tự kéo slider captcha HoYoLAB từ xa.

Vấn đề: bot chạy trên server thì người dùng không thấy cửa sổ trình duyệt
của bot, và Telegram cũng không kéo slider được. Giải pháp ở đây là một máy
chủ HTTP nhỏ, chỉ sống trong một lần đăng nhập:

* GET /?t=<token>: trang HTML hiển thị ảnh chụp mới nhất của trang
  Playwright, người dùng kéo chuột trên ảnh đó.
* POST /drag (token + tọa độ chuẩn hóa 0-1000): lưu vào hàng đợi để
  luồng Playwright thực hiện kéo chuột (không gọi trực tiếp từ thread HTTP).
* GET /shot?t=<token>: ảnh PNG mới nhất do luồng Playwright định kỳ chụp,
  luôn trả về Content-Type: image/png với Content-Length chính xác.
"""

from __future__ import annotations

import io
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw

PAGE_HTML = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kéo captcha HoYoLAB</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  margin: 0;
  padding: 24px 16px;
  background: #0f172a;
  color: #f8fafc;
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 100vh;
}
.container {
  width: 100%;
  max-width: 960px;
}
.header {
  margin-bottom: 20px;
}
h2 {
  color: #38bdf8;
  font-size: 22px;
  font-weight: 700;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 10px;
}
.live-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(34, 197, 94, 0.15);
  color: #4ade80;
  border: 1px solid rgba(34, 197, 94, 0.3);
  padding: 2px 8px;
  border-radius: 9999px;
}
.live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #4ade80;
  box-shadow: 0 0 6px #4ade80;
}
p.guide {
  color: #94a3b8;
  font-size: 14px;
  line-height: 1.6;
}
.card {
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 12px;
  padding: 12px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
  position: relative;
  overflow: hidden;
}
.img-wrapper {
  position: relative;
  width: 100%;
  background: #0f172a;
  border-radius: 8px;
  overflow: hidden;
  user-select: none;
  -webkit-user-select: none;
}
img#cap {
  width: 100%;
  height: auto;
  display: block;
  touch-action: none;
  cursor: grab;
  user-select: none;
  -webkit-user-select: none;
  -webkit-user-drag: none;
}
img#cap:active {
  cursor: grabbing;
}
.error-msg {
  display: none;
  color: #f87171;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid #ef4444;
  padding: 12px 16px;
  border-radius: 8px;
  margin-top: 14px;
  font-weight: 600;
  font-size: 14px;
  line-height: 1.5;
}
.status-bar {
  margin-top: 10px;
  font-size: 13px;
  color: #64748b;
  display: flex;
  justify-content: space-between;
}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h2>
      Kéo slider captcha HoYoLAB
      <span class="live-badge"><span class="live-dot"></span>Trực tiếp</span>
    </h2>
    <p class="guide">
      Kéo chuột (hoặc ngón tay) trên ảnh từ nút trượt tới điểm ghép hình. Ảnh tự làm mới mỗi 800ms.
      Vui lòng giữ trang này mở cho đến khi hệ thống báo đăng nhập thành công.
    </p>
  </div>

  <div class="card">
    <div class="img-wrapper">
      <img id="cap" src="/shot?t={token}" alt="Ảnh trình duyệt" draggable="false">
    </div>
    <div id="err" class="error-msg">Không chụp được trình duyệt, vui lòng kiểm tra cửa sổ Playwright còn mở không.</div>
    <div class="status-bar">
      <span id="sync-info">Đang kết nối...</span>
      <span>Cập nhật: 800ms</span>
    </div>
  </div>
</div>

<script>
const token = '{token}';
const img = document.getElementById('cap');
const errBox = document.getElementById('err');
const syncInfo = document.getElementById('sync-info');
let failCount = 0;
let isRefreshing = false;

function refreshImage() {
  if (isRefreshing) return;
  isRefreshing = true;

  const newImg = new Image();
  newImg.onload = () => {
    failCount = 0;
    img.src = newImg.src;
    errBox.style.display = 'none';
    syncInfo.innerText = 'Đang đồng bộ trực tiếp (' + new Date().toLocaleTimeString() + ')';
    isRefreshing = false;
  };
  newImg.onerror = () => {
    failCount++;
    if (failCount >= 3) {
      errBox.style.display = 'block';
      syncInfo.innerText = 'Lỗi chụp màn hình (thất bại 3 lần)';
    }
    isRefreshing = false;
  };
  newImg.src = '/shot?t=' + encodeURIComponent(token) + '&x=' + Date.now();
}

setInterval(refreshImage, 800);

let sx = 0, sy = 0, dragging = false;

function pos(e) {
  const r = img.getBoundingClientRect();
  let cx = e.clientX, cy = e.clientY;
  if (e.touches && e.touches.length > 0) {
    cx = e.touches[0].clientX;
    cy = e.touches[0].clientY;
  } else if (e.changedTouches && e.changedTouches.length > 0) {
    cx = e.changedTouches[0].clientX;
    cy = e.changedTouches[0].clientY;
  }
  return [
    Math.max(0, Math.min(1000, Math.round((cx - r.left) / r.width * 1000))),
    Math.max(0, Math.min(1000, Math.round((cy - r.top) / r.height * 1000)))
  ];
}

img.addEventListener('dragstart', e => e.preventDefault());

img.addEventListener('mousedown', e => {
  e.preventDefault();
  dragging = true;
  [sx, sy] = pos(e);
});

img.addEventListener('touchstart', e => {
  dragging = true;
  [sx, sy] = pos(e);
}, { passive: true });

function sendDrag(ex, ey) {
  if (!dragging) return;
  dragging = false;
  syncInfo.innerText = 'Đang gửi thao tác kéo chuột...';
  fetch('/drag', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ t: token, x0: sx, y0: sy, x1: ex, y1: ey })
  }).then(res => {
    if (res.ok) {
      setTimeout(refreshImage, 200);
    }
  }).catch(() => {});
}

window.addEventListener('mouseup', e => {
  if (!dragging) return;
  const [ex, ey] = pos(e);
  sendDrag(ex, ey);
});

img.addEventListener('touchend', e => {
  if (!dragging) return;
  const [ex, ey] = pos(e);
  sendDrag(ex, ey);
});
</script>
</body>
</html>
"""


def make_placeholder_png(text: str = "Đang tải ảnh captcha...") -> bytes:
    """Tạo ảnh PNG placeholder khi chưa có ảnh hoặc lỗi chụp."""
    img = Image.new("RGB", (800, 450), color=(15, 23, 42))
    draw = ImageDraw.Draw(img)
    draw.rectangle([8, 8, 792, 442], outline=(51, 65, 85), width=2)

    font = None
    for font_name in ("segoeui.ttf", "arial.ttf"):
        try:
            from PIL import ImageFont

            font = ImageFont.truetype(font_name, 22)
            break
        except Exception:
            continue

    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = max(20, (800 - tw) // 2)
        y = max(20, (450 - th) // 2)
    except Exception:
        x, y = 40, 210

    draw.text((x, y), text, fill=(248, 250, 252), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class CaptchaWebServer:
    """Máy chủ captcha tạm, gắn với một Playwright page đang mở."""

    def __init__(self, page: Any, port: int = 8765):
        self.page = page
        self.port = int(port)
        self.token = secrets.token_hex(16)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._screenshot_bytes: bytes = make_placeholder_png("Đang chuẩn bị ảnh chụp trình duyệt...")
        self._fail_count: int = 0
        self._pending_drag: dict[str, Any] | None = None

    def tick(self) -> None:
        """Gọi từ luồng Playwright: thực thi kéo chuột (nếu có) và chụp ảnh màn hình mới nhất."""
        # 1. Xử lý thao tác kéo nếu có
        drag_data = None
        with self._lock:
            if self._pending_drag is not None:
                drag_data = self._pending_drag
                self._pending_drag = None
        if drag_data is not None:
            try:
                self._execute_drag(drag_data)
            except Exception as e:
                print(f"[CaptchaWeb] Loi thuc hien keo chuot: {type(e).__name__}", flush=True)

        # 2. Chụp ảnh màn hình trên chính luồng Playwright
        try:
            shot = self.page.screenshot()
            if shot and len(shot) > 0:
                with self._lock:
                    self._screenshot_bytes = shot
                    self._fail_count = 0
        except Exception as exc:
            with self._lock:
                self._fail_count += 1
                if self._fail_count >= 3:
                    self._screenshot_bytes = make_placeholder_png(
                        "Không chụp được trình duyệt, kiểm tra cửa sổ Playwright còn mở không."
                    )
            print(f"[CaptchaWeb] Chup anh that bai (lan {self._fail_count}): {type(exc).__name__}", flush=True)

    def queue_drag(self, payload: dict[str, Any]) -> None:
        """Đưa thao tác kéo vào hàng đợi để luồng Playwright thực hiện."""
        with self._lock:
            self._pending_drag = payload

    def get_screenshot(self) -> tuple[bytes, int]:
        """Đọc bytes ảnh chụp gần nhất một cách an toàn đa luồng."""
        with self._lock:
            return self._screenshot_bytes, self._fail_count

    def _execute_drag(self, payload: dict[str, Any]) -> None:
        """Chuyển tiếp một lần kéo của người dùng sang Playwright (chạy trên luồng Playwright)."""
        size = self.page.viewport_size or {"width": 1280, "height": 800}
        width, height = int(size["width"]), int(size["height"])
        x0 = int(payload.get("x0", 0)) / 1000 * width
        y0 = int(payload.get("y0", 0)) / 1000 * height
        x1 = int(payload.get("x1", 0)) / 1000 * width
        y1 = int(payload.get("y1", 0)) / 1000 * height
        mouse = self.page.mouse
        mouse.move(x0, y0)
        mouse.down()
        steps = 25
        for i in range(1, steps + 1):
            cur_x = x0 + (x1 - x0) * i / steps
            cur_y = y0 + (y1 - y0) * i / steps
            mouse.move(cur_x, cur_y)
            self.page.wait_for_timeout(20)
        self.page.wait_for_timeout(100)
        mouse.up()
        print(f"[CaptchaWeb] Da chuyen tiep keo chuot: ({int(x0)}, {int(y0)}) -> ({int(x1)}, {int(y1)})", flush=True)

    def start(self, base_url: str = "") -> str:
        """Chạy máy chủ (thread nền) và trả link cho người dùng mở."""
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass  # Không dùng log mặc định của http.server

            def _ok(self, body: bytes, ctype: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(body)

            def _deny(self) -> None:
                self.send_response(403)
                self.end_headers()

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                req_token = qs.get("t", [""])[0]

                if req_token != server.token:
                    print(f"[CaptchaWeb /shot] status=403, bytes=0, error=InvalidToken", flush=True)
                    self._deny()
                    return

                if parsed.path == "/shot":
                    data, fails = server.get_screenshot()
                    if not data:
                        data = make_placeholder_png("Đang tải ảnh captcha...")
                    err_msg = "None" if fails < 3 else "CaptureFailed3Times"
                    print(f"[CaptchaWeb /shot] status=200, bytes={len(data)}, error={err_msg}", flush=True)
                    self._ok(data, "image/png")
                    return

                html = PAGE_HTML.replace("{token}", server.token)
                self._ok(html.encode("utf-8"), "text/html; charset=utf-8")

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw_data = self.rfile.read(length) if length > 0 else b"{}"
                    payload = json.loads(raw_data.decode("utf-8"))
                except Exception:
                    self._deny()
                    return

                if payload.get("t") != server.token:
                    self._deny()
                    return

                if parsed.path == "/drag":
                    server.queue_drag(payload)
                    self._ok(b"ok", "text/plain")
                    return

                self._deny()

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        base = (base_url or "").rstrip("/")
        if base:
            return f"{base}/?t={self.token}"
        return f"http://localhost:{self.port}/?t={self.token}"

    def stop(self) -> None:
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None
