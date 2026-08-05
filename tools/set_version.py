# -*- coding: utf-8 -*-
"""Проставить версию из тега релиза.

Вызывается из GitHub Actions при сборке по тегу: тег `v2026.2` превращается
в `__version__ = "2026.2"` в коде и в свойства собранного .exe. Локально
запускать не нужно — версия в исходниках уже актуальна.

Нумерация как у Umbra: год и порядковый выпуск внутри года (2026.1, 2026.2),
при необходимости с третьим числом для исправлений (2026.2.1).

Запуск: HUNTER_VERSION=v2026.2 python tools/set_version.py
"""

from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from huntercli import force_utf8_output  # noqa: E402  (нужен ROOT в sys.path)

force_utf8_output()

PACKAGE = os.path.join(ROOT, "huntercli", "__init__.py")
RESOURCE = os.path.join(ROOT, "version_info.txt")

VERSION_RE = re.compile(r'^__version__\s*=\s*"[^"]*"', re.M)
TAG_RE = re.compile(r"^[vV]?(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?$")


def _write(path: str, text: str) -> None:
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)


def _patch_package(version: str) -> bool:
    source = io.open(PACKAGE, encoding="utf-8").read()
    updated, count = VERSION_RE.subn(f'__version__ = "{version}"', source, count=1)
    if not count:
        print(f"В {PACKAGE} не найдена строка __version__", file=sys.stderr)
        return False
    _write(PACKAGE, updated)
    return True


def _patch_resource(parts: list[str]) -> bool:
    """Свойства .exe: четыре числа в filevers/prodvers и строковые поля."""
    if not os.path.exists(RESOURCE):
        return True  # ресурс необязателен
    numbers = ", ".join(parts)
    dotted = ".".join(parts[:3])
    text = io.open(RESOURCE, encoding="utf-8").read()
    for field in ("filevers", "prodvers"):
        text = re.sub(rf"{field}=\(\d+,\s*\d+,\s*\d+,\s*\d+\)", f"{field}=({numbers})", text)
    for field in ("FileVersion", "ProductVersion"):
        text = re.sub(rf"StringStruct\('{field}', '[^']*'\)",
                      f"StringStruct('{field}', '{dotted}')", text)
    _write(RESOURCE, text)
    return True


def main() -> int:
    raw = (os.environ.get("HUNTER_VERSION") or "").strip()
    if not raw:
        print("HUNTER_VERSION не задана — версию не трогаем", file=sys.stderr)
        return 0

    match = TAG_RE.match(raw)
    if not match:
        print(f"«{raw}» не похоже на версию — версию не трогаем", file=sys.stderr)
        return 1

    parts = list(match.groups(default="0"))
    # 2026.1.0 -> «2026.1», 2026.2.1 -> «2026.2.1»: хвостовой ноль не пишем.
    version = ".".join(parts[:3]).removesuffix(".0")

    if not _patch_package(version) or not _patch_resource(parts):
        return 1

    print(f"Версия проставлена: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
