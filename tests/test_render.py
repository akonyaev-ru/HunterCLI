# -*- coding: utf-8 -*-
"""Отрисовка дашборда на разных размерах окна.

Кроме проверок кладёт рядом render-preview.txt — туда можно заглянуть глазами.
"""

from __future__ import annotations

import io
import os
import re
import time
from datetime import datetime, timedelta, timezone

from harness import Report, sandbox

sandbox()

from rich.console import Console

from huntercli import APP_NAME, history
from huntercli.engine import Phase, Snapshot
from huntercli.hh import Resume
from huntercli.logbus import LogBus
from huntercli.ui.dashboard import (
    DEFAULT_TAB_MARK,
    PHASE_MARK,
    Dashboard,
    TabInfo,
    window_title,
)
from huntercli.ui.theme import MUTED, THEME

#: Ряд намеренно доходит до минимума, который принимает app.py (58x14):
#: именно на маленьких окнах вёрстка и разъезжается.
SIZES = [(140, 44), (120, 40), (100, 32), (92, 30), (84, 26), (70, 22),
         (64, 20), (60, 18), (58, 14)]
PREVIEW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render-preview.txt")


#: Символы BMP, у которых есть эмодзи-начертание (Emoji=Yes). Терминал красит
#: их своим цветом и рисует в две ячейки вместо одной — вёрстка съезжает.
#: Соседние по виду знаки (● U+25CF, ○ U+25CB, ■ U+25A0, ✓ U+2713, ✕ U+2715)
#: к эмодзи не относятся и безопасны.
EMOJI_BMP = {
    "☀", "☁", "☑", "✂", "✈", "✉", "✊", "✋",
    "✨", "✳", "✴", "❄", "❇", "❌", "❎", "❓",
    "❕", "❗", "✅", "✔", "✖", "➡", "▶", "◀",
    "⌛", "⏰", "⏳", "⚠", "⚡", "⚪", "⚫", "⭐",
    "⭕", "️",
}


def _emoji_capable(char: str) -> bool:
    """Отрисуется ли символ как эмодзи (цветным и двойной ширины)."""
    return char in EMOJI_BMP or ord(char) >= 0x1F000


def _demo_log() -> LogBus:
    """Журнал с теми же формулировками, что печатает само приложение."""
    log = LogBus(to_file=False)
    log.info("Hunter CLI 2.0.0 запущен")
    log.step("Синхронизация: резюме — 4, под автопилотом — 3")
    log.ok("«Ведущий юрист по корпоративному праву» поднято в поиске")
    log.warn("сервис ответил 503, повтор через 3 с")
    log.error("«Дизайнер интерфейсов»: исчерпан дневной лимит поднятий. "
              "Следующая попытка через 30 мин")
    return log


def _demo_snapshot() -> Snapshot:
    now = datetime.now(timezone.utc)
    return Snapshot(
        phase=Phase.WAITING,
        detail="ждём разрешённого времени",
        account="Алексей К.",
        started_at=time.time() - 8125,
        session_bumps=3,
        total_bumps=147,
        last_bump_at=time.time() - 640,
        last_sync_at=time.time() - 30,
        next_action_at=time.time() + 12480,
        wait_span=14400,
        token_seconds_left=11.4 * 86400,
        resumes=[
            Resume(id="1", title="Ведущий юрист по корпоративному праву",
                   next_publish_at=now + timedelta(hours=3, minutes=28),
                   total_views=1204, new_views=12, planned_at=time.time() + 12480),
            Resume(id="2", title="Руководитель юридического департамента", can_publish=True,
                   next_publish_at=now - timedelta(seconds=10), total_views=88,
                   planned_at=time.time() - 1),
            Resume(id="3", title="Дизайнер интерфейсов", total_views=17,
                   planned_at=time.time() + 1800,
                   problem="исчерпан дневной лимит поднятий"),
            Resume(id="4", title="Выключенное резюме", can_publish=True),
            Resume(id="5", title="Заблокированное модерацией", blocked=True),
        ],
    )


def _tabs(count: int) -> list[TabInfo]:
    """Вкладки как в жизни: обычное имя, длинное и аккаунт без имени."""
    names = ["Алексей К.", "Иван Петрович Синицын", "", "Мария", "Пётр"]
    phases = [Phase.WAITING, Phase.BUMPING, Phase.AUTH, Phase.OFFLINE, Phase.PAUSED]
    return [TabInfo(names[index], phases[index]) for index in range(count)]


