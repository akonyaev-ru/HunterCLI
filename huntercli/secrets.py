"""Шифрование токенов в config.json.

На Windows используется DPAPI (CryptProtectData) через ctypes — без внешних
зависимостей. Данные привязаны к учётной записи пользователя: скопированный на
чужую машину config.json расшифровать нельзя.

На остальных платформах — только base64-обфускация: это НЕ защита, а способ
не светить токен при случайном показе файла. Об этом честно сказано в README.
"""

from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

_DPAPI_PREFIX = "dpapi:"
_PLAIN_PREFIX = "b64:"

_IS_WINDOWS = sys.platform == "win32"


if _IS_WINDOWS:

    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

        def as_bytes(self) -> bytes:
            return ctypes.string_at(self.pbData, self.cbData)

    def _blob(data: bytes) -> _Blob:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    def _free(blob: _Blob) -> None:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def _dpapi_protect(raw: bytes) -> bytes | None:
    if not _IS_WINDOWS:
        return None
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_blob(raw)), "HunterCLI", None, None, None, 0, ctypes.byref(out)
    )
    if not ok:
        return None
    try:
        return out.as_bytes()
    finally:
        _free(out)


def _dpapi_unprotect(raw: bytes) -> bytes | None:
    if not _IS_WINDOWS:
        return None
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_blob(raw)), None, None, None, None, 0, ctypes.byref(out)
    )
    if not ok:
        return None
    try:
        return out.as_bytes()
    finally:
        _free(out)


def protect(value: str) -> str:
    """Зашифровать строку для хранения в конфиге."""
    if not value:
        return ""
    raw = value.encode("utf-8")
    sealed = _dpapi_protect(raw)
    if sealed is not None:
        return _DPAPI_PREFIX + base64.b64encode(sealed).decode("ascii")
    return _PLAIN_PREFIX + base64.b64encode(raw).decode("ascii")


def unprotect(value: str) -> str:
    """Расшифровать строку из конфига. Пустая строка = не смогли."""
    if not value:
        return ""
    try:
        if value.startswith(_DPAPI_PREFIX):
            raw = base64.b64decode(value[len(_DPAPI_PREFIX):])
            opened = _dpapi_unprotect(raw)
            return opened.decode("utf-8") if opened else ""
        if value.startswith(_PLAIN_PREFIX):
            return base64.b64decode(value[len(_PLAIN_PREFIX):]).decode("utf-8")
    except Exception:
        return ""
    # Конфиг первой версии хранил токен как есть.
    return value
