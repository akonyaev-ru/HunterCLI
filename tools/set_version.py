# -*- coding: utf-8 -*-
"""Проставить версию из тега релиза в huntercli/__init__.py.

Вызывается из GitHub Actions при сборке по тегу: тег `v2.1.0` превращается в
`__version__ = "2.1.0"`. Локально запускать не нужно — версия в исходниках уже
актуальна.

Запуск: HUNTER_VERSION=v2.1.0 python tools/set_version.py
"""

from __future__ import annotations

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "huntercli", "__init__.py")

VERSION_RE = re.compile(r'^__version__\s*=\s*"[^"]*"', re.M)
SEMVER_RE = re.compile(r"^\d+\.\d+(\.\d+)?([-+][0-9A-Za-z.-]+)?$")


def main() -> int:
    raw = (os.environ.get("HUNTER_VERSION") or "").strip()
    if not raw:
        print("HUNTER_VERSION не задана — версию не трогаем", file=sys.stderr)
        return 0

    version = raw[1:] if raw.startswith("v") else raw
    if not SEMVER_RE.match(version):
        print(f"«{raw}» не похоже на версию — версию не трогаем", file=sys.stderr)
        return 1

    source = io.open(TARGET, encoding="utf-8").read()
    updated, count = VERSION_RE.subn(f'__version__ = "{version}"', source, count=1)
    if not count:
        print(f"В {TARGET} не найдена строка __version__", file=sys.stderr)
        return 1

    io.open(TARGET, "w", encoding="utf-8", newline="\n").write(updated)
    print(f"Версия проставлена: {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
