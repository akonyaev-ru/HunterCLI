"""Сборка приложения: экраны, движок и цикл отрисовки."""

from __future__ import annotations

import os
import sys
import time

from rich.console import Console
from rich.live import Live

from . import __version__, config, paths, winconsole
from .engine import BumpEngine, Phase
from .hh import HHClient
from .logbus import LogBus
from .ui import screens
from .ui.dashboard import Dashboard
from .ui.keys import KeyReader
from .ui.theme import ACCENT, MUTED, THEME, WARN

#: Латиница + кириллица на тех же клавишах — раскладку переключать не нужно.
KEY_QUIT = {"q", "й"}
KEY_SYNC = {"r", "к"}
KEY_BUMP = {"b", "и"}
KEY_PAUSE = {"p", "з"}
KEY_AUTH = {"a", "ф"}
KEY_HELP = {"h", "р", "?"}
KEY_LOG = {"l", "д"}

FRAME_DELAY = 0.12
MIN_HEIGHT = 14
MIN_WIDTH = 58


class HunterApp:
    def __init__(self) -> None:
        self.console = Console(theme=THEME, highlight=False, soft_wrap=False)
        self.cfg = config.load()
        self.log = LogBus(capacity=self.cfg.settings.log_lines, to_file=paths.writable(paths.log_path()))
        self.client = HHClient(self.cfg, self.log)
        self.engine = BumpEngine(self.cfg, self.client, self.log)
        self.dashboard = Dashboard(self.console, self.log)
        self._started_at = time.time()

    # ------------------------------------------------------------ запуск

    def run(self) -> int:
        self._prepare_console()
        self.log.info(f"Hunter CLI {__version__} запущен")
        if not self.log.file_enabled:
            self.log.warn("Журнал не пишется на диск: нет прав на запись рядом с программой")
        if self.cfg.corrupted:
            self.log.warn("Прошлый config.json прочитать не удалось — нужен повторный вход")

        first_run = not self.cfg.authorized
        if first_run:
            screens.welcome(self.console)

        self.engine.start()
        code = 0
        try:
            while True:
                if not self.cfg.authorized or self.engine.auth_needed:
                    reason = "" if first_run else "Доступ потерян — нужно войти заново."
                    if self.cfg.corrupted and first_run:
                        reason = "Сохранённый доступ не читается. Войдите ещё раз."
                    if not screens.authorize(self.console, self.log, self.cfg, reason):
                        break
                    self.engine.clear_auth_flag()
                    first_run = False
                    continue

                outcome = self._session()
                if outcome == "quit":
                    break
                if outcome == "reauth-manual":
                    if not screens.authorize(
                        self.console, self.log, self.cfg, "Повторный вход по вашей команде."
                    ):
                        break
                    self.engine.clear_auth_flag()
        except KeyboardInterrupt:
            self.log.info("Остановка по Ctrl+C")
        finally:
            self.engine.stop()
            self.engine.join()
            self.cfg.save()

        snap = self.engine.snapshot()
        screens.farewell(
            self.console, snap.session_bumps, snap.total_bumps, time.time() - self._started_at
        )
        return code

    def _prepare_console(self) -> None:
        winconsole.set_title(f"Hunter CLI {__version__}")
        # Значок берёт классическое окно консоли. Windows Terminal показывает
        # свой и менять его не даёт — это его устройство, не наша недоработка.
        winconsole.set_console_icon()

    # ------------------------------------------------------------- сеанс

    def _session(self) -> str:
        """Один цикл дашборда. Возвращает 'quit' или 'reauth'."""
        if not self.console.is_terminal:
            return self._headless()

        with KeyReader() as keys, Live(
            console=self.console,
            screen=True,
            refresh_per_second=8,
            transient=False,
        ) as live:
            while True:
                if self.engine.auth_needed:
                    return "reauth"

                snap = self.engine.snapshot()
                width, height = self.console.size
                if height < MIN_HEIGHT or width < MIN_WIDTH:
                    live.update(
                        f"[{WARN}]Окно слишком маленькое.[/]\n"
                        f"[{MUTED}]Нужно хотя бы {MIN_WIDTH}×{MIN_HEIGHT} символов, "
                        f"сейчас {width}×{height}.[/]"
                    )
                else:
                    live.update(self.dashboard.render(snap))

                action = self._handle_keys(keys)
                if action:
                    return action

                self.dashboard.tick += 1
                time.sleep(FRAME_DELAY)

    def _handle_keys(self, keys: KeyReader) -> str | None:
        key = keys.poll()
        if key is None:
            return None
        lowered = key.lower()

        if key == "\x1b":  # Esc
            if self.dashboard.show_help:
                self.dashboard.show_help = False
            return None
        if key == "\x03":  # Ctrl+C внутри raw-режима
            raise KeyboardInterrupt

        if lowered in KEY_QUIT:
            return "quit"
        if lowered in KEY_HELP:
            self.dashboard.show_help = not self.dashboard.show_help
            return None
        if lowered in KEY_SYNC:
            self.engine.request_sync()
            self.dashboard.toast("Обновляем список резюме...")
            return None
        if lowered in KEY_BUMP:
            self.engine.request_bump()
            self.dashboard.toast("Поднимаем всё, что сейчас разрешено...")
            return None
        if lowered in KEY_PAUSE:
            paused = self.engine.toggle_pause()
            self.dashboard.toast("Пауза. Поднятия остановлены." if paused else "Автопилот снова в работе.")
            return None
        if lowered in KEY_AUTH:
            self.dashboard.toast("Открываем окно входа...")
            return "reauth-manual"
        if lowered in KEY_LOG:
            self._open_log()
            return None
        if key.isdigit() and key != "0":
            verdict = self.engine.toggle_resume(int(key))
            self.dashboard.toast(verdict or "Резюме с таким номером нет.")
            return None
        return None

    def _open_log(self) -> None:
        path = paths.log_path()
        if not os.path.exists(path):
            self.dashboard.toast("Файл журнала ещё не создан.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 - открываем свой же файл
            self.dashboard.toast(f"Журнал: {path}")
        except Exception:
            self.dashboard.toast(f"Журнал лежит здесь: {path}")

    # ------------------------------------------ режим без интерактивности

    def _headless(self) -> str:
        """Вывод без дашборда: перенаправленный вывод, планировщик и т. п."""
        self.console.print(
            f"[{ACCENT}]Hunter CLI {__version__} — фоновый режим (терминал не интерактивный)[/]"
        )
        seen = 0
        while not self.engine.auth_needed:
            for entry in self.log.since(seen):
                self.console.print(f"[{MUTED}]{entry.clock}[/] {entry.text}")
                seen = entry.seq
            if self.engine.snapshot().phase == Phase.STOPPED:
                return "quit"
            time.sleep(2.0)
        return "reauth"


def run() -> int:
    app = HunterApp()
    try:
        return app.run()
    except KeyboardInterrupt:
        return 0
