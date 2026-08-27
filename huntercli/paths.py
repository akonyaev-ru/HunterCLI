"""Пути к файлам приложения.

Всё, что программа пишет — конфиг, журнал, служебные файлы, — лежит в
%LOCALAPPDATA%\\HunterCLI. Рядом с .exe не остаётся ничего: программу кладут
в «Загрузки» или на рабочий стол, и подсыпать туда свои файлы некрасиво, а
в Program Files на это ещё и нет прав.

Файлы из прежних версий, лежавшие рядом с программой, переносятся сюда при
первом запуске — иначе потерялись бы авторизация и накопленная статистика.
"""

from __future__ import annotations

import os
import shutil
import sys

CONFIG_NAME = "config.json"
LOG_NAME = "hunter.log"
HISTORY_NAME = "stats.json"


def base_dir() -> str:
    """Директория, рядом с которой лежит .exe или main.py."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def state_dir() -> str:
    """Папка для всего, что программа пишет на диск."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    path = os.path.join(root, "HunterCLI")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = base_dir()
    return path


def config_path() -> str:
    return os.path.join(state_dir(), CONFIG_NAME)


def log_path() -> str:
    return os.path.join(state_dir(), LOG_NAME)


def history_path() -> str:
    """История просмотров.

    Отдельно от конфига: там миграции схемы и зашифрованные доступы, а
    здесь открытые числа, которые не жалко.
    """
    return os.path.join(state_dir(), HISTORY_NAME)


def handoff_path() -> str:
    """Файл, через который экземпляр-обработчик hh-android:// передаёт код."""
    return os.path.join(state_dir(), "oauth_handoff.json")


def adopt_legacy_files() -> list[str]:
    """Забрать конфиг и журнал, оставшиеся рядом с программой от версий до 2026.7.

    Возвращает имена перенесённых файлов — их показывают в журнале, чтобы
    пропажа файлов из папки программы не выглядела необъяснимой.

    Целевой файл не затираем: если в новом месте уже что-то есть, оно свежее.
    Старый при этом всё равно убираем — ради того всё и затевалось.
    """
    moved: list[str] = []
    source_dir = base_dir()
    target_dir = state_dir()
    if os.path.normcase(source_dir) == os.path.normcase(target_dir):
        return moved

    for name in (CONFIG_NAME, LOG_NAME):
        source = os.path.join(source_dir, name)
        if not os.path.isfile(source):
            continue
        target = os.path.join(target_dir, name)
        try:
            if os.path.exists(target):
                os.remove(source)
            else:
                shutil.move(source, target)
                moved.append(name)
        except OSError:
            # Не смогли — и ладно: программе это не мешает, а лезть с ошибкой
            # к пользователю из-за уборки старых файлов незачем.
            continue
    return moved


def writable(path: str) -> bool:
    directory = os.path.dirname(path) or "."
    return os.access(directory, os.W_OK)
