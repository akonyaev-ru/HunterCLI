# -*- coding: utf-8 -*-
"""Микро-каркас для тестов: без внешних зависимостей, читаемый вывод.

Конфиг и журнал во время тестов пишутся во временную папку, чтобы не затирать
рабочий config.json рядом с программой.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

#: Корень проекта — рядом с ним лежат huntercli/ и main.py.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from huntercli import force_utf8_output  # noqa: E402  (нужен ROOT в sys.path)

# Отчёты тестов на русском, а вывод в CI и в файл идёт не в UTF-8.
force_utf8_output()

_SANDBOX: str | None = None


def sandbox() -> str:
    """Временная папка, в которую смотрит huntercli.paths во время тестов."""
    global _SANDBOX
    if _SANDBOX is None:
        _SANDBOX = tempfile.mkdtemp(prefix="huntercli-tests-")
        import huntercli.paths as paths

        paths.base_dir = lambda: _SANDBOX
    return _SANDBOX


def cleanup() -> None:
    if _SANDBOX and os.path.isdir(_SANDBOX):
        shutil.rmtree(_SANDBOX, ignore_errors=True)


class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.failures: list[str] = []
        self.total = 0
        print(f"\n{'=' * 68}\n  {title}\n{'=' * 68}")

    def section(self, name: str) -> None:
        print(f"\n-- {name}")

    def check(self, label: str, condition: object, extra: str = "") -> bool:
        self.total += 1
        ok = bool(condition)
        if not ok:
            self.failures.append(label)
        print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{(' ' + extra) if extra else ''}")
        return ok

    def raises(self, label: str, exc_type: type, func, *args, **kwargs) -> bool:
        try:
            func(*args, **kwargs)
        except exc_type as exc:
            return self.check(label, True, f"-> {str(exc)[:110]}")
        except Exception as exc:  # noqa: BLE001 - показать, что прилетело вместо
            return self.check(label, False, f"-> {type(exc).__name__}: {exc}")
        return self.check(label, False, "(исключения не было)")

    @property
    def passed(self) -> bool:
        return not self.failures

    def summary(self) -> bool:
        if self.passed:
            print(f"\n  {self.title}: пройдено {self.total} проверок")
        else:
            print(f"\n  {self.title}: провалено {len(self.failures)} из {self.total}")
            for item in self.failures:
                print(f"    - {item}")
        return self.passed
