"""Чтобы автопилот не простаивал, пока компьютер отдыхает.

Что происходит на самом деле:

* **Блокировка экрана** программе не мешает вообще. Процесс продолжает
  работать, ничего делать не нужно.
* **Сон** останавливает процесс целиком: пока система спит, программа не
  выполняет ни одной инструкции. Никакой код изнутри этого не обойдёт.
* **Закрытие крышки** по умолчанию отправляет ноутбук в сон. Что именно
  делать по крышке, решает Windows в настройках питания, и приложение эту
  настройку не перебивает.

Отсюда два приёма, которые здесь и реализованы:

1. `keep_awake` — просим Windows не засыпать по бездействию, пока программа
   работает. Экран при этом гаснуть может: держим систему, а не дисплей.
   Против явного сна (крышка, «Пуск → Сон») это не работает по замыслу
   Windows, и притворяться иначе не стоит.
2. `WakeTimer` — будильник, который поднимает систему из сна к нужному
   времени. Работает, если в настройках питания разрешены таймеры пробуждения
   (по умолчанию разрешены, но администратор или режим экономии могут их
   запретить).

Плюс `slept_through` — определение того, что система всё-таки поспала:
монотонные часы во сне стоят, а календарные идут, и по расхождению видно,
что пора немедленно свериться с сервером.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

_IS_WINDOWS = sys.platform == "win32"

# Флаги SetThreadExecutionState.
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040


def keep_awake(enable: bool) -> bool:
    """Запретить или снова разрешить засыпание по бездействию.

    Возвращает True, если Windows приняла запрос. Дисплею гаснуть не мешаем:
    для фоновой программы это было бы наглостью.
    """
    if not _IS_WINDOWS:
        return False
    flags = ES_CONTINUOUS
    if enable:
        # AWAYMODE позволяет системе работать с погашенным экраном; если
        # режим недоступен, повторяем запрос без него.
        flags |= ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
    try:
        result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        if not result and enable:
            result = ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED
            )
        return bool(result)
    except Exception:
        return False


class WakeTimer:
    """Будильник, поднимающий систему из сна к назначенному времени."""

    def __init__(self) -> None:
        self._handle = None
        self.supported = _IS_WINDOWS
        if not _IS_WINDOWS:
            return
        try:
            kernel = ctypes.windll.kernel32
            kernel.CreateWaitableTimerW.restype = wintypes.HANDLE
            kernel.CreateWaitableTimerW.argtypes = [
                wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR
            ]
            # manual reset = True: таймер нужен как будильник, не как событие.
            self._handle = kernel.CreateWaitableTimerW(None, True, None)
            self.supported = bool(self._handle)
        except Exception:
            self.supported = False

    def arm(self, seconds_from_now: float) -> bool:
        """Завести будильник. Отрицательное или нулевое время — снять."""
        if not self._handle:
            return False
        if seconds_from_now <= 0:
            return self.cancel()
        try:
            kernel = ctypes.windll.kernel32
            kernel.SetWaitableTimer.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong), wintypes.LONG,
                wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL,
            ]
            # Отрицательное значение = интервал от текущего момента,
            # шаг 100 наносекунд.
            due = ctypes.c_longlong(-int(seconds_from_now * 10_000_000))
            return bool(kernel.SetWaitableTimer(
                self._handle, ctypes.byref(due), 0, None, None, True  # fResume
            ))
        except Exception:
            return False

    def cancel(self) -> bool:
        if not self._handle:
            return False
        try:
            return bool(ctypes.windll.kernel32.CancelWaitableTimer(self._handle))
        except Exception:
            return False

    def close(self) -> None:
        if self._handle:
            try:
                ctypes.windll.kernel32.CancelWaitableTimer(self._handle)
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


class SleepDetector:
    """Замечает, что система поспала, пока программа ждала.

    Монотонные часы во время сна стоят, календарные идут. Разница между ними
    и есть длительность сна.
    """

    #: Меньшие расхождения — обычные задержки планировщика, не сон.
    THRESHOLD_SEC = 20.0

    def __init__(self) -> None:
        self._monotonic = time.monotonic()
        self._wall = time.time()

    def check(self) -> float:
        """Сколько секунд система проспала с прошлой проверки (0 — не спала)."""
        monotonic, wall = time.monotonic(), time.time()
        drift = (wall - self._wall) - (monotonic - self._monotonic)
        self._monotonic, self._wall = monotonic, wall
        return drift if drift > self.THRESHOLD_SEC else 0.0
