"""Hunter CLI — точка входа.

Обычный запуск открывает дашборд. Особые режимы:
  HunterCLI.exe "hh-android://oauth/code?code=..."  — приём кода от браузера
  HunterCLI.exe --check                             — проверка окружения
  HunterCLI.exe --version                           — версия
  HunterCLI.exe --license                           — лицензия и гарантии
  HunterCLI.exe --autostart on | off | status       — запуск вместе с Windows
"""

from __future__ import annotations

import sys
import time

#: Сколько держать на экране сообщение второго экземпляра. Его могли открыть
#: двойным щелчком — окно не должно исчезнуть раньше, чем его прочтут.
ALREADY_RUNNING_PAUSE_SEC = 6

_AUTOSTART_USAGE = "Использование: HunterCLI.exe --autostart on | off | status"


def _handle_autostart(mode: str) -> int:
    """Включить, выключить или показать автозапуск — без запуска автопилота."""
    from huntercli import autostart

    if not autostart.supported():
        print("Автозапуск есть только у собранной программы (HunterCLI.exe).")
        return 1
    if mode == "on":
        ok, error = autostart.enable()
        if not ok:
            print(f"Включить автозапуск не вышло: {error}")
            return 1
        print("Автозапуск включён: Hunter CLI откроется через минуту после входа в Windows.")
        print(f"Файл: {autostart.target()}")
        return 0
    if mode == "off":
        ok, error = autostart.disable()
        if not ok:
            print(f"Выключить автозапуск не вышло: {error}")
            return 1
        print("Автозапуск выключен.")
        return 0
    if mode == "status":
        current = autostart.registered()
        if current is None:
            print("Автозапуск выключен.")
        else:
            print(f"Автозапуск включён: {current}")
        return 0
    print(_AUTOSTART_USAGE)
    return 2


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

    if argument == "--autostart":
        return _handle_autostart(sys.argv[2] if len(sys.argv) > 2 else "status")

    # Служебные режимы выше работают и при запущенной программе. Второй
    # автопилот — нет: два движка на одних аккаунтах только мешают друг другу.
    from huntercli import instance

    if not instance.acquire():
        print("Hunter CLI уже запущен — его окно открыто, возможно, свёрнуто.")
        print("Второй экземпляр не нужен: два автопилота мешали бы друг другу.")
        time.sleep(ALREADY_RUNNING_PAUSE_SEC)
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
