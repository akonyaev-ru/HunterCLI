# -*- coding: utf-8 -*-
"""Чистые функции: разбор дат, кодов, конфиг, шифрование, планирование."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone

from harness import Report, sandbox

sandbox()

import huntercli.config as config_mod
from huntercli import auth, secrets
from huntercli.config import Config, Settings
from huntercli.engine import BumpEngine, human_nap
from huntercli.power import SleepDetector, WakeTimer, keep_awake
from huntercli.hh import (
    AUTH_ERROR_MARKERS,
    Resume,
    error_kinds,
    explain_errors,
    parse_hh_time,
    token_is_sendable,
)
from huntercli.logbus import LogBus
import huntercli.paths as paths_mod
from huntercli.paths import config_path


def run() -> bool:
    report = Report("Модульные проверки")

    report.section("Разбор дат hh.ru")
    for raw, expect_ok in [
        ("2026-08-05T14:02:11+0300", True),
        ("2026-08-05T14:02:11+03:00", True),
        ("2026-08-05T11:02:11Z", True),
        ("2026-08-05T11:02:11", True),
        (None, False),
        ("мусор", False),
    ]:
        got = parse_hh_time(raw)
        report.check(f"parse_hh_time({raw!r})", (got is not None) == expect_ok, f"-> {got}")
    report.check(
        "смещение без двоеточия разобрано верно",
        parse_hh_time("2026-08-05T14:02:11+0300") == parse_hh_time("2026-08-05T11:02:11Z"),
    )

    report.section("Извлечение кода авторизации")
    for raw, expect in [
        ("hh-android://oauth/code?code=ABC123XYZ", "ABC123XYZ"),
        ("https://hh.ru/x?state=1&code=QWE456&foo=1", "QWE456"),
        ("  ABC123XYZ  ", "ABC123XYZ"),
        ("https://hh.ru/oauth/authorize?client_id=Z", None),
        ("", None),
        (None, None),
    ]:
        report.check(f"extract_code({raw!r})", auth.extract_code(raw) == expect,
                     f"-> {auth.extract_code(raw)!r}")

    report.section("Ссылка на авторизацию")
    url = auth.build_auth_url()
    report.check("redirect_uri не передаётся (hh.ru его отвергает)", "redirect_uri" not in url)
    report.check("client_id на месте", auth.CLIENT_ID in url)

    report.section("Расшифровка ошибок hh.ru")
    report.check(
        "известный код объяснён по-русски",
        "лимит" in explain_errors({"errors": [{"value": "quota_exceeded"}]}),
    )
    report.check("неизвестный код показан как есть",
                 explain_errors({"errors": [{"value": "странное"}]}) == "странное")
    report.check("пустой ответ не ломает", explain_errors(None) == "")

    report.section("Распознавание ошибок токена")
    real_403 = {"description": "Forbidden",
                "errors": [{"value": "bad_authorization", "type": "oauth"}]}
    report.check("настоящий отказ hh.ru опознан как проблема с токеном",
                 bool(error_kinds(real_403) & AUTH_ERROR_MARKERS),
                 f"-> {error_kinds(real_403)}")
    report.check("запрос без заголовка тоже опознан",
                 bool(error_kinds({"errors": [{"type": "forbidden"}]}) & AUTH_ERROR_MARKERS))
    quota = {"errors": [{"type": "bad_argument", "value": "quota_exceeded"}]}
    report.check("исчерпанный лимит НЕ путаем с проблемой токена",
                 not (error_kinds(quota) & AUTH_ERROR_MARKERS), f"-> {error_kinds(quota)}")
    report.check("пустой ответ не даёт ложного срабатывания", error_kinds(None) == set())

    report.section("Пригодность токена для заголовка")
    report.check("кириллица отвергнута", not token_is_sendable("ТОКЕН"))
    report.check("пробелы отвергнуты", not token_is_sendable(" abc "))
    report.check("пустой отвергнут", not token_is_sendable(""))
    report.check("нормальный принят", token_is_sendable("USERABC123"))

    report.section("Шифрование токена (DPAPI на Windows)")
    sealed = secrets.protect("USERTOKEN-СЕКРЕТ-123")
    report.check("в файл уходит не открытый текст", "USERTOKEN" not in sealed)
    report.check("расшифровка возвращает исходник",
                 secrets.unprotect(sealed) == "USERTOKEN-СЕКРЕТ-123")
    report.check("мусор не роняет программу", secrets.unprotect("dpapi:не-база64") == "")
    report.check("токен из старой версии читается", secrets.unprotect("raw-legacy") == "raw-legacy")

    report.section("Разбор формы подтверждения")
    html = (
        '<form action="/oauth/approve" method="POST">'
        '<input type="hidden" name="_xsrf" value="TOK123">'
        '<input type="hidden" name="client_id" value="CID">'
        '<input type="submit" name="approve" value="Продолжить">'
        "</form>"
    )
    found = auth._find_approve_form(html)
    report.check("форма найдена", found is not None)
    if found:
        action, fields = found
        report.check("адрес формы", action == "/oauth/approve", f"-> {action}")
        report.check("_xsrf вытащен", fields.get("_xsrf") == "TOK123")
        report.check("кнопка подтверждения сохранена", fields.get("approve") == "Продолжить")
    report.check("на странице без формы -> None", auth._find_approve_form("<p>пусто</p>") is None)

    report.section("Логин-редирект против обычного oauth-редиректа")
    report.check("страница входа опознана", auth._is_login_redirect("/account/login?backurl=x"))
    report.check("oauth-адрес НЕ считается входом",
                 not auth._is_login_redirect("/oauth/authorize?client_id=CID"))

    report.section("Конфиг: сохранение и чтение")
    cfg = Config()
    account = cfg.account
    account.apply_token({"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 1209599})
    account.name = "Иван"
    account.managed_resumes = ["111"]
    account.stats.record_bump("Тестовое резюме", time.time())
    cfg.save()

    loaded = config_mod.load()
    report.check("токен пережил сохранение", loaded.account.access_token == "AT-1")
    report.check("refresh пережил сохранение", loaded.account.refresh_token == "RT-1")
    report.check("имя аккаунта сохранено", loaded.account.name == "Иван")
    report.check("выбор резюме сохранён", loaded.account.managed_resumes == ["111"])
    report.check("статистика сохранена", loaded.account.stats.total_bumps == 1)
    report.check("свежий токен не просит обновления", not loaded.account.needs_refresh)
    report.check("на диске нет открытого токена",
                 "AT-1" not in open(config_path(), encoding="utf-8").read())

    account.expires_at = time.time() + 3600
    report.check("токен на исходе просит обновления", account.needs_refresh)

    report.section("Конфиг: миграция с версии 1")
    with open(config_path(), "w", encoding="utf-8") as fh:
        json.dump({"token": "Bearer LEGACY123", "resume_id": "999"}, fh)
    migrated = config_mod.load()
    report.check("старый токен подхвачен без слова Bearer",
                 migrated.account.access_token == "LEGACY123")
    report.check("старый resume_id стал списком",
                 migrated.account.managed_resumes == ["999"])

    with open(config_path(), "w", encoding="utf-8") as fh:
        fh.write("{это не json")
    broken = config_mod.load()
    report.check("битый конфиг помечен, а не уронил программу", broken.corrupted)
    os.remove(config_path())

    report.section("Файлы программы: рядом с .exe ничего не остаётся")
    # Проверяем на отдельных папках: песочница тестов подменяет обе точки
    # входа одним путём, и переезд в ней просто нечему было бы показать.
    program = tempfile.mkdtemp(prefix="huntercli-program-")
    settings = tempfile.mkdtemp(prefix="huntercli-settings-")
    keep_base, keep_state = paths_mod.base_dir, paths_mod.state_dir
    try:
        paths_mod.base_dir = lambda: program
        paths_mod.state_dir = lambda: settings

        report.check("конфиг лежит в папке настроек",
                     os.path.dirname(paths_mod.config_path()) == settings)
        report.check("журнал лежит там же",
                     os.path.dirname(paths_mod.log_path()) == settings)

        with open(os.path.join(program, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "auth": {"account": "Старый"}}, fh)
        with open(os.path.join(program, "hunter.log"), "w", encoding="utf-8") as fh:
            fh.write("старая запись\n")

        moved = paths_mod.adopt_legacy_files()
        report.check("оба файла переехали", sorted(moved) == ["config.json", "hunter.log"],
                     f"-> {moved}")
        report.check("рядом с программой пусто", not os.listdir(program),
                     f"-> {os.listdir(program)}")
        report.check("настройки не потерялись при переезде",
                     config_mod.load().account.name == "Старый")

        # Второй переезд поверх уже существующих настроек: свежее не затираем,
        # но старое рядом с программой всё равно убираем.
        with open(os.path.join(program, "config.json"), "w", encoding="utf-8") as fh:
            json.dump({"version": 2, "auth": {"account": "Ещё старее"}}, fh)
        again = paths_mod.adopt_legacy_files()
        report.check("повторный переезд ничего не перенёс", again == [], f"-> {again}")
        report.check("рядом с программой снова пусто", not os.listdir(program))
        report.check("свежие настройки уцелели", config_mod.load().account.name == "Старый")

        os.remove(paths_mod.config_path())
        report.check("без старых файлов переезд молчит", paths_mod.adopt_legacy_files() == [])
    finally:
        paths_mod.base_dir, paths_mod.state_dir = keep_base, keep_state
        shutil.rmtree(program, ignore_errors=True)
        shutil.rmtree(settings, ignore_errors=True)

    report.section("Тихие часы")
    quiet = Settings(quiet_hours=[23, 7])
    report.check("23:00 — тихо", quiet.quiet_now(23))
    report.check("03:00 — тихо", quiet.quiet_now(3))
    report.check("12:00 — не тихо", not quiet.quiet_now(12))
    report.check("без настройки тихих часов нет", not Settings().quiet_now(3))

    report.section("Журнал событий")
    bus = LogBus(capacity=3, to_file=False)
    for index in range(5):
        bus.info(f"строка {index}")
    report.check("буфер держит только последние записи", len(bus.tail(10)) == 3)
    report.check("хвост — самые свежие", bus.tail(1)[0].text == "строка 4")
    after = bus.since(bus.tail(3)[0].seq)
    report.check("since отдаёт только новое без дублей",
                 [e.text for e in after] == ["строка 3", "строка 4"],
                 f"-> {[e.text for e in after]}")
    report.check("since с нуля отдаёт всё, что есть", len(bus.since(0)) == 3)

    report.section("Сон, блокировка, крышка")
    detector = SleepDetector()
    report.check("обычное ожидание сном не считается", detector.check() == 0.0)
    # Подделываем расхождение часов: во сне монотонные стоят, календарные идут.
    detector._wall -= 900
    napped = detector.check()
    report.check("сон опознан по расхождению часов", 880 < napped < 920,
                 f"-> {napped:.0f} с")
    report.check("после проверки счётчик сброшен", detector.check() == 0.0)
    report.check("длительность сна по-человечески", human_nap(900) == "15 мин",
                 f"-> {human_nap(900)!r}")
    report.check("длинный сон в часах", human_nap(7800) == "2 ч 10 мин",
                 f"-> {human_nap(7800)!r}")

    timer = WakeTimer()
    report.check("будильник пробуждения создан", timer.supported)
    report.check("будильник заводится", timer.arm(3600))
    report.check("будильник снимается", timer.cancel())
    timer.close()
    report.check("повторное закрытие не ломает", timer.close() is None)

    report.check("запрет засыпания включается", keep_awake(True))
    report.check("и снимается", keep_awake(False))
    report.check("настройки по умолчанию — беречь работу автопилота",
                 Settings().prevent_sleep and Settings().wake_from_sleep)

    report.section("Планирование поднятий")
    engine = BumpEngine(Config().account, None, LogBus(to_file=False))
    now = datetime.now(timezone.utc)
    engine._resumes = [
        Resume(id="a", title="Можно сейчас", can_publish=True,
               next_publish_at=now - timedelta(minutes=5)),
        Resume(id="b", title="Ждём", can_publish=False,
               next_publish_at=now + timedelta(hours=3, minutes=58)),
        Resume(id="c", title="Заблокировано", blocked=True),
        Resume(id="d", title="Не заполнено", finished=False),
    ]
    engine._replan_locked()
    report.check("разрешённое поднимаем сразу",
                 abs(engine._resumes[0].planned_at - time.time()) < 2)
    gap = engine._resumes[1].planned_at - engine._resumes[1].next_publish_at.timestamp()
    report.check("к ожидающему добавлен человеческий разброс", 45 <= gap <= 240, f"-> {gap:.0f} с")
    report.check("заблокированное не планируем", engine._resumes[2].planned_at is None)
    report.check("незаполненное не планируем", engine._resumes[3].planned_at is None)

    first = engine._resumes[1].planned_at
    engine._replan_locked()
    report.check("разброс не скачет между синхронизациями",
                 engine._resumes[1].planned_at == first)

    engine._resumes[0].retry_after = time.time() + 1800
    engine._replan_locked()
    report.check("после отказа выдерживаем паузу",
                 engine._resumes[0].planned_at - time.time() > 1700)

    engine.account.managed_resumes = ["b"]
    engine._replan_locked()
    report.check("выключенное резюме не планируется", engine._resumes[0].planned_at is None)

    report.section("Зарплаты: приведение к одному виду")
    from huntercli import salary

    rates = {"EUR": 0.00997, "KZT": 5.338544}
    # Курс сервис отдаёт как «сколько валюты в рубле», поэтому пересчёт делением.
    report.check("евро приводится к рублю",
                 224000 < salary.to_rub({"from": 2000, "to": 2500, "currency": "EUR"}, rates) < 227000,
                 f"-> {salary.to_rub({'from': 2000, 'to': 2500, 'currency': 'EUR'}, rates)}")
    report.check("тенге приводится к рублю",
                 186000 < salary.to_rub({"from": 1000000, "currency": "KZT"}, rates) < 189000)
    report.check("вилка сводится к середине",
                 salary.to_rub({"from": 130000, "to": 160000, "currency": "RUR"}, rates) == 145000)
    report.check("одна граница берётся как есть",
                 salary.to_rub({"from": 200000, "currency": "RUR"}, rates) == 200000)
    report.check("верхняя граница тоже годится",
                 salary.to_rub({"to": 90000, "currency": "RUR"}, rates) == 90000)
    # «До вычета» и «на руки» несопоставимы: без пересчёта медиана завышена.
    report.check("до вычета приводится к на руки",
                 salary.to_rub({"from": 100000, "currency": "RUR", "gross": True}, rates) == 87000)
    report.check("зарплаты нет — ничего не выдумываем",
                 salary.to_rub(None, rates) is None)
    report.check("пустая вилка отбрасывается",
                 salary.to_rub({"currency": "RUR"}, rates) is None)
    # Незнакомую валюту пересчитать нечем, а класть чужие деньги в рублёвый
    # ряд — портить медиану.
    report.check("незнакомая валюта отбрасывается",
                 salary.to_rub({"from": 100, "currency": "XXX"}, rates) is None)
    report.check("почасовая ставка отбрасывается",
                 salary.to_rub({"from": 500, "to": 700, "currency": "RUR"}, rates) is None)
    report.check("годовая сумма отбрасывается",
                 salary.to_rub({"from": 9000000, "currency": "RUR"}, rates) is None)

    report.section("Зарплаты: сводка")
    plain = [{"from": v, "currency": "RUR"} for v in
             (100000, 120000, 140000, 160000, 180000, 200000, 5000000)]
    summary = salary.summarize(plain, rates, total=20)
    # Медиана, а не среднее: одна вакансия за пять миллионов не должна тянуть.
    report.check("медиана устойчива к выбросу", summary.median == 160000,
                 f"-> {summary.median}")
    report.check("среднее было бы враньём", summary.median < sum(
        v["from"] for v in plain) / len(plain))
    report.check("квартили посчитаны", summary.low < summary.median < summary.high,
                 f"-> {summary.low}/{summary.median}/{summary.high}")
    # Доля указавших — часть ответа: зарплату публикует меньшинство.
    report.check("доля указавших сохранена", summary.share == "7 из 20",
                 f"-> {summary.share}")
    report.check("значения округлены до тысяч",
                 all(v % 1000 == 0 for v in (summary.median, summary.low, summary.high)))

    nothing = salary.summarize([None, None], rates, total=12)
    report.check("без единой зарплаты сводка пустая", nothing.empty)
    report.check("но число просмотренных сохранено", nothing.total == 12)
    # На двух-трёх числах квартили — выдумка, показываем размах.
    tiny = salary.summarize([{"from": 100000, "currency": "RUR"},
                             {"from": 200000, "currency": "RUR"}], rates, total=5)
    report.check("на крошечной выборке берётся размах",
                 tiny.low == 100000 and tiny.high == 200000, f"-> {tiny}")
    report.check("разряды разделены пробелом", salary.human(185000) == "185 000",
                 f"-> {salary.human(185000)!r}")

    report.section("Зарплаты: курсы из справочника")
    rates2 = salary.rates_from_dictionary(
        {"currency": [{"code": "RUR", "rate": 1}, {"code": "EUR", "rate": 0.00997},
                      {"code": "BAD", "rate": 0}, {"code": None, "rate": 5}, "мусор"]})
    report.check("годные курсы разобраны", rates2 == {"RUR": 1.0, "EUR": 0.00997},
                 f"-> {rates2}")
    report.check("мусор в справочнике не роняет",
                 salary.rates_from_dictionary(None) == {})

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
