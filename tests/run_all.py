# -*- coding: utf-8 -*-
"""Прогнать все проверки: python tests\\run_all.py [--live]

По умолчанию сеть не нужна. С ключом --live дополнительно проверяется
поведение против настоящего hh.ru (логин при этом не требуется).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harness  # noqa: E402  (ставит sys.path и песочницу)

MODULES = [
    "test_units",
    "test_theme",
    "test_render",
    "test_no_branding",
    "test_auth",
    "test_webview_flow",
    "test_engine",
    "test_accounts",
]


def main() -> int:
    modules = list(MODULES)
    if "--live" in sys.argv:
        modules.append("test_live")

    results: list[tuple[str, bool]] = []
    for name in modules:
        module = __import__(name)
        try:
            results.append((name, bool(module.run())))
        except Exception as exc:  # noqa: BLE001 - падение теста тоже результат
            import traceback

            traceback.print_exc()
            results.append((name, False))
            print(f"  {name} упал: {exc}")

    harness.cleanup()

    print(f"\n{'=' * 68}\n  ИТОГ\n{'=' * 68}")
    for name, ok in results:
        print(f"  [{'OK  ' if ok else 'FAIL'}] {name}")

    failed = [name for name, ok in results if not ok]
    if failed:
        print(f"\n  Провалено: {', '.join(failed)}")
        return 1
    print("\n  Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
