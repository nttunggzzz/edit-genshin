"""Đọc file Excel (.xlsx) và CSV cho chức năng ghép hàng loạt bằng thư viện chuẩn Python."""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

CODE_RE = re.compile(r"^[A-Za-z0-9#_\-]{1,16}$")
CELL_REF_RE = re.compile(r"^([A-Za-z]+)(\d+)$")

HEADER_KEYWORDS = ("mã", "ma", "code", "stt", "tài khoản", "tai khoan", "account", "username")


@dataclass
class BatchRow:
    row_num: int
    code: str = ""
    username: str = ""
    password: str = ""
    background: str | None = None
    frame_color: str | None = None
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.error is None and bool(self.code and self.username and self.password)


def col_to_index(col_str: str) -> int:
    """Đổi tên cột Excel ('A', 'B', 'AA'...) thành chỉ số 0-based."""
    idx = 0
    for char in col_str.upper():
        idx = idx * 26 + (ord(char) - ord("A") + 1)
    return idx - 1


def _parse_xlsx_bytes(data: bytes) -> list[list[str]]:
    """Phân tích .xlsx bằng zipfile + xml thuần (chuẩn OOXML)."""
    with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
        namelist = zf.namelist()

        # 1. Đọc shared strings nếu có
        shared_strings: list[str] = []
        sst_path = next((name for name in namelist if name.lower() == "xl/sharedstrings.xml"), None)
        if sst_path:
            tree = ET.fromstring(zf.read(sst_path))
            for si in tree.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
                # Gom text từ mọi thẻ t bên trong
                text = "".join(node.text or "" for node in si.iter() if node.tag.endswith("t"))
                shared_strings.append(text)

        # 2. Tìm sheet đầu tiên
        sheet_path = next((name for name in namelist if name.lower().startswith("xl/worksheets/sheet")), None)
        if not sheet_path:
            return []

        sheet_tree = ET.fromstring(zf.read(sheet_path))
        rows_data: list[list[str]] = []

        for row_elem in sheet_tree.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
            cells_dict: dict[int, str] = {}
            max_col = -1

            for c_elem in row_elem.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                ref = c_elem.attrib.get("r", "")
                m = CELL_REF_RE.match(ref)
                if not m:
                    continue
                col_idx = col_to_index(m.group(1))
                if col_idx > max_col:
                    max_col = col_idx

                cell_type = c_elem.attrib.get("t", "")
                val = ""

                # Cell inline string
                is_elem = c_elem.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}is")
                if is_elem is not None:
                    val = "".join(node.text or "" for node in is_elem.iter() if node.tag.endswith("t"))
                else:
                    v_elem = c_elem.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                    raw_val = v_elem.text if v_elem is not None and v_elem.text is not None else ""
                    if cell_type == "s" and raw_val.isdigit():
                        idx = int(raw_val)
                        val = shared_strings[idx] if idx < len(shared_strings) else raw_val
                    else:
                        val = raw_val

                cells_dict[col_idx] = val.strip()

            if max_col >= 0:
                row_list = [cells_dict.get(i, "") for i in range(max_col + 1)]
                rows_data.append(row_list)

        return rows_data


def _parse_csv_bytes(data: bytes) -> list[list[str]]:
    """Phân tích file CSV với nhiều bộ mã (UTF-8, UTF-8-SIG, CP1252, Latin-1)."""
    text = ""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return []

    # Nhận diện delimiter
    sample = text[:2048]
    delimiter = ","
    for d in (",", "\t", ";"):
        if sample.count(d) > sample.count(delimiter):
            delimiter = d

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [[col.strip() for col in row] for row in reader]


def parse_batch_rows(file_bytes: bytes, filename: str) -> list[BatchRow]:
    """Đọc toàn bộ file Excel/CSV thành danh sách BatchRow.

    - Bỏ qua hàng tiêu đề nếu hàng đầu không phải mã hợp lệ.
    - Bỏ qua hàng trống hoàn toàn.
    - Hàng thiếu thông tin hoặc sai mã sẽ được gắn lỗi rõ ràng để báo cáo.
    """
    is_xlsx = filename.lower().endswith(".xlsx") or file_bytes[:4] == b"PK\x03\x04"
    try:
        raw_rows = _parse_xlsx_bytes(file_bytes) if is_xlsx else _parse_csv_bytes(file_bytes)
    except Exception as exc:
        return [BatchRow(row_num=1, error=f"Không thể đọc file {filename}: {exc}")]

    if not raw_rows:
        return []

    result: list[BatchRow] = []

    # Kiểm tra xem hàng đầu tiên có phải tiêu đề không
    start_idx = 0
    first_row = raw_rows[0]
    first_col = first_row[0].strip().lower() if first_row else ""
    if any(k in first_col for k in HEADER_KEYWORDS) or (first_col and not CODE_RE.match(first_col)):
        # Hàng 1 nhiều khả năng là header
        start_idx = 1

    for row_idx in range(start_idx, len(raw_rows)):
        raw = raw_rows[row_idx]
        display_num = row_idx + 1

        # Bỏ qua hàng hoàn toàn rỗng
        if not any(c.strip() for c in raw):
            continue

        code = raw[0].strip() if len(raw) > 0 else ""
        username = raw[1].strip() if len(raw) > 1 else ""
        password = raw[2].strip() if len(raw) > 2 else ""
        bg = raw[3].strip() if len(raw) > 3 and raw[3].strip() else None
        frame_color = raw[4].strip() if len(raw) > 4 and raw[4].strip() else None

        # Kiểm tra thiếu thông tin
        if not code or not username or not password:
            missing = []
            if not code:
                missing.append("mã")
            if not username:
                missing.append("tài khoản")
            if not password:
                missing.append("mật khẩu")
            result.append(BatchRow(
                row_num=display_num,
                code=code,
                username=username,
                password="",
                error=f"Thiếu {'/'.join(missing)}",
            ))
            continue

        # Kiểm tra regex mã acc
        if not CODE_RE.match(code):
            result.append(BatchRow(
                row_num=display_num,
                code=code,
                username=username,
                password="",
                error=f"Mã acc '{code}' không hợp lệ (yêu cầu 1-16 ký tự chữ, số, _, -, #)",
            ))
            continue

        result.append(BatchRow(
            row_num=display_num,
            code=code,
            username=username,
            password=password,
            background=bg,
            frame_color=frame_color,
            error=None,
        ))

    return result
