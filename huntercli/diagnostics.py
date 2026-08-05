"""Самопроверка окружения: `HunterCLI.exe --check`.

Нужна прежде всего для собранного .exe: убедиться, что встроенный браузер
(WebView2) и сеть на месте, не запуская при этом настоящий вход.
"""

from __future__ import annotations

import os
import sys

from . import __version__, auth, paths


def _line(ok: bool | None, label: str, detail: str = "") -> str:
    mark = {True: "[ OK ]", False: "[ ! ]", None: "[ .. ]"}[ok]
    return f"{mark} {label}" + (f"  —  {detail}" if detail else "")


def _pywebview_version() -> str:
    """Версию pywebview в собранном .exe достать удаётся не всегда — это норма."""
    try:
        import webview

        version = getattr(webview, "__version__", "")
        if version:
            return f"версия {version}"
    except Exception:
        return ""
    try:
        from importlib.metadata import version as package_version

        return f"версия {package_version('pywebview')}"
    except Exception:
        return "версия неизвестна"


def _webview2_runtime() -> tuple[bool, str]:
    """Установлен ли Microsoft Edge WebView2 Runtime."""
    if sys.platform != "win32":
        return False, "не Windows"
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return False, "нет winreg"

    key_paths = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients"
         r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\EdgeUpdate\Clients"
         r"\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
    ]
    for root, path in key_paths:
        try:
            with winreg.OpenKey(root, path) as key:
                version, _ = winreg.QueryValueEx(key, "pv")
                if version:
                    return True, f"версия {version}"
        except OSError:
            continue
    return False, "не найден — поставьте Microsoft Edge WebView2 Runtime"


def run() -> int:
    print(f"Hunter CLI {__version__} — проверка окружения")
    print(f"Python {sys.version.split()[0]}, сборка: "
          f"{'exe' if getattr(sys, 'frozen', False) else 'исходники'}")
    print()

    problems = 0

    for module in ("requests", "rich"):
        try:
            __import__(module)
            print(_line(True, f"библиотека {module}"))
        except Exception as exc:
            problems += 1
            print(_line(False, f"библиотека {module}", str(exc)))

    available, error = auth.webview_available()
    if available:
        print(_line(True, "встроенный браузер (pywebview)", _pywebview_version()))
    else:
        problems += 1
        print(_line(False, "встроенный браузер (pywebview)", error))
        print("       вход придётся делать через браузер или вручную")

    ok, detail = _webview2_runtime()
    print(_line(ok, "Microsoft Edge WebView2 Runtime", detail))
    if not ok and available:
        problems += 1

    config = paths.config_path()
    can_write = paths.writable(config)
    print(_line(can_write, "права на запись рядом с программой", os.path.dirname(config)))
    if not can_write:
        problems += 1
    saved = os.path.exists(config)
    print(_line(True if saved else None, "config.json",
                "найден" if saved else "ещё нет — вход при первом запуске"))

    try:
        import requests

        response = requests.get("https://api.hh.ru/", timeout=15)
        print(_line(response.status_code == 200, "связь с сервисом",
                    f"код {response.status_code}"))
        if response.status_code != 200:
            problems += 1
    except Exception as exc:
        problems += 1
        print(_line(False, "связь с сервисом", str(exc)[:90]))

    print()
    if problems:
        print(f"Найдено проблем: {problems}. Смотрите строки с [ ! ].")
    else:
        print("Всё в порядке — можно запускать.")
    return 1 if problems else 0
