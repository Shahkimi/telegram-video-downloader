"""Logging to a small rotating file plus an in-memory tail for the Diagnostics screen."""
from __future__ import annotations

import logging
import logging.handlers
from collections import deque

from .paths import AppPaths

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class RingHandler(logging.Handler):
    def __init__(self, capacity: int = 400):
        super().__init__()
        self.lines: deque[str] = deque(maxlen=capacity)
        self.setFormatter(logging.Formatter(_FORMAT, "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass


_ring = RingHandler()
_configured = False


def setup_logging(paths: AppPaths, debug: bool = False) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger("tgdl")
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.addHandler(_ring)
    try:
        paths.log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(paths.log_file, maxBytes=512 * 1024, backupCount=1, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    except OSError:
        pass
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("telethon").addHandler(_ring)


def tail(count: int = 200) -> list[str]:
    return list(_ring.lines)[-count:]
