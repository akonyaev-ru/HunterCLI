"""Один автопилот на сеанс Windows.

Два экземпляра — это два движка на каждом аккаунте: второе поднятие уходит в
отказ, а продление токена превращается в гонку — кто первым потратит ключ
продления, тот и с доступом, второй его теряет и записывает пустой поверх
свежего. С автозапуском шанс запустить программу второй раз стал заметным:
задание сработало при входе, а человек по привычке открыл её сам.

Держим именованный мьютекс Windows. Имя в пространстве Local\\ — то есть на
сеанс пользователя: у другого пользователя той же машины свой автопилот.
Мьютекс освобождает сама система, когда процесс завершается любым способом.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

MUTEX_NAME = "Local\\HunterCLI-autopilot"

ERROR_ALREADY_EXISTS = 183

_handle = None


def acquire(name: str = MUTEX_NAME) -> bool:
    """Занять место. False — программа уже запущена в этом сеансе."""
    global _handle
    if sys.platform != "win32":
        return True
    if _handle is not None:
        return True
    try:
        # use_last_error: код ошибки сохраняется сразу после вызова, иначе сам
        # интерпретатор успевал бы его перезаписать до проверки.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel.CreateMutexW(None, False, name)
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    except Exception:
        # Без мьютекса лучше работать, чем не запуститься вовсе.
        return True
    if not handle:
        return True
    if already:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        return False
    _handle = handle
    return True


def release() -> None:
    """Освободить место раньше выхода из процесса (нужно тестам)."""
    global _handle
    if _handle is None or sys.platform != "win32":
        _handle = None
        return
    try:
        kernel = ctypes.WinDLL("kernel32")
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(_handle)
    except Exception:
        pass
    _handle = None
