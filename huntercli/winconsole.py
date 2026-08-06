"""Значок и заголовок окна консоли.

Про значок в панели задач стоит сказать прямо: у консольной программы своего
окна нет. Окно рисует терминал, и значок в панели задач принадлежит ему, а не
нам. Отсюда и «командная строка» вместо нашего глаза.

Что при этом можно и чего нельзя:

* **Классическое окно консоли** (conhost, запуск двойным щелчком по .exe)
  значок принимает: меняем его через WM_SETICON и недокументированную, но
  живущую в kernel32 десятилетиями `SetConsoleIcon`.
* **Windows Terminal** значок не отдаёт. Он показывает свою иконку и свой
  заголовок вкладки, и способа перебить это изнутри запущенной программы нет.
  Это устройство терминала, а не недоработка приложения.
* **Ярлык** можно указать со своим значком вручную: свойства ярлыка →
  «Сменить значок» → выбрать HunterCLI.exe.

При запуске из исходников значка нет вовсе: он лежит внутри собранного .exe.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_IS_WINDOWS = sys.platform == "win32"

WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1


def set_title(title: str) -> bool:
    if not _IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.kernel32.SetConsoleTitleW(title))
    except Exception:
        return False


def _own_icons() -> tuple[int, int] | None:
    """Достать значок из собственного .exe: большой и малый."""
    if not getattr(sys, "frozen", False):
        return None  # из исходников брать неоткуда
    try:
        large = wintypes.HICON()
        small = wintypes.HICON()
        shell = ctypes.windll.shell32
        shell.ExtractIconExW.argtypes = [
            wintypes.LPCWSTR, ctypes.c_int,
            ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.HICON),
            wintypes.UINT,
        ]
        count = shell.ExtractIconExW(
            sys.executable, 0, ctypes.byref(large), ctypes.byref(small), 1
        )
        if not count or not large.value:
            return None
        return large.value, small.value or large.value
    except Exception:
        return None


def set_console_icon() -> bool:
    """Поставить окну консоли значок программы. True, если получилось.

    Возврат False — обычное дело: в Windows Terminal значок принадлежит
    терминалу, и менять его нам нечем.
    """
    if not _IS_WINDOWS:
        return False

    icons = _own_icons()
    if icons is None:
        return False
    large, small = icons

    changed = False
    try:
        window = ctypes.windll.kernel32.GetConsoleWindow()
        if window:
            ctypes.windll.user32.SendMessageW(window, WM_SETICON, ICON_SMALL, small)
            ctypes.windll.user32.SendMessageW(window, WM_SETICON, ICON_BIG, large)
            changed = True
    except Exception:
        pass

    try:
        # Экспортируется kernel32 без объявления в заголовках, но существует
        # во всех версиях Windows и меняет значок именно у окна консоли.
        ctypes.windll.kernel32.SetConsoleIcon(large)
        changed = True
    except Exception:
        pass

    return changed