def _render(board_setup, snap, log, width, height, tabs=None, active=0) -> str:
    # legacy_windows=False: иначе Rich отдаёт на столбец меньше, чем просили,
    # и все замеры ширины уезжают на единицу.
    console = Console(theme=THEME, highlight=False, file=io.StringIO(), width=width,
                      height=height, force_terminal=True, color_system="truecolor",
                      record=True, legacy_windows=False)
    board = Dashboard(console, log)
    board.tick = 4
    board_setup(board)
    console.print(board.render(snap, tabs, active))
    return console.export_text()


TOP, BOTTOM = ("╭", "┌"), ("╰", "└")


def _log_lines(text: str) -> list[str]:
    """Строки внутри панели журнала. Она всегда во всю ширину окна."""
    lines, inside = [], False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith(TOP) and "ЖУРНАЛ" in line:
            inside = True
        elif inside and line.startswith(BOTTOM):
            break
        elif inside and line.startswith("│"):
            body = line[1:].rstrip("│ ")
            if body.strip():
                lines.append(body)
    return lines


def _empty_panels(text: str) -> list[str]:
    """Панели без единой строки содержимого: за верхней рамкой сразу нижняя.

    В широкой раскладке панели стоят рядом и делят строку, поэтому смотрим не
    на отдельную панель, а на пару соседних строк-рамок.
    """
    lines = [raw.rstrip() for raw in text.splitlines()]
    return [
        lines[index].strip()
        for index in range(len(lines) - 1)
        if lines[index].startswith(TOP) and lines[index + 1].startswith(BOTTOM)
    ]


