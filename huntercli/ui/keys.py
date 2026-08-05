"""Неблокирующее чтение клавиш — чтобы дашборд оставался живым."""

from __future__ import annotations

import sys


class KeyReader:
    """Читает одиночные нажатия, не блокируя цикл отрисовки.

    Используется как контекстный менеджер: на POSIX терминал переводится в
    raw-режим и корректно восстанавливается на выходе.
    """

    def __init__(self) -> None:
        self._enabled = sys.stdin is not None and sys.stdin.isatty()
        self._windows = sys.platform == "win32"
        self._saved = None
        self._msvcrt = None
        if self._enabled and self._windows:
            try:
                import msvcrt

                self._msvcrt = msvcrt
            except ImportError:  # pragma: no cover
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def __enter__(self) -> "KeyReader":
        if self._enabled and not self._windows:
            try:
                import termios
                import tty

                self._saved = termios.tcgetattr(sys.stdin.fileno())
                tty.setcbreak(sys.stdin.fileno())
            except Exception:  # pragma: no cover
                self._enabled = False
        return self

    def __exit__(self, *exc: object) -> None:
        if self._saved is not None:
            try:
                import termios

                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._saved)
            except Exception:  # pragma: no cover
                pass
            self._saved = None

    def poll(self) -> str | None:
        """Вернуть нажатый символ или None, если ничего не нажато."""
        if not self._enabled:
            return None
        if self._windows:
            return self._poll_windows()
        return self._poll_posix()

    def _poll_windows(self) -> str | None:
        assert self._msvcrt is not None
        if not self._msvcrt.kbhit():
            return None
        char = self._msvcrt.getwch()
        if char in ("\x00", "\xe0"):  # функциональная клавиша — гасим хвост
            self._msvcrt.getwch()
            return None
        return char

    def _poll_posix(self) -> str | None:  # pragma: no cover - не Windows
        import select

        ready, _, _ = select.select([sys.stdin], [], [], 0)
        if not ready:
            return None
        return sys.stdin.read(1)
