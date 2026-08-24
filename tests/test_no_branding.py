# -*- coding: utf-8 -*-
"""В надписях интерфейса не должно быть названия площадки.

Окно бывает открыто весь рабочий день, и на него смотрит не только владелец.
Адрес остаётся там, где он нужен по делу: в самих URL, в ключе реестра для
обработчика протокола и в комментариях с документацией — этого пользователь
на экране не видит.
"""

from __future__ import annotations

import ast
import io
import os

from harness import Report, ROOT, sandbox

sandbox()

PACKAGE = os.path.join(ROOT, "huntercli")
FORBIDDEN = ("hh.ru", "HH.RU", "Hh.Ru")

#: Строки, где адрес нужен по делу, а на экран не попадает (или попадает
#: как ссылка, по которой пользователь обязан перейти).
ALLOWED_MARKERS = (
    "http://",
    "https://",
    r"Software\Classes",
    "URL:",
)


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """id() строковых узлов, которые являются docstring — их не проверяем."""
    marked: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                marked.add(id(first.value))
    return marked


def _ui_strings(path: str) -> list[tuple[int, str]]:
    """Строки-константы модуля без docstring и без служебных адресов."""
    source = io.open(path, encoding="utf-8").read()
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        text = node.value
        if any(marker in text for marker in ALLOWED_MARKERS):
            continue
        found.append((node.lineno, text))
    return found


def run() -> bool:
    report = Report("Название площадки не светится в интерфейсе")

    report.section("Строки в коде приложения")
    offenders: list[str] = []
    checked = 0
    for folder, dirs, files in os.walk(PACKAGE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            checked += 1
            for line, text in _ui_strings(path):
                if any(bad in text for bad in FORBIDDEN):
                    relative = os.path.relpath(path, ROOT)
                    offenders.append(f"{relative}:{line}: {text[:60]}")

    report.check("модули проверены", checked >= 10, f"-> {checked}")
    report.check("в надписях интерфейса адреса нет", not offenders,
                 "" if not offenders else "-> " + "; ".join(offenders[:4]))

    report.section("Постоянно видимые элементы")
    from huntercli.engine import PHASE_LABEL
    from huntercli.hh import _ERROR_HINTS
    from huntercli.ui.banner import subtitle
    from huntercli.ui.dashboard import HELP_SECTIONS, HOTKEYS, TAB_HOTKEYS

    line = subtitle(120, with_name=True).plain
    report.check("подпись под логотипом чистая", "hh" not in line.lower(), f"-> {line!r}")
    report.check("названия состояний чистые",
                 not any("hh" in v.lower() for v in PHASE_LABEL.values()))
    report.check("подсказки по клавишам чистые",
                 not any("hh" in label.lower() for _, label in HOTKEYS + TAB_HOTKEYS))
    help_lines = [text for _, rows, _ in HELP_SECTIONS for _, text in rows]
    help_lines += [title for title, _, _ in HELP_SECTIONS]
    report.check("справка чистая", not any("hh.ru" in text for text in help_lines))
    report.check("объяснения ошибок чистые",
                 not any("hh.ru" in text for text in _ERROR_HINTS.values()))

    report.section("Адрес остался там, где он нужен по делу")
    from huntercli import auth, hh

    report.check("ссылка авторизации рабочая", "hh.ru" in auth.build_auth_url())
    report.check("адрес API на месте", "hh.ru" in hh.API_ROOT)

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
