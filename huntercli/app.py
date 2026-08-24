"""Сборка приложения: экраны, движки аккаунтов и цикл отрисовки.

Аккаунтов может быть несколько. У каждого свой движок в своём потоке, своя
вкладка на экране и своя подпись в общем журнале; настройки и окно — общие.
"""

from __future__ import annotations

import os
import sys
import time

from rich.console import Console
from rich.live import Live

from . import __version__, auth, config, paths, power, winconsole
from .config import MAX_ACCOUNTS, Account
from .engine import BumpEngine, Phase
from .hh import HHClient, HHError
from .logbus import LogBus, TaggedLog
from .ui import screens
from .ui.dashboard import Dashboard, TabInfo
from .ui.keys import SHIFT_TAB, KeyReader
from .ui.theme import ACCENT, MUTED, THEME, WARN

#: Латиница + кириллица на тех же клавишах — раскладку переключать не нужно.
KEY_QUIT = {"q", "й"}
KEY_SYNC = {"r", "к"}
KEY_BUMP = {"b", "и"}
KEY_PAUSE = {"p", "з"}
KEY_AUTH = {"a", "ф"}
KEY_HELP = {"h", "р", "?"}
KEY_LOG = {"l", "д"}
KEY_ADD = {"n", "т"}
KEY_DROP = {"d", "в"}

FRAME_DELAY = 0.12
MIN_HEIGHT = 14
MIN_WIDTH = 58

#: Сколько ждём подтверждения отключения аккаунта. Одним нажатием такое не
#: делается: аккаунт уходит вместе с сохранённым доступом.
DROP_CONFIRM_SEC = 5.0


