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

from huntercli.engine import Phase, Snapshot
from huntercli.hh import Resume
from huntercli.logbus import LogBus
from huntercli.ui.dashboard import Dashboard
from huntercli.ui.theme import THEME

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


def _render(board_setup, snap, log, width, height) -> str:
    # legacy_windows=False: иначе Rich отдаёт на столбец меньше, чем просили,
    # и все замеры ширины уезжают на единицу.
    console = Console(theme=THEME, highlight=False, file=io.StringIO(), width=width,
                      height=height, force_terminal=True, color_system="truecolor",
                      record=True, legacy_windows=False)
    board = Dashboard(console, log)
    board.tick = 4
    board_setup(board)
    console.print(board.render(snap))
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

    report.section("В интерфейсе нет символов с эмодзи-начертанием")
    everywhere = _render(lambda b: None, snap, log, 120, 40) + help_text + offline_text
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
