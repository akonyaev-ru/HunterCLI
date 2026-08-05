"""Журнал событий: кольцевой буфер для экрана + файл на диске."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass

from . import paths

MAX_LOG_BYTES = 1_000_000

OK = "ok"
INFO = "info"
WARN = "warn"
ERROR = "error"
STEP = "step"


@dataclass(frozen=True)
class Entry:
    at: float
    level: str
    text: str
    #: Сквозной номер записи — по нему безопасно продолжать чтение журнала.
    seq: int = 0

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.at))


class LogBus:
    """Потокобезопасный журнал. Пишет в файл и хранит хвост для дашборда."""

    def __init__(self, capacity: int = 400, to_file: bool = True) -> None:
        self._lock = threading.Lock()
        self._entries: deque[Entry] = deque(maxlen=capacity)
        self._file_ok = to_file
        self._revision = 0
        if to_file:
            self._rotate()

    # ------------------------------------------------------------- запись

    def _rotate(self) -> None:
        path = paths.log_path()
        try:
            if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
                backup = path + ".1"
                if os.path.exists(backup):
                    os.remove(backup)
                os.replace(path, backup)
        except OSError:
            self._file_ok = False

    def _write_file(self, entry: Entry) -> None:
        if not self._file_ok:
            return
        path = paths.log_path()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry.at))
        # Новый файл начинаем с BOM: иначе «Блокнот» и Excel читают русские
        # строки как кракозябры. К уже существующему файлу просто дописываем.
        try:
            fresh = not os.path.exists(path) or os.path.getsize(path) == 0
        except OSError:
            fresh = False
        try:
            with open(path, "a", encoding="utf-8-sig" if fresh else "utf-8") as fh:
                fh.write(f"{stamp} [{entry.level.upper():5}] {entry.text}\n")
        except OSError:
            self._file_ok = False

    def add(self, level: str, text: str) -> None:
        with self._lock:
            self._revision += 1
            entry = Entry(time.time(), level, text, self._revision)
            self._entries.append(entry)
        self._write_file(entry)

    def ok(self, text: str) -> None:
        self.add(OK, text)

    def info(self, text: str) -> None:
        self.add(INFO, text)

    def step(self, text: str) -> None:
        self.add(STEP, text)

    def warn(self, text: str) -> None:
        self.add(WARN, text)

    def error(self, text: str) -> None:
        self.add(ERROR, text)

    # ------------------------------------------------------------- чтение

    def tail(self, count: int) -> list[Entry]:
        with self._lock:
            if count <= 0:
                return []
            return list(self._entries)[-count:]

    def since(self, seq: int) -> list[Entry]:
        """Записи новее указанного номера — для дозаписи без пропусков и дублей."""
        with self._lock:
            return [entry for entry in self._entries if entry.seq > seq]

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def file_enabled(self) -> bool:
        return self._file_ok