class HunterApp:
    def __init__(self) -> None:
        self.console = Console(theme=THEME, highlight=False, soft_wrap=False)
        # Строго до чтения конфига: у версий до 2026.7 он лежал рядом с .exe.
        self._adopted = paths.adopt_legacy_files()
        self.cfg = config.load()
        self.log = LogBus(capacity=self.cfg.settings.log_lines, to_file=paths.writable(paths.log_path()))
        self.engines: list[BumpEngine] = [
            self._make_engine(account, slot)
            for slot, account in enumerate(self.cfg.accounts, start=1)
        ]
        self.dashboard = Dashboard(self.console, self.log)
        self._started_at = time.time()
        self._awake_held = False
        self._drop_asked_until = 0.0

    # ---------------------------------------------------------- аккаунты

    def _make_engine(self, account: Account, slot: int) -> BumpEngine:
        """Движок аккаунта: свой клиент и свой подписанный журнал."""
        journal = TaggedLog(self.log)
        engine = BumpEngine(account, HHClient(account, journal), journal, slot=slot)
        # Подпись ленивая: имя владельца выясняется только при первой
        # синхронизации, а писать в журнал движок начинает раньше.
        journal.tag = lambda: self._tag_for(engine)
        return engine

    def _tag_for(self, engine: BumpEngine) -> str:
        """Чем подписывать записи журнала. Пока аккаунт один — ничем."""
        if len(self.engines) < 2:
            return ""
        name, _ = engine.brief()
        return name or f"аккаунт {engine.slot}"

    @property
    def account(self) -> Account:
        return self.cfg.accounts[self.cfg.active]

    @property
    def engine(self) -> BumpEngine:
        return self.engines[self.cfg.active]

    def _label(self, index: int) -> str:
        """Как называть аккаунт в сообщениях: именем, а до входа — номером."""
        name, _ = self.engines[index].brief()
        return name or self.cfg.accounts[index].name or f"Аккаунт {index + 1}"

    def _tabs(self) -> list[TabInfo]:
        """Полоса вкладок: подпись и фаза каждого аккаунта."""
        return [TabInfo(self._label(index), engine.brief()[1])
                for index, engine in enumerate(self.engines)]

    def _renumber(self) -> None:
        for number, engine in enumerate(self.engines, start=1):
            engine.slot = number

    # ------------------------------------------------------------ запуск

    def run(self) -> int:
        self._prepare_console()
        self.log.info(f"Hunter CLI {__version__} запущен")
        if self._adopted:
            self.log.info(f"Настройки и журнал переехали в {paths.state_dir()}")
        if not self.log.file_enabled:
            self.log.warn("Журнал не пишется на диск: нет прав на запись рядом с программой")
        if self.cfg.corrupted:
            self.log.warn("Прошлый config.json прочитать не удалось — нужен повторный вход")
        if auth.drop_legacy_session():
            self.log.info("Убрана общая сессия окна входа от прежних версий")

        first_run = not self.cfg.authorized
        if first_run:
            screens.welcome(self.console)

        self._hold_awake()
        for engine in self.engines:
            engine.start()

        code = 0
        try:
            while True:
                if not self.account.authorized or self.engine.auth_needed:
                    if not self._authorize_active(first_run):
                        break
                    first_run = False
                    continue

                outcome = self._session()
                if outcome == "quit":
                    break
                if outcome == "reauth-manual":
                    if not self._authorize_active(False, manual=True):
                        break
                elif outcome == "add-account":
                    self._add_account()
                elif outcome == "drop-account":
                    self._drop_account()
        except KeyboardInterrupt:
            self.log.info("Остановка по Ctrl+C")
        finally:
            for engine in self.engines:
                engine.stop()
            for engine in self.engines:
                engine.join()
            self._release_awake()
            self.cfg.save()

        screens.farewell(
            self.console,
            sum(engine.snapshot().session_bumps for engine in self.engines),
            sum(account.stats.total_bumps for account in self.cfg.accounts),
            time.time() - self._started_at,
        )
        return code

    def _prepare_console(self) -> None:
        winconsole.set_title(f"Hunter CLI {__version__}")
        # Значок берёт классическое окно консоли. Windows Terminal показывает
        # свой и менять его не даёт — это его устройство, не наша недоработка.
        winconsole.set_console_icon()

    def _hold_awake(self) -> None:
        """Запрет засыпания — один на программу, а не на каждый аккаунт.

        Windows выставляет это состояние потоку целиком, поэтому снимать его
        при отключении одного аккаунта было бы неверно: остальные работают.
        """
        if not self.cfg.settings.prevent_sleep:
            return
        self._awake_held = power.keep_awake(True)
        if self._awake_held:
            self.log.info("Компьютеру запрещено засыпать по бездействию")
        else:
            self.log.warn("Не удалось запретить засыпание по бездействию")

    def _release_awake(self) -> None:
        if self._awake_held:
            power.keep_awake(False)
            self._awake_held = False

    # -------------------------------------------------------------- вход

    def _authorize_active(self, first_run: bool, *, manual: bool = False) -> bool:
        """Вход в аккаунт открытой вкладки. False — продолжать больше нечем."""
        if manual:
            reason = "Повторный вход по вашей команде."
        elif first_run:
            reason = ""
        else:
            reason = "Доступ потерян — нужно войти заново."
        if self.cfg.corrupted and first_run:
            reason = "Сохранённый доступ не читается. Войдите ещё раз."

        others = any(
            account.authorized
            for index, account in enumerate(self.cfg.accounts)
            if index != self.cfg.active
        )
        if len(self.cfg.accounts) > 1:
            reason = f"Вкладка {self.cfg.active + 1} из {len(self.cfg.accounts)}. {reason}".strip()

        if screens.authorize(
            self.console,
            self.log,
            self.account,
            reason,
            cancel="Вернуться к другим аккаунтам" if others else "Выйти из программы",
        ):
            self.engine.clear_auth_flag()
            self.cfg.save()
            twin = self._twin_of(self.account)
            if twin >= 0:
                # Не запрещаем, но предупреждаем: два автопилота на одних
                # резюме друг другу только мешают.
                self.log.warn(f"Этот аккаунт уже открыт на вкладке {twin + 1}")
            return True
        return self._fall_back_from(self.cfg.active)

    def _fall_back_from(self, index: int) -> bool:
        """Вход не состоялся: уходим на другой рабочий аккаунт, если он есть."""
        for other, account in enumerate(self.cfg.accounts):
            if other != index and account.authorized:
                self.cfg.active = other
                self.dashboard.toast(f"Вкладка {other + 1}: {self._label(other)}")
                return True
        return False

    def _add_account(self) -> None:
        """Подключить ещё один аккаунт — он откроется на своей вкладке."""
        if len(self.cfg.accounts) >= MAX_ACCOUNTS:
            self.dashboard.toast(f"Больше {MAX_ACCOUNTS} аккаунтов подключить нельзя.")
            return

        previous = self.cfg.active
        account = self.cfg.add_account()
        engine = self._make_engine(account, len(self.engines) + 1)
        self.engines.append(engine)
        self.cfg.active = len(self.cfg.accounts) - 1

        ok = screens.authorize(
            self.console,
            self.log,
            account,
            "Вход в дополнительный аккаунт: он откроется на своей вкладке.",
            cancel="Отмена — вернуться к дашборду",
        )
        if ok:
            twin = self._twin_of(account)
            if twin >= 0:
                ok = False
                self.console.print()
                self.console.print(f"[{WARN}]Этот аккаунт уже подключён — вкладка {twin + 1}.[/]")
                self.console.print(f"[{MUTED}]Ничего не меняем, возвращаемся к дашборду.[/]")
                time.sleep(2.5)

        if not ok:
            self.cfg.accounts.pop()
            self.engines.pop()
            auth.forget_session(account.uid)
            self.cfg.active = previous
            self.cfg.adopt()
            return

        engine.clear_auth_flag()
        engine.start()
        self.cfg.save()
        self.log.ok(f"Подключён аккаунт: {self._label(self.cfg.active)}")
        self.dashboard.toast("Аккаунт подключён — он на своей вкладке.")

    def _twin_of(self, account: Account) -> int:
        """Не этот ли аккаунт уже открыт на соседней вкладке. -1 — новый.

        Заодно узнаём имя владельца, чтобы вкладка подписалась сразу, а не
        после первой синхронизации.
        """
        try:
            person_id, name = HHClient(account, self.log).identity()
        except HHError:
            return -1  # не спросили — и ладно, отменять из-за этого вход незачем
        account.name = name or account.name
        account.person_id = person_id or account.person_id
        return self.cfg.find_person(account.person_id, skip=self.cfg.active)

    def _drop_account(self) -> None:
        """Отключить аккаунт открытой вкладки вместе с сохранённым доступом."""
        index = self.cfg.active
        label = self._label(index)
        uid = self.cfg.accounts[index].uid

        engine = self.engines.pop(index)
        engine.stop()
        engine.join()

        self.cfg.drop_account(index)
        auth.forget_session(uid)
        # Аккаунтов не бывает ноль: на месте последнего остаётся пустая
        # вкладка, и программа попросит войти заново.
        while len(self.engines) < len(self.cfg.accounts):
            fresh = self._make_engine(self.cfg.accounts[len(self.engines)], len(self.engines) + 1)
            self.engines.append(fresh)
            fresh.start()
        self._renumber()
        self.cfg.save()
        self.log.info(f"Аккаунт «{label}» отключён")
        self.dashboard.toast(f"Аккаунт «{label}» отключён.")

    # ------------------------------------------------------------- сеанс

    def _session(self) -> str:
        """Один цикл дашборда. Возвращает то, чем заниматься дальше."""
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
                    live.update(self.dashboard.render(snap, self._tabs(), self.cfg.active))

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
            self._drop_asked_until = 0.0
            return None
        if key == "\x03":  # Ctrl+C внутри raw-режима
            raise KeyboardInterrupt

        if key == "\t":
            self._switch_tab(1)
            return None
        if key == SHIFT_TAB:
            self._switch_tab(-1)
            return None

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
        if lowered in KEY_ADD:
            self.dashboard.toast("Открываем окно входа для нового аккаунта...")
            return "add-account"
        if lowered in KEY_DROP:
            return self._ask_drop()
        if lowered in KEY_LOG:
            self._open_log()
            return None
        if key.isdigit() and key != "0":
            verdict = self.engine.toggle_resume(int(key))
            self.dashboard.toast(verdict or "Резюме с таким номером нет.")
            return None
        return None

    def _switch_tab(self, step: int) -> None:
        if len(self.cfg.accounts) < 2:
            self.dashboard.toast("Аккаунт пока один. Ещё один — клавишей N.")
            return
        self.cfg.active = (self.cfg.active + step) % len(self.cfg.accounts)
        self._drop_asked_until = 0.0
        self.dashboard.toast(f"Вкладка {self.cfg.active + 1}: {self._label(self.cfg.active)}")

    def _ask_drop(self) -> str | None:
        """Отключение аккаунта идёт в два нажатия: мимо такой клавиши обидно."""
        if time.time() <= self._drop_asked_until:
            self._drop_asked_until = 0.0
            return "drop-account"
        self._drop_asked_until = time.time() + DROP_CONFIRM_SEC
        label = self._label(self.cfg.active)
        verb = "Отключить аккаунт" if len(self.cfg.accounts) > 1 else "Выйти из аккаунта"
        self.dashboard.toast(f"{verb} «{label}»? Нажмите D ещё раз.", DROP_CONFIRM_SEC)
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
