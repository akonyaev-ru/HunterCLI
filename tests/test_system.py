# -*- coding: utf-8 -*-
"""Автозапуск вместе с Windows и единственный экземпляр программы.

Задание планировщика здесь не создаётся: проверяется XML задания и то, какие
команды уходят в schtasks. Настоящий круг «создать — найти — удалить» идёт
только с переменной среды HUNTER_SYSTEM_TESTS=1 — обычный прогон в системе
владельца ничего не оставляет.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

from harness import ROOT, Report, sandbox

sandbox()

from rich.console import Console  # noqa: E402

import huntercli.app as app_mod  # noqa: E402
from huntercli import autostart, instance  # noqa: E402
from huntercli.app import HunterApp  # noqa: E402
from huntercli.engine import BumpEngine  # noqa: E402
from huntercli.paths import config_path  # noqa: E402
from huntercli.ui import screens  # noqa: E402

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
EXE = "G:\\Мой диск\\Агенты\\x & y\\HunterCLI.exe"
USER = "MSI\\konya"


class FakeScheduler:
    """Подмена schtasks: помнит команды и отвечает, как настоящий."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.tasks: dict[str, str] = {}
        self.fail_create = False

    def __call__(self, args: list[str]) -> tuple[int, str]:
        self.calls.append(list(args))
        verb = args[0]
        name = args[args.index("/TN") + 1]
        if verb == "/Create":
            if self.fail_create:
                return 1, "ОШИБКА: отказано в доступе."
            with open(args[args.index("/XML") + 1], encoding="utf-16") as fh:
                self.tasks[name] = fh.read()
            return 0, "SUCCESS"
        if verb == "/Query":
            if name not in self.tasks:
                return 1, "ERROR: The system cannot find the file specified."
            return 0, self.tasks[name]
        if verb == "/Delete":
            if self.tasks.pop(name, None) is None:
                return 1, "ERROR: The system cannot find the file specified."
            return 0, "SUCCESS"
        return 1, "unknown"

    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


def _xml_field(root: ET.Element, path: str) -> str:
    node = root.find(path, NS)
    return (node.text or "").strip() if node is not None else ""


