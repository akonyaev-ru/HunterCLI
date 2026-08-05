"""Hunter CLI — точка входа.

Обычный запуск открывает дашборд. Особые режимы:
  HunterCLI.exe "hh-android://oauth/code?code=..."  — приём кода от браузера
  HunterCLI.exe --check                             — проверка окружения
  HunterCLI.exe --version                           — версия
  HunterCLI.exe --license                           — лицензия и гарантии
"""

from __future__ import annotations

import sys


def _handle_protocol(url: str) -> int:
    """Нас запустила Windows по ссылке hh-android:// — передать код основному окну."""
    from huntercli import auth

    try:
        auth.write_handoff(url)
        print("Код авторизации передан в Hunter CLI. Это окно можно закрыть.")
    except Exception as exc:
        print(f"Не удалось передать код: {exc}")
        print(f"Скопируйте ссылку и вставьте её в программу вручную:\n{url}")
        try:
            input("Enter — закрыть...")
        except Exception:
            pass
    return 0


def main() -> int:
    from huntercli import force_utf8_output

    force_utf8_output()
    argument = sys.argv[1] if len(sys.argv) > 1 else ""

    if argument.startswith("hh-android://"):
        return _handle_protocol(argument)

    if argument in ("--version", "-v"):
        from huntercli import __version__

        print(f"Hunter CLI {__version__}")
        return 0

    if argument == "--license":
        from huntercli import APP_NAME, LICENSE_NOTICE, __version__

        print(f"{APP_NAME} {__version__}")
        print()
        print(LICENSE_NOTICE)
        return 0

    if argument in ("--check", "--selftest"):
        from huntercli import diagnostics

        return diagnostics.run()

    if argument in ("--help", "-h", "/?"):
        print(__doc__)
        return 0

    from huntercli.app import run

    return run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:  # последний рубеж: не закрывать окно молча
        import traceback

        traceback.print_exc()
        print()
        print(f"Критическая ошибка: {error}")
        try:
            input("Нажмите Enter, чтобы закрыть окно...")
        except Exception:
            pass
        raise SystemExit(1)
