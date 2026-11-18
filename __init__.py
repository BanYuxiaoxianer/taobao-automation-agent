"""商家智能运营 Agent · 深加工版（by 31）

在 Windows 上被 import 时顺手把 stdout 切到 UTF-8，
避免 ✅ / ⚠️ 这类字符在 GBK 控制台里直接把程序打崩。
"""

from __future__ import annotations

import os
import sys


def _force_utf8() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


if os.name == "nt":
    _force_utf8()

__version__ = "0.0.1"
__author__ = "31"
