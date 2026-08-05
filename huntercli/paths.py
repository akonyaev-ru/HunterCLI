"""Пути к файлам приложения.

Конфиг и лог лежат рядом с .exe (или со скриптом при запуске из исходников),
чтобы утилиту можно было носить на флешке. Служебные файлы (handoff от
обработчика протокола, lock-файл) — в %LOCALAPPDATA%, потому что рядом с .exe
может не быть прав на запись.
"""

from __future__ import annotations

import os
import sys

CONFIG_NAME = "config.json"
LOG_NAME = "hunter.log"


def base_dir() -> str:
    """Директория, рядом с которой лежит .exe или main.py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path() -> str:
    return os.path.join(base_dir(), CONFIG_NAME)


def log_path() -> str:
    return os.path.join(base_dir(), LOG_NAME)


def state_dir() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    path = os.path.join(root, "HunterCLI")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = base_dir()
    return path


def handoff_path() -> str:
    """Файл, через который экземпляр-обработчик hh-android:// передаёт код."""
    return os.path.join(state_dir(), "oauth_handoff.json")


def writable(path: str) -> bool:
    directory = os.path.dirname(path) or "."
    return os.access(directory, os.W_OK)