def run() -> bool:
    report = Report("Отрисовка интерфейса")
    log, snap = _demo_log(), _demo_snapshot()
    chunks: list[str] = []

    report.section("Основной вид")
    for width, height in SIZES:
        text = _render(lambda b: None, snap, log, width, height)
        lines = [line.rstrip() for line in text.splitlines()]
        too_wide = [line for line in lines if len(line) > width]
        report.check(f"{width}x{height}: ничего не вылезает за край", not too_wide,
                     f"(строк {len(lines)})")
        report.check(f"{width}x{height}: высота не превышена", len(lines) <= height,
                     f"-> {len(lines)}")
        # Именно строкой таблицы, а не упоминанием в журнале: когда панель
        # резюме схлопывалась совсем, проверка по названию всё равно проходила.
        report.check(f"{width}x{height}: в таблице есть первое резюме",
                     re.search(r"│\s+1\s+Ведущий", text) is not None)
        # Рамки считаем по углам: панель без верхней границы Rich рисует молча.
        report.check(f"{width}x{height}: все панели закрыты",
                     text.count("╭") == text.count("╰"),
                     f"-> открыто {text.count('╭')}, закрыто {text.count('╰')}")
        chunks.append(f"\n{'=' * width}\n=== {width}x{height} ===\n{'=' * width}\n{text}")

    report.section("Панели не бывают пустыми")
    # Подписанная коробка без содержимого читается как поломка программы.
    for width, height in SIZES:
        text = _render(lambda b: None, snap, log, width, height)
        empty = _empty_panels(text)
        report.check(f"{width}x{height}: у каждой панели есть содержимое", not empty,
                     f"-> {empty}")

    report.section("Журнал обрезает, а не переносит")
    # Строка без времени в начале — это перенос: он ломает сетку и вытесняет
    # снизу столько же записей, сколько занял.
    for width, height in SIZES:
        text = _render(lambda b: None, snap, log, width, height)
        rows = _log_lines(text)
        stamped = [row for row in rows if re.match(r"\s*\d\d:\d\d:\d\d ", row)]
        report.check(f"{width}x{height}: каждая строка журнала со временем",
                     len(rows) == len(stamped), f"-> {len(stamped)} из {len(rows)}")
        report.check(f"{width}x{height}: время не ужато", all("…" not in r[:10] for r in rows))

    report.section("Обратный отсчёт не выбрасывается")
    # Ради него на экран и смотрят: панель скорее расстанется со счётчиками.
    for width, height in SIZES:
        text = _render(lambda b: None, snap, log, width, height)
        # Ищем именно подпись счётчика или его значение вида 3:27:59. Простое
        # «через » проходило и на журнале («повтор через 3 с»).
        report.check(f"{width}x{height}: обратный отсчёт на месте",
                     "Следующее поднятие" in text
                     or re.search(r"через \d+:\d\d:\d\d", text) is not None)

    report.section("Обрезанный список резюме не молчит")
    crowded = _demo_snapshot()
    crowded.resumes = crowded.resumes + [
        Resume(id=str(number), title=f"Резюме номер {number}",
               total_views=number, planned_at=time.time() + 600 * number)
        for number in range(6, 26)
    ]
    for width, height in SIZES:
        text = _render(lambda b: None, crowded, log, width, height)
        report.check(f"{width}x{height}: о спрятанных резюме сказано",
                     "…и ещё" in text or "показаны" in text)

    report.section("Полоса вкладок аккаунтов")
    # Строка вкладок отбирает строку у тела: вёрстка обязана это учесть на
    # каждом размере окна, а открытая вкладка — остаться видимой.
    for width, height in SIZES:
        for active in (0, 2):
            text = _render(lambda b: None, snap, log, width, height, _tabs(3), active)
            lines = [line.rstrip() for line in text.splitlines()]
            mark = f"{width}x{height}, вкладка {active + 1}"
            report.check(f"{mark}: ничего не вылезает за край",
                         not [ln for ln in lines if len(ln) > width])
            report.check(f"{mark}: высота не превышена", len(lines) <= height, f"-> {len(lines)}")
            open_mark = PHASE_MARK.get(_tabs(3)[active].phase, DEFAULT_TAB_MARK)
            report.check(f"{mark}: открытая вкладка на месте",
                         f"{open_mark} {active + 1} " in text)
            report.check(f"{mark}: все панели закрыты", text.count("╭") == text.count("╰"))
            report.check(f"{mark}: в таблице есть первое резюме",
                         re.search(r"│\s+1\s+Ведущий", text) is not None)

    lonely = _render(lambda b: None, snap, log, 120, 40, _tabs(1), 0)
    report.check("с одним аккаунтом полосы вкладок нет", "● 1 " not in lonely)
    report.check("и про переключение вкладок не сказано", "вкладка" not in lonely)
    # Цвета вкладок задаются намеренно: открытая белая, остальные серые,
    # цветных плашек нет — они спорят с рамками панелей.
    plain = Console(theme=THEME, highlight=False, file=io.StringIO(), width=100,
                    force_terminal=True, color_system="truecolor", legacy_windows=False)
    strip = Dashboard(plain, log)._tabs_line(_tabs(3), 1, 100)
    styles = [str(span.style) for span in strip.spans]
    report.check("у вкладок нет цветной плашки",
                 not [style for style in styles if " on " in style], f"-> {styles}")
    report.check("открытая вкладка белая", "bold white" in styles, f"-> {styles}")
    report.check("остальные серые", MUTED in styles, f"-> {styles}")

    # Цвета BUMPING и AUTH почти совпадают, поэтому «нужен человек» обязано
    # отличаться знаком: иначе слетевший вход у чужой вкладки ничем не выдаёт
    # себя — заголовок окна о нём молчит намеренно.
    marked = _render(lambda b: None, snap, log, 120, 40, _tabs(3), 0)
    report.check("слетевший вход помечен своим знаком", "! 3 " in marked,
                 "-> нет «! 3 » в полосе вкладок")
    report.check("работающие вкладки помечены обычным знаком",
                 "● 1 " in marked and "● 2 " in marked)
    report.check("знак слетевшего входа одноячеечный и не эмодзи",
                 all(mark not in EMOJI_BMP and len(mark) == 1
                     for mark in PHASE_MARK.values()),
                 f"-> {PHASE_MARK}")

    crowd = _render(lambda b: None, snap, log, 120, 40, _tabs(5), 4)
    report.check("подсказка о вкладках появляется вместе с ними", "вкладка" in crowd)
    report.check("вкладки, которые не влезли, посчитаны", "+1" in crowd or "● 1 " in crowd)

    report.section("Логотип показывается там, где помещается")
    roomy = _render(lambda b: None, snap, log, 140, 29)
    report.check("широкое невысокое окно: логотип на месте", "██╗" in roomy)
    # А там, где логотип съел бы содержимое, он уступает место таблице.
    cramped = _render(lambda b: None, snap, log, 140, 20)
    report.check("низкое окно: место отдано таблице",
                 "██╗" not in cramped
                 and re.search(r"│\s+1\s+Ведущий", cramped) is not None)

    report.section("Аккаунт и причина отказа видны")
    full = _render(lambda b: None, snap, log, 120, 40)
    report.check("имя аккаунта показано", "Алексей К." in full)
    report.check("причина отказа с отступом под названием",
                 "└ исчерпан дневной лимит" in full)

    report.section("Особые состояния")
    help_text = _render(lambda b: setattr(b, "show_help", True), snap, log, 120, 40)
    report.check("справка открывается", "Горячие клавиши" in help_text)
    report.check("в справке есть уведомление о лицензии", "AGPL" in help_text)
    chunks.append(f"\n=== СПРАВКА ===\n{help_text}")

    report.section("Справка помещается в окно")
    # Разделов больше, чем строк в маленьком окне: обязательный (клавиши)
    # обязан остаться, остальное отбрасывается, но обрезки быть не должно.
    for width, height in SIZES:
        text = _render(lambda b: setattr(b, "show_help", True), snap, log, width, height)
        lines = [line.rstrip() for line in text.splitlines()]
        report.check(f"{width}x{height}: справка не вылезает", len(lines) <= height
                     and not [ln for ln in lines if len(ln) > width])
        report.check(f"{width}x{height}: клавиши на месте", "Горячие клавиши" in text)
        # Рамка панели должна замкнуться — если раздел не влез, его выбрасывают
        # целиком, а не режут по середине. Угол зависит от набора символов:
        # скруглённый в обычном терминале, прямой в старой консоли Windows.
        report.check(f"{width}x{height}: панель справки замкнута",
                     "╰" in text or "└" in text)

    empty = _render(lambda b: None, Snapshot(phase=Phase.STARTING, started_at=time.time()),
                    LogBus(to_file=False), 120, 40)
    report.check("пустое состояние не падает", "ещё не загружено" in empty)
    chunks.append(f"\n=== ПУСТОЕ СОСТОЯНИЕ ===\n{empty}")

    offline = _demo_snapshot()
    offline.phase = Phase.OFFLINE
    offline.detail = "нет связи с сервисом"
    offline.offline_since = time.time() - 240
    offline_text = _render(lambda b: None, offline, log, 120, 40)
    report.check("состояние «нет сети» видно", "НЕТ СЕТИ" in offline_text)
    chunks.append(f"\n=== НЕТ СЕТИ ===\n{offline_text}")

    toast = _render(lambda b: b.toast("Поднимаем всё, что сейчас разрешено..."), snap, log, 120, 40)
    report.check("всплывающая подсказка показывается", "Поднимаем всё" in toast)

    report.section("Заголовок окна")
    # Окно бывает свёрнуто весь день, и в панели задач видно один заголовок.
    def titled(**changes) -> str:
        state = _demo_snapshot()
        for name, value in changes.items():
            setattr(state, name, value)
        return window_title(state)

    waiting = window_title(snap)
    report.check("в ожидании виден отсчёт", "через" in waiting, f"-> {waiting!r}")
    report.check("название программы на месте", waiting.endswith(APP_NAME), f"-> {waiting!r}")
    # Панель задач обрезает справа, поэтому состояние обязано идти первым.
    report.check("состояние раньше названия",
                 waiting.index("через") < waiting.index(APP_NAME), f"-> {waiting!r}")
    report.check("с одним аккаунтом имени нет", "Алексей" not in waiting, f"-> {waiting!r}")

    # Приглашение: окно свёрнуто, и заголовок — единственное, что видно.
    one = titled(invitations_pending=1)
    many = titled(invitations_pending=3)
    report.check("одно приглашение видно в заголовке", "Приглашение!" in one, f"-> {one!r}")
    report.check("несколько приглашений посчитаны", "Приглашений: 3" in many, f"-> {many!r}")
    report.check("приглашение вытесняет обратный отсчёт", "через" not in one, f"-> {one!r}")
    # find, а не index: на коде без правки index бросает ValueError и обрывает
    # весь прогон, а проверка обязана честно провалиться и пустить остальные.
    report.check("приглашение раньше названия программы",
                 0 <= one.find("Приглашение") < one.find(APP_NAME), f"-> {one!r}")
    # Со слетевшим входом чинить надо вход: без него о следующих приглашениях
    # программа не узнает вовсе.
    broken = titled(invitations_pending=1, phase=Phase.AUTH)
    report.check("«нужен вход» старше приглашения", "нужен вход" in broken, f"-> {broken!r}")
    report.check("без приглашений заголовок прежний",
                 window_title(_demo_snapshot()) == waiting)

    named = window_title(snap, "Алексей К.")
    report.check("с несколькими аккаунтами имя впереди",
                 named.startswith("Алексей К.") and "через" in named, f"-> {named!r}")

    report.check("пауза видна", "пауза" in titled(paused=True))
    report.check("обрыв связи виден", "нет сети" in titled(phase=Phase.OFFLINE))
    report.check("требование входа видно", "нужен вход" in titled(phase=Phase.AUTH))
    report.check("поднятие видно", "поднимаем" in titled(phase=Phase.BUMPING))
    # Пауза важнее отсчёта: иначе свёрнутое окно врёт, что автопилот работает.
    report.check("пауза перебивает отсчёт", "через" not in titled(paused=True))
    # Ждём, но время следующего действия ещё не известно.
    report.check("без срока — просто состояние",
                 titled(next_action_at=None) == f"мониторинг — {APP_NAME}",
                 f"-> {titled(next_action_at=None)!r}")
    report.check("незнакомая фаза не роняет", APP_NAME in titled(phase="выдумка"))

    later = titled(next_action_at=time.time() + 60)
    report.check("отсчёт идёт", later != waiting, f"-> {later!r}")

    # SetConsoleTitleW принимает одну строку, разметка Rich туда не годится.
    every = [waiting, named, later, titled(paused=True), titled(phase=Phase.AUTH)]
    breaks = (chr(10), chr(13))
    report.check("заголовок однострочный",
                 not any(ch in t for t in every for ch in breaks))
    report.check("разметки Rich в заголовке нет", not any("[" in t for t in every))
    report.check("названия площадки в заголовке нет",
                 not any("hh" in t.lower() for t in every))

    report.section("Экран статистики")

    def _report(count: int = 3, **changes) -> history.Report:
        """Сводка нужной формы, без обращения к диску."""
        base = dict(days=7, views=85, views_before=42, bumps=18, covered=5,
                    total_views=1309, since="2026-08-20",
                    talks=355, invitations=39, responses=213, discards=103,
                    invitations_gained=2)
        base.update(changes)
        titles = ["Ведущий юрист по корпоративному праву", "Дизайнер интерфейсов",
                  "Руководитель юридического департамента", "Курьер", "Оператор"]
        base["resumes"] = [history.ResumeStat(titles[i % len(titles)], 80 - i * 20,
                                             1204 - i * 100, 38 if i == 0 else 0)
                           for i in range(count)]
        return history.Report(**base)

    def _stats(board_report, width=120, height=40) -> str:
        def setup(board):
            board.show_stats = True
            board.stats = board_report
        return _render(setup, snap, log, width, height)

    shown = _stats(_report())
    report.check("экран открывается", "СТАТИСТИКА" in shown)
    report.check("прирост со знаком", "+85" in shown, "-> нет «+85»")
    report.check("динамика к прошлой неделе", "+43" in shown, "-> нет «+43»")
    report.check("поднятия показаны", "Поднятий" in shown)
    report.check("отдача поднятия посчитана", "4,7" in shown, "-> нет «4,7»")
    report.check("о пропусках сказано", "5 из 7 дней" in shown)
    report.check("разбивка по резюме есть", "Ведущий юрист" in shown)
    report.check("общий счётчик внизу", "1309" in shown)
    report.check("дата начала наблюдений", "20.08" in shown)
    chunks.append(f"\n=== СТАТИСТИКА ===\n{shown}")

    # Динамика в ноль — «столько же» понятнее, чем «+0».
    flat = _stats(_report(views=42, views_before=42))
    report.check("нулевая динамика названа словами", "столько же" in flat)
    # Поднятий за окно не было — делить не на что.
    idle = _stats(_report(bumps=0))
    report.check("без поднятий отдача не выдумывается", "На одно поднятие" in idle)
    # Данные за все дни окна — строку о пропусках не показываем.
    full = _stats(_report(covered=7))
    report.check("без пропусков лишней строки нет", "из 7 дней" not in full)

    nothing = _stats(history.Report())
    report.check("пустая история объясняет себя", "появится завтра" in nothing)
    report.check("пустой экран всё равно с рамкой", "СТАТИСТИКА" in nothing)
    chunks.append(f"\n=== СТАТИСТИКА БЕЗ ДАННЫХ ===\n{nothing}")

    report.check("приглашения за неделю видны", "+2" in shown, "-> нет «+2»")
    report.check("итоги за всё время отделены заголовком", "За всё время" in shown)
    report.check("отклики названы словом", "из 355 откликов" in shown,
                 "-> нет «из 355 откликов»")
    report.check("приглашения за всё время", "39 из 355" in shown, "-> нет «39 из 355»")
    # Заголовок без единой строки под ним не выводится никогда.
    report.check("секция резюме не бывает пустой",
                 "По резюме" not in shown or "+80" in shown)
    # «355» без подписи читалось как недельное число: блок сверху именно про
    # неделю. Заголовок обязан быть.
    week_at = shown.index("За 7 дней")
    report.check("недельный блок идёт первым", week_at < shown.index("За всё время"))
    # Итоги важнее длинного списка резюме: место под них резервируется раньше,
    # иначе разбивка съедала всё и главные числа не показывались нигде.
    crowded = _stats(_report(count=12))
    report.check("с длинной разбивкой итоги всё равно на месте", "За всё время" in crowded)
    report.check("а сама разбивка при этом обрезана", "…и ещё" in crowded)
    report.check("приглашения привязаны к резюме", "пригл. 38" in shown, "-> нет «пригл. 38»")
    # Пока обращения ни разу не считали, этих строк быть не должно: ноль
    # приглашений и «ещё не считали» — разные вещи.
    silent = _stats(_report(talks=0, invitations=0, invitations_gained=0))
    report.check("до первого подсчёта о приглашениях молчим",
                 "Приглашений" not in silent)
    report.check("а просмотры показываются как раньше", "+85" in silent)

    report.section("Статистика помещается в окно")
    # Резюме заведомо больше, чем строк: разбивка обязана обрезаться, а не
    # выдавливать панель за край. Правило то же, что у таблицы резюме.
    for width, height in SIZES:
        text = _stats(_report(count=12), width, height)
        lines = [line.rstrip() for line in text.splitlines()]
        report.check(f"{width}x{height}: статистика не вылезает", len(lines) <= height
                     and not [ln for ln in lines if len(ln) > width])
        report.check(f"{width}x{height}: панель замкнута", "╰" in text or "└" in text)
        report.check(f"{width}x{height}: главное число на месте", "+85" in text)
        report.check(f"{width}x{height}: приглашения не потерялись", "+2" in text)
        report.check(f"{width}x{height}: пустых панелей нет", not _empty_panels(text))

    # Об обрезанной разбивке экран сообщает, а не молчит. Размер берём тот,
    # где разбивка вообще помещается: в низком окне её нет целиком, и это не
    # обрезка, а отсутствие.
    cramped = _stats(_report(count=12), 120, 40)
    report.check("обрезанная разбивка сообщает об этом", "…и ещё" in cramped,
                 "-> нет строки «…и ещё N»")

    report.section("В интерфейсе нет символов с эмодзи-начертанием")
    everywhere = (_render(lambda b: None, snap, log, 120, 40)
                  + help_text + offline_text + shown + nothing)
    found = sorted({ch for ch in everywhere if _emoji_capable(ch)})
    report.check("эмодзи-символов на экране нет", not found,
                 f"-> {[hex(ord(c)) for c in found]}")
    report.check("проверка вообще работает", _emoji_capable("✔") and not _emoji_capable("●"))

    report.check("названия площадки на экране нет", "hh.ru" not in everywhere.lower(),
                 "" if "hh.ru" not in everywhere.lower() else "-> найдено в отрисовке")

    stale = _render(lambda b: b.toast("исчезну", seconds=-1), snap, log, 120, 40)
    report.check("подсказка исчезает по времени", "исчезну" not in stale)

    with open(PREVIEW, "w", encoding="utf-8") as fh:
        fh.write("".join(chunks))
    print(f"\n  Внешний вид сохранён: {PREVIEW}")

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
