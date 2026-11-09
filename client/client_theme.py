"""Bảng màu + tiện ích hiển thị dùng chung. Không import mạng, không import widget.

Ý tưởng màu: nền xanh-đen như màn hình terminal, cyan là màu chủ đạo của khung,
mỗi loại thông tin có một màu riêng để nhìn lướt là biết:
  cyan = kênh GLOBAL / khung · magenta = tin riêng · amber = "bạn" + chưa đọc
  lime = online / thành công · đỏ = lỗi
"""
from __future__ import annotations

import os
import re
import zlib
from functools import lru_cache

from rich.cells import cell_len

# Đặt GLP_FX=0 để tắt hẳn hiệu ứng mưa ký tự (máy yếu / SSH chậm).
FX = os.environ.get("GLP_FX", "1") != "0"

PAL = {
    "ink": "#06080d", "night": "#0a0e16", "dusk": "#0f1520",
    "line": "#1a2433", "line2": "#2a3a52", "dim": "#4a5f7a",
    "mist": "#7b92ad", "haze": "#aebfd4", "text": "#dbe7f3",
    "cyan": "#2de2e6", "lime": "#9dff57", "magenta": "#ff4d9d",
    "amber": "#ffb454", "violet": "#a78bfa", "blue": "#5aa2ff",
    "red": "#ff5d5d", "orange": "#ff8a4c",
}

# Màu tên người gửi (băm từ id nên ổn định giữa các lần chạy). Không có amber: dành cho "bạn".
AUTHOR_COLORS = [PAL[k] for k in ("cyan", "lime", "magenta", "violet", "blue", "orange")]

_TOKEN = re.compile(r"@([a-z0-9]+)")


def skin(css: str) -> str:
    """Thay @tên_màu trong CSS bằng mã hex của bảng màu."""
    return _TOKEN.sub(lambda m: PAL.get(m.group(1), m.group(0)), css)


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


@lru_cache(maxsize=2048)
def _mix_q(a: str, b: str, q: int) -> str:
    ra, rb = _hex_rgb(a), _hex_rgb(b)
    t = q / 64
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(ra, rb))


def mix(a: str, b: str, t: float) -> str:
    """Trộn hai màu hex. t=0 → a, t=1 → b."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return _mix_q(a, b, int(round(t * 64)))


GRAD_STOPS = [PAL["cyan"], PAL["blue"], PAL["violet"]]


def grad(p: float) -> str:
    """Gradient tĩnh cyan → blue → violet, p trong [0, 1]."""
    p = 0.0 if p < 0 else 1.0 if p > 1 else p
    pos = p * (len(GRAD_STOPS) - 1)
    i = min(int(pos), len(GRAD_STOPS) - 2)
    return mix(GRAD_STOPS[i], GRAD_STOPS[i + 1], pos - i)


def author_color(key: str) -> str:
    return AUTHOR_COLORS[zlib.crc32(key.encode("utf-8", "replace")) % len(AUTHOR_COLORS)]


# ----------------------------------------------------------------- xử lý chữ
# Chặn ký tự điều khiển (kể cả ESC) và ký tự đảo chiều chữ (RTL override) từ người khác gửi tới,
# nếu không một tin nhắn độc có thể làm vỡ giao diện terminal của người nhận.
_CTRL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    return _CTRL.sub("", text)


def clip(text: str, width: int) -> str:
    """Cắt cho vừa `width` ô (tính theo độ rộng hiển thị), thêm … nếu bị cắt."""
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        cw = cell_len(ch)
        if used + cw > width - 1:
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


def _hard_split(word: str, width: int) -> list[str]:
    parts, piece, used = [], "", 0
    for ch in word:
        cw = cell_len(ch)
        if used + cw > width and piece:
            parts.append(piece)
            piece, used = "", 0
        piece += ch
        used += cw
    if piece:
        parts.append(piece)
    return parts


def wrap_text(text: str, width: int) -> list[str]:
    """Xuống dòng theo từ, tính theo độ rộng ô (đúng với tiếng Việt, emoji, CJK).
    Từ dài hơn cả dòng thì bị bẻ cứng."""
    width = max(width, 8)
    out: list[str] = []
    for para in text.split("\n"):
        cur, used = "", 0
        for tok in re.split(r"(\s+)", para):
            if not tok:
                continue
            tw = cell_len(tok)
            if tw <= width - used:
                cur += tok
                used += tw
                continue
            if tok.isspace():  # khoảng trắng đúng chỗ ngắt dòng thì bỏ
                out.append(cur.rstrip())
                cur, used = "", 0
                continue
            if cur.strip():
                out.append(cur.rstrip())
                cur, used = "", 0
            if tw > width:
                pieces = _hard_split(tok, width)
                out.extend(pieces[:-1])
                cur, used = pieces[-1], cell_len(pieces[-1])
            else:
                cur, used = tok, tw
        out.append(cur.rstrip())
    return out or [""]
