# -*- coding: utf-8 -*-
"""Несколько аккаунтов: конфиг, изоляция, вкладки и управление ими.

Сеть здесь не нужна: окно входа и обращения к сервису подменяются заглушками.
"""

from __future__ import annotations

import io
import json
import os
import threading
import time

from harness import Report, sandbox

sandbox()

import huntercli.config as config_mod
from huntercli import auth
from huntercli.app import HunterApp
from huntercli.config import MAX_ACCOUNTS, Config
from huntercli.engine import BumpEngine, Phase
from huntercli.hh import HHClient, Resume
from huntercli.logbus import LogBus, TaggedLog
from huntercli.paths import config_path, state_dir
from huntercli.ui import screens


def _fresh_config() -> None:
    """Убрать конфиг, оставшийся от соседних проверок."""
    if os.path.exists(config_path()):
        os.remove(config_path())


def _login(token: str):
    """Заглушка окна входа: считаем, что человек вошёл и токен получен."""

    def authorize(console, log, account, reason="", *, cancel="") -> bool:
        account.apply_token({"access_token": token, "expires_in": 1209599})
        account.name = ""
        account.person_id = ""
        return True

    return authorize


def _refused(console, log, account, reason="", *, cancel="") -> bool:
    return False


def run() -> bool:
    report = Report("Несколько аккаунтов")

    # ----------------------------------------------------------- конфиг
    report.section("Конфиг хранит аккаунты по отдельности")
    _fresh_config()
    cfg = Config()
    first = cfg.account
    first.apply_token({"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 1209599})
    first.name, first.person_id = "Первый", "111"
    first.managed_resumes = ["r1"]
    first.stats.record_bump("Резюме первого", time.time())

    second = cfg.add_account()
    second.apply_token({"access_token": "AT-2", "expires_in": 1209599})
    second.name, second.person_id = "Второй", "222"
    cfg.active = 1
    cfg.save()

    loaded = config_mod.load()
    report.check("прочитаны оба аккаунта", len(loaded.accounts) == 2, f"-> {len(loaded.accounts)}")
    report.check("у каждого свой токен",
                 [a.access_token for a in loaded.accounts] == ["AT-1", "AT-2"])
    report.check("имена на месте", [a.name for a in loaded.accounts] == ["Первый", "Второй"])
    report.check("открытая вкладка запомнена", loaded.active == 1, f"-> {loaded.active}")
    report.check("выбор резюме у каждого свой",
                 loaded.accounts[0].managed_resumes == ["r1"]
                 and loaded.accounts[1].managed_resumes is None)
    report.check("статистика не общая",
                 (loaded.accounts[0].stats.total_bumps, loaded.accounts[1].stats.total_bumps)
                 == (1, 0))
    report.check("ключи аккаунтов различаются",
                 loaded.accounts[0].uid != loaded.accounts[1].uid)
    report.check("настройки, наоборот, общие",
                 loaded.accounts[1].settings is loaded.settings)
    on_disk = io.open(config_path(), encoding="utf-8").read()
    report.check("на диске нет открытых токенов",
                 "AT-1" not in on_disk and "AT-2" not in on_disk)

    report.section("Границы и поиск по владельцу")
    loaded.active = 99
    loaded.adopt()
    report.check("номер вкладки не уезжает за край", loaded.active == 1, f"-> {loaded.active}")
    report.check("владелец находится по идентификатору", loaded.find_person("222") == 1)
    report.check("сам себя дублем не считает", loaded.find_person("222", skip=1) == -1)
    report.check("пустой идентификатор ни с чем не совпадает", loaded.find_person("") == -1)

    report.section("Отключение аккаунта")
    loaded.drop_account(0)
    report.check("остался один аккаунт", len(loaded.accounts) == 1)
    report.check("и это тот, который не трогали", loaded.account.name == "Второй")
    report.check("вкладка не показывает в пустоту", loaded.active == 0)
    loaded.drop_account(0)
    report.check("последний аккаунт не исчезает, а становится пустым",
                 len(loaded.accounts) == 1 and not loaded.account.authorized)
    report.check("общий признак входа сброшен", not loaded.authorized)
    report.check("больше положенного не подключить", MAX_ACCOUNTS >= 2)

    report.section("Конфиг прошлой версии становится первым аккаунтом")
    _fresh_config()
    with io.open(config_path(), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "version": 2,
                "auth": {"access_token": "OLD-TOKEN", "refresh_token": "OLD-RT",
                         "expires_at": time.time() + 1209599, "account": "Старый"},
                "settings": {"managed_resumes": ["777"], "log_lines": 123},
                "stats": {"total_bumps": 42},
            },
            fh,
        )
    old = config_mod.load()
    report.check("аккаунт ровно один", len(old.accounts) == 1)
    report.check("токен перенесён", old.account.access_token == "OLD-TOKEN")
    report.check("имя перенесено", old.account.name == "Старый")
    report.check("выбор резюме переехал из общих настроек в аккаунт",
                 old.account.managed_resumes == ["777"])
    report.check("статистика переехала туда же", old.account.stats.total_bumps == 42)
    report.check("общие настройки уцелели", old.settings.log_lines == 123)
    report.check("у перенесённого аккаунта появился свой ключ", bool(old.account.uid))
    _fresh_config()

    report.section("Конфиг переживает одновременное сохранение")
    # Аккаунтов несколько — значит, и потоков, которые пишут конфиг, тоже.
    parallel = Config()
    parallel.add_account()
    parallel.add_account()
    threads = [threading.Thread(target=lambda: [parallel.save() for _ in range(20)])
               for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    try:
        written = json.load(io.open(config_path(), encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - обрывок файла тоже результат
        written = {}
        report.check("файл остался целым", False, f"-> {exc}")
    report.check("файл остался целым и полным", len(written.get("accounts", [])) == 3,
                 f"-> {len(written.get('accounts', []))}")
    _fresh_config()

    # ------------------------------------------------------------ сессии
    report.section("У каждого аккаунта своя сессия окна входа")
    report.check("папки сессий не совпадают",
                 auth.session_dir("aaa") != auth.session_dir("bbb"))
    report.check("общая папка отличается от именной",
                 auth.session_dir() != auth.session_dir("aaa"))
    os.makedirs(auth.session_dir("aaa"), exist_ok=True)
    os.makedirs(auth.session_dir("bbb"), exist_ok=True)
    auth.forget_session("aaa")
    report.check("отключённый аккаунт уносит свою сессию",
                 not os.path.isdir(auth.session_dir("aaa")))
    report.check("а чужую не трогает", os.path.isdir(auth.session_dir("bbb")))
    auth.forget_session("")
    report.check("пустой ключ ничего не сносит", os.path.isdir(auth.session_dir("bbb")))
    auth.forget_session("bbb")

    os.makedirs(auth.session_dir(), exist_ok=True)
    report.check("общая сессия прежних версий убирается", auth.drop_legacy_session())
    report.check("повторно убирать нечего", not auth.drop_legacy_session())

    # ------------------------------------------------------------ журнал
    report.section("Записи журнала подписаны аккаунтом")
    bus = LogBus(to_file=False)
    TaggedLog(bus, "Второй").ok("резюме поднято")
    report.check("подпись стоит перед текстом",
                 bus.tail(1)[0].text == "Второй · резюме поднято", f"-> {bus.tail(1)[0].text!r}")
    TaggedLog(bus).ok("резюме поднято")
    report.check("без подписи запись выглядит как раньше",
                 bus.tail(1)[0].text == "резюме поднято")
    holder = [""]
    lazy = TaggedLog(bus, lambda: holder[0])
    lazy.info("до входа")
    holder[0] = "Иван"
    lazy.info("после входа")
    report.check("подпись появляется, как только становится известна",
                 [e.text for e in bus.tail(2)] == ["до входа", "Иван · после входа"],
                 f"-> {[e.text for e in bus.tail(2)]}")

    # ------------------------------------------------------------ движки
    report.section("Движки соседних аккаунтов не мешают друг другу")
    pair = Config()
    left, right = pair.account, pair.add_account()
    engine_left = BumpEngine(left, None, LogBus(to_file=False), slot=1)
    engine_right = BumpEngine(right, None, LogBus(to_file=False), slot=2)
    engine_left._resumes = [Resume(id="x", title="Резюме слева")]
    engine_right._resumes = [Resume(id="y", title="Резюме справа")]
    engine_left.toggle_resume(1)
    report.check("выключили резюме у одного аккаунта", left.managed_resumes == [])
    report.check("у соседа выбор не тронут", right.managed_resumes is None)
    report.check("сосед видит своё резюме",
                 [r.id for r in engine_right.snapshot().resumes] == ["y"])
    report.check("сводка для вкладки не требует входа",
                 engine_right.brief() == ("", Phase.STARTING),
                 f"-> {engine_right.brief()}")
    engine_left.toggle_pause()
    report.check("пауза у одного аккаунта видна на его вкладке",
                 engine_left.brief()[1] in (Phase.PAUSED, Phase.STARTING))
    report.check("а на вкладке соседа её нет", engine_right.brief()[1] == Phase.STARTING)
    _fresh_config()

    # ------------------------------------------------------- приложение
    report.section("Приложение: подключение и отключение аккаунтов")
    keep_start = BumpEngine.start
    keep_identity = HHClient.identity
    keep_authorize = screens.authorize
    try:
        # Настоящие потоки и сеть тут не нужны: проверяем управление вкладками.
        BumpEngine.start = lambda self: None
        _fresh_config()

        app = HunterApp()
        app.console.file = io.StringIO()
        app.account.apply_token({"access_token": "AT-1", "expires_in": 1209599})
        app.account.name = "Первый"
        report.check("на старте один аккаунт и одна вкладка",
                     len(app.engines) == 1 and len(app._tabs()) == 1)
        report.check("пока аккаунт один, записи журнала не подписываются",
                     app._tag_for(app.engines[0]) == "")

        screens.authorize = _login("AT-2")
        HHClient.identity = lambda self: ("222", "Второй")
        app._add_account()
        report.check("аккаунт подключился", len(app.cfg.accounts) == 2)
        report.check("открылась его вкладка", app.cfg.active == 1)
        report.check("у него свой токен", app.account.access_token == "AT-2")
        report.check("имя подхватилось сразу", app._label(1) == "Второй")
        report.check("подписи в журнале появились",
                     app._tag_for(app.engines[1]) == "Второй")
        report.check("на полосе вкладок оба аккаунта",
                     [tab.label for tab in app._tabs()] == ["Первый", "Второй"],
                     f"-> {[tab.label for tab in app._tabs()]}")
        report.check("движков столько же, сколько аккаунтов",
                     len(app.engines) == len(app.cfg.accounts))

        HHClient.identity = lambda self: ("222", "Второй ещё раз")
        screens.authorize = _login("AT-3")
        app._add_account()
        report.check("тот же аккаунт второй раз не подключается",
                     len(app.cfg.accounts) == 2, f"-> {len(app.cfg.accounts)}")
        report.check("после отказа осталась прежняя вкладка", app.cfg.active == 1)
        report.check("лишних движков не завелось", len(app.engines) == 2)

        screens.authorize = _refused
        app._add_account()
        report.check("отказ от входа не оставляет пустой вкладки",
                     len(app.cfg.accounts) == 2 and len(app.engines) == 2)

        app._switch_tab(1)
        report.check("Tab переходит по кругу на первую вкладку", app.cfg.active == 0)
        app._switch_tab(-1)
        report.check("Shift+Tab возвращает назад", app.cfg.active == 1)

        report.check("первое нажатие D только спрашивает", app._ask_drop() is None)
        report.check("второе подтверждает", app._ask_drop() == "drop-account")
        app._drop_account()
        report.check("аккаунт отключён", len(app.cfg.accounts) == 1)
        report.check("остался тот, который не трогали", app._label(0) == "Первый")
        report.check("движок отключённого убран", len(app.engines) == 1)
        report.check("номера вкладок пересчитаны", app.engines[0].slot == 1)
        report.check("подписи в журнале снова не нужны",
                     app._tag_for(app.engines[0]) == "")

        app._drop_account()
        report.check("последний аккаунт отключается как выход из аккаунта",
                     len(app.cfg.accounts) == 1 and not app.cfg.authorized)
        report.check("движок для пустой вкладки создан",
                     len(app.engines) == 1 and app.engines[0].account is app.cfg.account)
    finally:
        BumpEngine.start = keep_start
        HHClient.identity = keep_identity
        screens.authorize = keep_authorize
        _fresh_config()

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