def run() -> bool:
    report = Report("Автозапуск и один экземпляр")

    # ------------------------------------------------------- задание
    report.section("Задание планировщика: при входе, с задержкой, без админа")
    xml = autostart.build_xml(EXE, USER)
    root = ET.fromstring(xml.encode("utf-16"))
    report.check("запуск при входе этого пользователя",
                 _xml_field(root, "t:Triggers/t:LogonTrigger/t:UserId") == USER)
    report.check("задержка минута — диск с программой успевает подключиться",
                 _xml_field(root, "t:Triggers/t:LogonTrigger/t:Delay") == "PT1M")
    report.check("обычные права, без администратора",
                 _xml_field(root, "t:Principals/t:Principal/t:RunLevel") == "LeastPrivilege"
                 and _xml_field(root, "t:Principals/t:Principal/t:LogonType") == "InteractiveToken")
    report.check("путь с пробелами, кириллицей и «&» не ломает XML",
                 _xml_field(root, "t:Actions/t:Exec/t:Command") == EXE)
    report.check("рабочая папка — папка программы",
                 _xml_field(root, "t:Actions/t:Exec/t:WorkingDirectory") == os.path.dirname(EXE))
    report.check("второй экземпляр планировщик не запускает",
                 _xml_field(root, "t:Settings/t:MultipleInstancesPolicy") == "IgnoreNew")
    report.check("без предела времени работы",
                 _xml_field(root, "t:Settings/t:ExecutionTimeLimit") == "PT0S")
    report.check("на батарее тоже работает",
                 _xml_field(root, "t:Settings/t:DisallowStartIfOnBatteries") == "false"
                 and _xml_field(root, "t:Settings/t:StopIfGoingOnBatteries") == "false")
    report.check("путь читается обратно из ответа планировщика",
                 autostart.command_in(xml) == EXE)
    report.check("мусор вместо XML — путь неизвестен", autostart.command_in("не xml") is None)
    # Так schtasks отвечает на самом деле (проверено 2026-10-05): XML задания
    # в трубу идёт в кодовой странице консоли, у владельца — cp866. Страницу
    # подставляем как у владельца: на раннере GitHub она 437, и проверка,
    # завязанная на машину, там краснела на правильном коде.
    names = autostart._console_encodings()
    report.check("кодовая страница консоли берётся у Windows, сразу после UTF-8",
                 names[0] == "utf-8" and all(n.startswith("cp") for n in names[1:]),
                 f"-> {names}")
    keep_encodings = autostart._console_encodings
    autostart._console_encodings = lambda: ["utf-8", "cp866", "cp1251"]
    try:
        report.check("ответ планировщика в cp866 читается вместе с кириллицей",
                     autostart.command_in(autostart._decode(xml.encode("cp866"))) == EXE)
        report.check("сообщение в кодировке консоли читается",
                     autostart._decode("ОШИБКА: отказано".encode("cp866")) == "ОШИБКА: отказано")
    finally:
        autostart._console_encodings = keep_encodings
    report.check("ответ планировщика в UTF-16 читается",
                 autostart.command_in(autostart._decode(xml.encode("utf-16"))) == EXE)
    report.check("и без метки порядка байтов тоже",
                 autostart.command_in(autostart._decode(xml.encode("utf-16-le"))) == EXE)

    report.section("Включение, выключение, сверка пути")
    fake = FakeScheduler()
    keep_runner = autostart._schtasks
    autostart._schtasks = fake
    try:
        report.check("задания нет — состояние «выключен»", autostart.registered() is None)
        ok, _ = autostart.enable(EXE)
        report.check("включение создаёт задание", ok and autostart.TASK_NAME in fake.tasks)
        report.check("задание создаётся с перезаписью (/F)", "/F" in fake.calls[-1])
        report.check("после включения путь читается", autostart.registered() == EXE)

        report.check("тот же файл — задание не трогаем",
                     autostart.sync(EXE) == "ok" and fake.verbs()[-1] == "/Query")
        moved = "C:\\Programs\\HunterCLI.exe"
        report.check("программу перенесли — задание идёт за ней",
                     autostart.sync(moved) == "moved" and autostart.registered() == moved)
        report.check("регистр букв в пути — не перенос",
                     autostart.sync(moved.upper()) == "ok")

        ok, _ = autostart.disable()
        report.check("выключение удаляет задание", ok and not fake.tasks)
        report.check("удалённое руками задание не восстанавливаем",
                     autostart.sync(EXE) == "missing" and not fake.tasks)

        fake.fail_create = True
        ok, error = autostart.enable(EXE)
        report.check("отказ планировщика — ответ, а не исключение", not ok and error, f"-> {error!r}")
        fake.fail_create = False
    finally:
        autostart._schtasks = keep_runner

    report.section("Из исходников автозапуск не предлагается")
    report.check("в режиме исходников недоступен", not autostart.supported()
                 if not getattr(sys, "frozen", False) else True)

    # ------------------------------------------------- один экземпляр
    report.section("Второй экземпляр")
    name = f"Local\\HunterCLI-test-{os.getpid()}"
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from huntercli import instance; "
        "print('FREE' if instance.acquire(sys.argv[2]) else 'BUSY')"
    )

    def other_process() -> str:
        done = subprocess.run([sys.executable, "-c", probe, ROOT, name],
                              capture_output=True, text=True, timeout=60)
        return (done.stdout or done.stderr).strip()

    report.check("первый экземпляр занимает место", instance.acquire(name))
    if sys.platform == "win32":
        report.check("другой процесс видит, что место занято", other_process() == "BUSY",
                     f"-> {other_process()!r}")
        instance.release()
        report.check("после выхода первого место свободно", other_process() == "FREE")
    else:
        instance.release()

    report.section("Точка входа при уже запущенной программе")
    import main as entry

    keep = instance.acquire, app_mod.run, getattr(entry, "ALREADY_RUNNING_PAUSE_SEC", None)
    keep_argv = sys.argv
    started: list[str] = []
    handoffs: list[str] = []
    from huntercli import auth

    keep_handoff = auth.write_handoff
    try:
        instance.acquire = lambda *args, **kwargs: False
        app_mod.run = lambda: started.append("run") or 0
        entry.ALREADY_RUNNING_PAUSE_SEC = 0
        auth.write_handoff = handoffs.append

        def launch(*argv: str) -> tuple[int, str]:
            sys.argv = ["main.py", *argv]
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = entry.main()
            return code, out.getvalue()

        code, text = launch()
        report.check("второй автопилот не запускается", started == [], f"-> {started}")
        report.check("человеку сказано, что программа уже открыта", "уже запущен" in text,
                     f"-> {text.strip()[:80]!r}")
        code, text = launch("--version")
        report.check("--version работает при запущенной программе",
                     code == 0 and "Hunter CLI" in text)
        code, text = launch("--license")
        report.check("--license тоже", code == 0 and "Affero" in text)
        launch("hh-android://oauth/code?code=SECOND")
        report.check("код из браузера по-прежнему передаётся окну",
                     handoffs == ["hh-android://oauth/code?code=SECOND"], f"-> {handoffs}")
        code, text = launch("--autostart", "status")
        report.check("--autostart отвечает и при запущенной программе", bool(text.strip()))
        if not getattr(sys, "frozen", False):
            report.check("из исходников --autostart объясняет, что он для .exe",
                         code != 0 and ".exe" in text, f"-> {text.strip()[:80]!r}")

        # Как это выглядит для собранной программы — с подменой планировщика.
        fake = FakeScheduler()
        keep_supported, keep_target = autostart.supported, autostart.target
        autostart._schtasks, autostart.supported = fake, (lambda: True)
        autostart.target = lambda: EXE
        try:
            code, text = launch("--autostart", "on")
            report.check("--autostart on включает", code == 0 and EXE == autostart.registered(),
                         f"-> {text.strip()[:80]!r}")
            code, text = launch("--autostart", "status")
            report.check("--autostart status показывает путь", code == 0 and EXE in text)
            code, text = launch("--autostart", "off")
            report.check("--autostart off выключает", code == 0 and autostart.registered() is None)
            code, text = launch("--autostart", "чепуха")
            report.check("непонятный режим — подсказка, а не падение",
                         code != 0 and "on" in text and "off" in text)
            report.check("и всё это без запуска автопилота", started == [])
        finally:
            autostart._schtasks, autostart.supported = keep_runner, keep_supported
            autostart.target = keep_target
    finally:
        instance.acquire, app_mod.run = keep[0], keep[1]
        if keep[2] is not None:
            entry.ALREADY_RUNNING_PAUSE_SEC = keep[2]
        auth.write_handoff = keep_handoff
        sys.argv = keep_argv

    # ------------------------------------------------ вопрос при запуске
    report.section("Вопрос об автозапуске при запуске программы")
    keep_start = BumpEngine.start
    keep_offer = screens.offer_autostart if hasattr(screens, "offer_autostart") else None
    keep_supported, keep_target = autostart.supported, autostart.target
    asked: list[int] = []
    answer = {"value": True}

    def offer(console) -> bool | None:
        asked.append(1)
        return answer["value"]

    def fresh_app(fake_scheduler: FakeScheduler) -> HunterApp:
        if os.path.exists(config_path()):
            os.remove(config_path())
        app = HunterApp()
        app.console = Console(file=io.StringIO(), force_terminal=True, width=100)
        autostart._schtasks = fake_scheduler
        return app

    try:
        BumpEngine.start = lambda self: None
        screens.offer_autostart = offer
        autostart.supported = lambda: True
        autostart.target = lambda: EXE

        fake = FakeScheduler()
        app = fresh_app(fake)
        app._offer_autostart()
        report.check("в первый раз спросили", len(asked) == 1)
        report.check("согласие — задание создано", autostart.registered() == EXE)
        report.check("ответ запомнен", app.cfg.settings.autostart_asked is True)
        report.check("в журнале сказано, что включено",
                     any("Автозапуск включён" in e.text for e in app.log.tail(20)))
        app._offer_autostart()
        report.check("второй раз не спрашиваем", len(asked) == 1)

        asked.clear()
        answer["value"] = False
        fake = FakeScheduler()
        app = fresh_app(fake)
        app._offer_autostart()
        report.check("отказ — задания нет", autostart.registered() is None and len(asked) == 1)
        report.check("отказ тоже запомнен", app.cfg.settings.autostart_asked is True)
        report.check("в журнале — как включить позже",
                     any("--autostart on" in e.text for e in app.log.tail(20)))
        # Ради этого случая флаг и хранится: задания нет, а спрашивать снова нельзя.
        app._offer_autostart()
        report.check("после отказа второй раз не спрашиваем", len(asked) == 1, f"-> {len(asked)}")
        again = HunterApp()  # следующий запуск: ответ берётся из config.json
        again.console = Console(file=io.StringIO(), force_terminal=True, width=100)
        again._offer_autostart()
        report.check("и после перезапуска программы — тоже", len(asked) == 1, f"-> {len(asked)}")

        asked.clear()
        answer["value"] = None
        fake = FakeScheduler()
        app = fresh_app(fake)
        app._offer_autostart()
        report.check("ввод недоступен — спросим в другой раз",
                     app.cfg.settings.autostart_asked is False and autostart.registered() is None)

        asked.clear()
        answer["value"] = True
        fake = FakeScheduler()
        app = fresh_app(fake)
        app.console = Console(file=io.StringIO(), force_terminal=False)
        app._offer_autostart()
        report.check("без интерактивного окна не спрашиваем", not asked)

        asked.clear()
        fake = FakeScheduler()
        autostart._schtasks = fake
        autostart.enable("C:\\old\\HunterCLI.exe")
        app = fresh_app(fake)
        app._offer_autostart()
        report.check("задание уже есть — не спрашиваем", not asked)
        report.check("а путь в нём переведён на запущенный файл", autostart.registered() == EXE)
        report.check("и об этом есть строка в журнале",
                     any("Автозапуск" in e.text for e in app.log.tail(20)))

        asked.clear()
        fake = FakeScheduler()
        fake.fail_create = True
        app = fresh_app(fake)
        try:
            app._offer_autostart()
            crashed = ""
        except Exception as exc:  # noqa: BLE001 - падать как раз нельзя
            crashed = repr(exc)
        report.check("отказ планировщика программу не роняет", not crashed, f"-> {crashed}")
        report.check("и записан предупреждением",
                     any(e.level == "warn" and "Автозапуск" in e.text for e in app.log.tail(20)))

        asked.clear()
        autostart.supported = lambda: False
        app = fresh_app(FakeScheduler())
        app._offer_autostart()
        report.check("из исходников не спрашиваем", not asked)
    finally:
        BumpEngine.start = keep_start
        if keep_offer is not None:
            screens.offer_autostart = keep_offer
        autostart._schtasks = keep_runner
        autostart.supported, autostart.target = keep_supported, keep_target
        if os.path.exists(config_path()):
            os.remove(config_path())

    report.section("Экран вопроса помещается в узкое окно")
    keep_ask = screens._ask
    try:
        screens._ask = lambda console, prompt, **kwargs: "1"
        for width in (58, 80, 120):
            sink = io.StringIO()
            console = Console(file=sink, width=width, height=40, legacy_windows=False,
                              force_terminal=False, color_system=None)
            agreed = screens.offer_autostart(console)
            lines = sink.getvalue().splitlines()
            widest = max((len(line) for line in lines), default=0)
            report.check(f"{width} столбцов: строки не шире окна", widest <= width,
                         f"-> {widest}")
            report.check(f"{width} столбцов: вопрос виден и ответ «да» понят",
                         agreed is True and "Windows" in sink.getvalue())
            report.check(f"{width} столбцов: название площадки не светится",
                         "hh.ru" not in sink.getvalue())
    finally:
        screens._ask = keep_ask

    # --------------------------------------- настоящий планировщик (по желанию)
    if os.environ.get("HUNTER_SYSTEM_TESTS") == "1" and sys.platform == "win32":
        report.section("Настоящий планировщик: создать, найти, удалить")
        task = "HunterCLI-test"
        # Файл существовать не обязан: планировщик путь при создании не проверяет.
        # Кириллица и пробел — как в настоящем пути владельца на Google Drive.
        target = "C:\\Мой диск\\Агенты\\x & y\\HunterCLI.exe"
        ok, error = autostart.enable(target, task=task)
        report.check("задание создано без прав администратора", ok, f"-> {error}")
        report.check("путь читается обратно", autostart.registered(task=task) == target,
                     f"-> {autostart.registered(task=task)!r}")
        ok, error = autostart.disable(task=task)
        report.check("задание удалено", ok and autostart.registered(task=task) is None,
                     f"-> {error}")

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
