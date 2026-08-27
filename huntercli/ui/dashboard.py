"""Полноэкранный дашборд автопилота."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime

from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .. import APP_NAME, history
from ..engine import PHASE_LABEL, Phase, Snapshot
from ..logbus import ERROR, LogBus, OK, STEP, WARN
from . import banner
from .theme import (
    ACCENT,
    ACCENT_SOFT,
    COOL,
    ERR,
    FLAME,
    FRAME,
    FRAME_HOT,
    MUTED,
    OK as OK_COLOR,
    WARN as WARN_COLOR,
    gradient_bar,
    human_span,
    spinner,
)

#: С этой ширины сводка встаёт сбоку от таблицы, а не над ней.
WIDE_LAYOUT_WIDTH = 92
#: Заголовок без логотипа: подпись и линейка под ней.
COMPACT_HEADER_ROWS = 2
#: Полная сводка в узкой раскладке: рамка, фаза, счётчики, отсчёт и полоса.
COMPACT_STATUS_ROWS = 6
#: Рамка панели плюс шапка таблицы с линейкой — столько уходит не на резюме.
TABLE_CHROME_ROWS = 4
#: Панели резюме нужно хотя бы это, чтобы показать одну строку, а не пустоту.
MIN_TABLE_PANEL = 5
#: Ширина колонки времени: «12:17:33» плюс пробел, чтобы значок уровня не
#: прилипал к цифрам.
LOG_CLOCK_WIDTH = 9
#: Что в строке журнала занято не текстом: рамка панели с отступами (4),
#: время, значок уровня (1) и два пробела между колонками.
LOG_CHROME_WIDTH = LOG_CLOCK_WIDTH + 7
#: Разделитель между фактами в однострочной сводке.
FACT_SEPARATOR = "  ·  "
#: Пока сводке достаётся хотя бы столько строк, она сохраняет отбивку под
#: заголовком; дальше отбивка уходит первой.
MIN_SUMMARY_ROWS = 3
#: Ниже этих значений тело не заслуживает логотипа над собой.
MIN_WIDE_BODY = 8
MIN_NARROW_BODY = COMPACT_STATUS_ROWS + MIN_TABLE_PANEL
#: Длиннее имя на вкладке не показываем: место нужно и соседним аккаунтам.
TAB_NAME_LIMIT = 18

PHASE_STYLE = {
    Phase.STARTING: ACCENT_SOFT,
    Phase.SYNCING: COOL,
    Phase.WAITING: OK_COLOR,
    Phase.BUMPING: ACCENT,
    Phase.OFFLINE: WARN_COLOR,
    Phase.AUTH: ERR,
    Phase.PAUSED: MUTED,
    Phase.STOPPED: MUTED,
}

# Значки журнала. Только «текстовые» символы: U+2714 ✔ и U+2716 ✖ входят в
# эмодзи-набор — терминал красит их своим цветом и занимает две ячейки вместо
# одной, из-за чего строка съезжает. У U+2713 ✓ и U+2715 ✕ такой беды нет.
LEVEL_MARK = {
    OK: ("✓", OK_COLOR),
    ERROR: ("✕", ERR),
    WARN: ("!", WARN_COLOR),
    STEP: ("›", COOL),
}

HOTKEYS = [
    ("Q", "выход"),
    ("R", "обновить"),
    ("B", "поднять сейчас"),
    ("P", "пауза"),
    ("A", "перезайти"),
    ("N", "ещё аккаунт"),
    ("1-9", "вкл/выкл резюме"),
    ("H", "справка"),
    ("S", "статистика"),
]

#: Подсказки, которые нужны только когда аккаунтов несколько.
TAB_HOTKEYS = [
    ("Tab", "вкладка"),
    ("D", "отключить"),
]


def hotkeys(multi: bool) -> list[tuple[str, str]]:
    """Подсказки внизу экрана. Про вкладки говорим, только когда они есть."""
    if not multi:
        return list(HOTKEYS)
    after = [key for key, _ in HOTKEYS].index("N") + 1
    return HOTKEYS[:after] + TAB_HOTKEYS + HOTKEYS[after:]


@dataclass(frozen=True)
class TabInfo:
    """Аккаунт в полосе вкладок: как подписан и чем сейчас занят."""

    label: str = ""
    phase: str = Phase.STARTING


#: Разделы справки: (заголовок, строки, порядок вытеснения).
#: Порядок в списке = порядок на экране. Число — очерёдность, в которой раздел
#: пытаются добавить, если в окне мало места: 0 показываем всегда, дальше по
#: возрастанию. Уведомление о лицензии идёт раньше пояснений: этого требует
#: AGPL от интерактивных программ, а «как это работает» есть и в README.
HELP_SECTIONS: list[tuple[str, list[tuple[str, str]], int]] = [
    (
        "Как это работает",
        [
            ("", "Площадка разрешает поднимать резюме раз в 4 часа. Программа"),
            ("", "узнаёт точное время, добавляет случайную задержку 45–240 с"),
            ("", "и поднимает сама. Окно свернёте — работает, закроете — нет."),
        ],
        2,
    ),
    (
        "Горячие клавиши",
        [
            ("Q", "выйти (автопилот остановится)"),
            ("R", "перечитать список резюме прямо сейчас"),
            ("B", "поднять всё, что разрешено поднять сию секунду"),
            ("P", "пауза — резюме перестают подниматься, но окно живёт"),
            ("A", "заново пройти вход в аккаунт"),
            ("N", "подключить ещё один аккаунт на своей вкладке"),
            ("Tab", "следующая вкладка, Shift+Tab — предыдущая"),
            ("D", "отключить аккаунт открытой вкладки"),
            ("1…9", "включить или выключить резюме под номером в таблице"),
            ("S / L", "статистика просмотров · файл журнала"),
            ("H", "закрыть эту справку"),
        ],
        0,
    ),
    (
        "Лицензия",
        [
            ("", f"© 2026 Алексей Коняев. {APP_NAME} — свободная программа под"),
            ("", "GNU AGPL v3, без каких-либо гарантий. Подробнее: --license"),
        ],
        1,
    ),
]


def window_title(snap: Snapshot, account: str = "") -> str:
    """Заголовок окна: состояние автопилота одной строкой.

    Окно бывает свёрнуто весь рабочий день, и в панели задач от программы
    видно только заголовок. Поэтому в нём то же, ради чего окно
    разворачивают: сколько осталось до поднятия и не встал ли автопилот.
    Состояние идёт первым — панель задач обрезает заголовок справа.
    """
    if snap.paused:
        state = "пауза"
    elif snap.phase == Phase.OFFLINE:
        state = "нет сети"
    elif snap.phase == Phase.AUTH:
        state = "нужен вход"
    elif snap.phase == Phase.BUMPING:
        state = "поднимаем"
    elif snap.phase == Phase.WAITING and snap.next_action_at is not None:
        state = f"через {human_span(snap.next_action_at - time.time())}"
    else:
        state = PHASE_LABEL.get(snap.phase, snap.phase).lower()
    if account:
        state = f"{account} · {state}"
    return f"{state} — {APP_NAME}"


def _signed(value: int) -> str:
    """Прирост всегда со знаком: «85» и «+85» читаются по-разному."""
    return f"{value:+d}"


def _decimal(value: float) -> str:
    """Дробное по-русски, через запятую."""
    return f"{value:.1f}".replace(".", ",")


def _day(iso: str) -> str:
    """`2026-08-20` -> `20.08`. Пустое остаётся пустым."""
    parts = iso.split("-")
    return f"{parts[2]}.{parts[1]}" if len(parts) == 3 else iso


def _rule(width: int) -> Text:
    """Градиентная линия-разделитель ровно на всю ширину."""
    width = max(1, width)
    text = Text(no_wrap=True)
    drawn = 0
    for index, color in enumerate(FLAME):
        # Границы кусков считаем от общей ширины, чтобы не терять символы.
        edge = round(width * (index + 1) / len(FLAME))
        size = edge - drawn
        if size > 0:
            text.append("─" * size, style=color)
            drawn = edge
    return text


def _panel(body: RenderableType, title: str, *, hot: bool = False) -> Panel:
    return Panel(
        body,
        title=f"[bold {ACCENT_SOFT}]{title}[/]",
        title_align="left",
        border_style=FRAME_HOT if hot else FRAME,
        box=box.ROUNDED,
        padding=(0, 1),
    )


class Dashboard:
    def __init__(self, console: Console, log: LogBus) -> None:
        self.console = console
        self.log = log
        self.tick = 0
        self.show_help = False
        self.show_stats = False
        #: Сводка для экрана статистики. Считает её приложение при открытии:
        #: данные меняются раз в четверть часа, каждый кадр их пересчитывать
        #: незачем.
        self.stats: history.Report | None = None
        self._toast = ""
        self._toast_until = 0.0

    # ------------------------------------------------------------- toasts

    def toast(self, message: str, seconds: float = 4.0) -> None:
        self._toast = message
        self._toast_until = time.time() + seconds

    def _toast_line(self, width: int, *, last_log: bool = False) -> Text:
        """Строка под телом: подсказка, а в её отсутствие — свежая запись.

        Совсем низкому окну панель журнала не достаётся, и тогда эта строка
        остаётся единственным местом, где видно, что программа что-то делает.
        """
        if time.time() <= self._toast_until and self._toast:
            line = Text(f"  {self._toast}", style=f"bold {ACCENT_SOFT}",
                        no_wrap=True, overflow="ellipsis")
        elif last_log:
            entries = self.log.tail(1)
            if not entries:
                return Text("")
            line = Text(f"  {entries[0].clock}  {entries[0].text}", style=MUTED,
                        no_wrap=True, overflow="ellipsis")
        else:
            return Text("")
        line.truncate(max(4, width), overflow="ellipsis")
        return line

    # ------------------------------------------------------------- блоки

    def _header(self, snap: Snapshot, width: int, tall: bool) -> RenderableType:
        # tick нужен значку: раз в несколько секунд глаз моргает.
        head = banner.render(width, compact=not tall, tick=self.tick)
        return Group(head, _rule(width))

    @staticmethod
    def _fit_resumes(snap: Snapshot, rows: int) -> tuple[list[tuple[int, object]], int]:
        """Что поместится в таблицу: у резюме с проблемой строки две.

        Если влезают не все, одну строку придерживаем под счётчик спрятанных:
        молча обрезанный список выглядит как полный, и это хуже всего.
        """
        budget = max(1, rows)
        total = len(snap.resumes)
        shown: list[tuple[int, object]] = []
        used = 0
        for index, item in enumerate(snap.resumes, start=1):
            cost = 2 if item.problem else 1
            reserve = 1 if index < total and budget >= 3 else 0
            if used + cost + reserve > budget:
                break
            shown.append((index, item))
            used += cost
        return shown, used

    def _resume_table(self, snap: Snapshot, width: int, rows: int) -> tuple[RenderableType, str]:
        """Таблица резюме и заголовок для её панели."""
        table = Table(
            box=box.SIMPLE_HEAD,
            header_style=f"bold {ACCENT}",
            border_style=FRAME,
            expand=True,
            pad_edge=False,
            show_edge=False,
            padding=(0, 1),
        )
        roomy = width >= 74
        table.add_column("#", width=2, style=MUTED, justify="right")
        # Обрезкой заведует сам Rich: он один знает итоговую ширину колонки.
        table.add_column("РЕЗЮМЕ", ratio=1, no_wrap=True, overflow="ellipsis", min_width=12)
        table.add_column("СТАТУС", width=15 if roomy else 12, no_wrap=True, overflow="ellipsis")
        if roomy:
            table.add_column("ПРОСМ.", width=9, justify="right", no_wrap=True)
        table.add_column("СЛЕДУЮЩЕЕ", width=11, justify="right", no_wrap=True)

        blanks = ["", "", ""] if roomy else ["", ""]
        if not snap.resumes:
            hint = "ещё не загружено" if snap.phase in (Phase.STARTING, Phase.SYNCING) else "резюме не найдены"
            table.add_row("", Text(hint, style=MUTED), *blanks)
            return table, "РЕЗЮМЕ"

        now = time.time()
        shown, used = self._fit_resumes(snap, rows)
        for index, item in shown:
            name = Text(item.title, style="white", no_wrap=True, overflow="ellipsis")
            if item.problem:
                # Отступ и уголок: без них причина отказа читается как название
                # следующего резюме. Символ рамочный — он есть в любом шрифте
                # терминала, в отличие от стрелок.
                name.append("\n  └ " + item.problem, style=ERR)

            if item.blocked:
                status, when = Text("■ заблокировано", style=ERR), Text("—", style=MUTED)
            elif not item.finished:
                status, when = Text("■ не заполнено", style=WARN_COLOR), Text("—", style=MUTED)
            elif item.planned_at is None:
                status, when = Text("○ выключено", style=MUTED), Text("—", style=MUTED)
            elif item.problem:
                status = Text("✕ ошибка", style=ERR)
                when = Text(human_span(max(0.0, item.planned_at - now), short=True), style=MUTED)
            elif item.can_publish and item.planned_at <= now:
                status = Text(f"{spinner(self.tick)} поднимаем", style=ACCENT)
                when = Text("сейчас", style=ACCENT)
            else:
                status = Text("● в работе", style=OK_COLOR)
                left = max(item.planned_at - now, item.seconds_to_allowed)
                when = Text(human_span(left, short=True), style=ACCENT_SOFT)

            cells = [str(index), name, status]
            if roomy:
                cells.append(self._views_cell(item))
            cells.append(when)
            table.add_row(*cells)

        hidden = len(snap.resumes) - len(shown)
        if not hidden:
            return table, "РЕЗЮМЕ"
        if used < max(1, rows):
            table.add_row("", Text(f"…и ещё {hidden}", style=MUTED), *blanks)
            return table, "РЕЗЮМЕ"
        # Строки под счётчик не нашлось — говорим о недоборе в заголовке
        # панели: молча обрезанный список выглядит как полный.
        return table, f"РЕЗЮМЕ · показаны {len(shown)} из {len(snap.resumes)}"

    @staticmethod
    def _views_cell(item) -> Text:
        if item.total_views is None:
            return Text("—", style=MUTED)
        cell = Text(str(item.total_views), style=MUTED)
        if item.new_views:
            cell.append(f"+{item.new_views}", style=OK_COLOR)
        return cell

    def _phase_head(self, snap: Snapshot, inner_width: int) -> RenderableType:
        style = PHASE_STYLE.get(snap.phase, ACCENT)
        label = PHASE_LABEL.get(snap.phase, snap.phase.upper())
        head = Text(no_wrap=True, overflow="ellipsis")
        head.append(f"{spinner(self.tick)} ", style=style)
        head.append(label, style=f"bold {style}")
        if not snap.detail:
            return head
        detail = Text(f"  {snap.detail}", style=MUTED, no_wrap=True, overflow="ellipsis")
        detail.truncate(max(4, inner_width), overflow="ellipsis")
        return Group(head, detail)

    def _status_entries(self, snap: Snapshot, width: int) -> list[tuple[int, str, Text]]:
        """Строки сводки: (очерёдность вытеснения, подпись, значение).

        Список идёт в порядке показа, а число говорит, чем жертвовать первым,
        когда строк не хватает: 0 остаётся всегда, дальше по возрастанию.
        Обратного отсчёта здесь нет намеренно — под него место резервируется
        раньше сводки, потому что ради него на панель и смотрят.
        """
        entries: list[tuple[int, str, Text]] = []

        if snap.account:
            # Имя может быть длинным (в него уходит и почта), а колонка узкая.
            name = Text(snap.account, style="bold white", no_wrap=True, overflow="ellipsis")
            name.truncate(max(6, width - 10), overflow="ellipsis")
            entries.append((3, "Аккаунт", name))

        entries.append((7, "В работе", Text(
            human_span(time.time() - snap.started_at, short=True), style="bold white")))
        entries.append((1, "Резюме", Text(
            f"{snap.managed_count} из {len(snap.resumes)}", style="bold white")))
        entries.append((2, "За сеанс", Text(str(snap.session_bumps), style="bold white")))
        entries.append((5, "Всего", Text(str(snap.total_bumps), style="bold white")))
        entries.append((6, "Последнее", Text(
            datetime.fromtimestamp(snap.last_bump_at).strftime("%d.%m %H:%M")
            if snap.last_bump_at else "—", style="bold white")))

        if snap.token_seconds_left:
            days = snap.token_seconds_left / 86400
            entries.append((4, "Токен", Text(
                f"{days:.0f} дн", style=OK_COLOR if days > 3 else WARN_COLOR)))
        else:
            entries.append((4, "Токен", Text(
                "бессрочно?" if snap.account else "—", style=MUTED)))

        if snap.offline_since:
            entries.append((0, "Без сети", Text(
                human_span(time.time() - snap.offline_since, short=True), style=WARN_COLOR)))
        return entries

    @staticmethod
    def _summary_grid(rows: list[tuple[int, str, Text]]) -> Table:
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(style=MUTED, no_wrap=True)
        grid.add_column(justify="right", no_wrap=True)
        for _, key, value in rows:
            grid.add_row(key, value)
        return grid

    def _status_panel(self, snap: Snapshot, height: int, width: int) -> RenderableType:
        inner = max(8, width - 4)
        head = self._phase_head(snap, inner)
        head_rows = 2 if snap.detail else 1

        # Считаем строки честно: панель обрезает молча. Порядок раздачи —
        # заголовок, обратный отсчёт, сводка по очерёдности, и только из
        # остатка пустые строки-разделители.
        budget = max(3, height - 2)          # минус рамка панели
        countdown = self._countdown(snap, inner)
        countdown_rows = 2 if countdown is not None else 0

        entries = self._status_entries(snap, inner)
        room = max(0, budget - head_rows - countdown_rows)
        # Пустая строка под заголовком — часть рисунка панели, а не остаток:
        # без неё сводка слипается с фазой. Уступает она только тем строкам,
        # без которых сводка перестаёт что-либо значить.
        gap = 1 if room > MIN_SUMMARY_ROWS else 0
        room -= gap

        by_priority = sorted(range(len(entries)), key=lambda i: entries[i][0])
        keep = set(by_priority[:room])
        rows = [entry for index, entry in enumerate(entries) if index in keep]

        parts: list[RenderableType] = [head]
        if gap:
            parts.append(Text())
        if rows:
            parts.append(self._summary_grid(rows))
        if countdown is not None:
            if room - len(rows) > 0:
                parts.append(Text())
            parts.append(countdown)

        hot = snap.phase in (Phase.BUMPING, Phase.AUTH)
        return _panel(Group(*parts), "СТАТУС", hot=hot)

    def _compact_status(self, snap: Snapshot, width: int, height: int) -> RenderableType:
        """Сводка для узких окон — экономит место под таблицу.

        Строк достаётся от одной до четырёх, поэтому содержимое собирается под
        выданный бюджет. Обратный отсчёт держится до последнего: в одну строку
        он уезжает к фазе, а жертвуют сначала полосой, потом счётчиками.
        """
        inner = max(8, width - 4)
        rows = max(1, height - 2)
        hot = snap.phase in (Phase.BUMPING, Phase.AUTH)

        if rows == 1:
            return _panel(self._status_oneliner(snap, inner), "СТАТУС", hot=hot)

        style = PHASE_STYLE.get(snap.phase, ACCENT)
        head = Text(no_wrap=True, overflow="ellipsis")
        head.append(f"{spinner(self.tick)} ", style=style)
        head.append(PHASE_LABEL.get(snap.phase, snap.phase.upper()), style=f"bold {style}")
        if snap.detail:
            head.append(f"  ·  {snap.detail}", style=MUTED)
        head.truncate(inner, overflow="ellipsis")

        # Кусочки добавляем только целиком: обрезанный по букве «токен 11 …»
        # или повисший разделитель читаются как сбой отрисовки.
        facts = Text(no_wrap=True, overflow="ellipsis")

        full = False

        def fact(value: str, style: str) -> None:
            nonlocal full
            gap = len(FACT_SEPARATOR) if facts.cell_len else 0
            if full or facts.cell_len + gap + len(value) > inner:
                # Дальше не идём: иначе строка теряет важное и показывает
                # то, что покороче, а состав скачет от кадра к кадру.
                full = True
                return
            if gap:
                facts.append(FACT_SEPARATOR, style=MUTED)
            facts.append(value, style=style)

        fact(f"{snap.managed_count}/{len(snap.resumes)} резюме", "bold white")
        fact(f"за сеанс {snap.session_bumps}", "bold white")
        fact(f"всего {snap.total_bumps}", "bold white")
        if snap.token_seconds_left:
            fact(f"токен {snap.token_seconds_left / 86400:.0f} дн", MUTED)
        if snap.account:
            fact(snap.account, MUTED)
        facts.truncate(inner, overflow="ellipsis")

        line = self._countdown_line(snap)
        parts: list[RenderableType] = [head]
        left = rows - 1
        # Счётчику нужна строка: сводку показываем, только если она не отнимет
        # у него последнее место.
        if left >= (2 if line is not None else 1):
            parts.append(facts)
            left -= 1
        if line is not None and left >= 1:
            parts.append(line)
            left -= 1
            if left >= 1:
                parts.append(self._countdown_bar(snap, inner))
        return _panel(Group(*parts), "СТАТУС", hot=hot)

    def _status_oneliner(self, snap: Snapshot, width: int) -> Text:
        """Самое нужное в одной строке: фаза, счёт резюме и обратный отсчёт."""
        style = PHASE_STYLE.get(snap.phase, ACCENT)
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append(f"{spinner(self.tick)} ", style=style)
        text.append(PHASE_LABEL.get(snap.phase, snap.phase.upper()), style=f"bold {style}")
        text.append("  ·  ", style=MUTED)
        text.append(f"{snap.managed_count}/{len(snap.resumes)} резюме", style="bold white")
        if snap.next_action_at is not None:
            left = max(0.0, snap.next_action_at - time.time())
            text.append("  ·  ", style=MUTED)
            text.append(f"через {human_span(left)}", style=f"bold {ACCENT_SOFT}")
        text.truncate(width, overflow="ellipsis")
        return text

    def _countdown_line(self, snap: Snapshot) -> RenderableType | None:
        """Подпись обратного отсчёта — одна строка."""
        if snap.next_action_at is None:
            return None
        left = max(0.0, snap.next_action_at - time.time())
        line = Table.grid(expand=True)
        line.add_column(no_wrap=True)
        line.add_column(justify="right", no_wrap=True)
        line.add_row(
            Text("Следующее поднятие", style=MUTED),
            Text(human_span(left), style=f"bold {ACCENT_SOFT}"),
        )
        return line

    def _countdown_bar(self, snap: Snapshot, width: int) -> Text:
        left = max(0.0, (snap.next_action_at or 0.0) - time.time())
        span = max(snap.wait_span, 1.0)
        done = 1.0 - min(1.0, left / span)
        return Text.from_markup(gradient_bar(done, max(8, min(width, 40))))

    def _countdown(self, snap: Snapshot, width: int) -> RenderableType | None:
        """Подпись вместе с полосой — две строки."""
        line = self._countdown_line(snap)
        if line is None:
            return None
        return Group(line, self._countdown_bar(snap, width))

    def _log_panel(self, lines: int, width: int) -> RenderableType:
        entries = self.log.tail(lines)
        if not entries:
            body: RenderableType = Text("пока тихо", style=MUTED)
        else:
            # Ширину текста считаем сами. Колонка без переноса просит ровно
            # столько, сколько в ней символов, и Rich начинает ужимать соседние:
            # время сжималось до «12:0…». А без no_wrap не работает overflow, и
            # запись переносится на вторую строку без времени и значка, вытесняя
            # снизу столько же записей.
            room = max(12, width - LOG_CHROME_WIDTH)
            grid = Table.grid(padding=(0, 1))
            grid.add_column(style=MUTED, no_wrap=True, width=LOG_CLOCK_WIDTH)
            grid.add_column(no_wrap=True, width=1)
            grid.add_column(width=room, no_wrap=True, overflow="ellipsis")
            for entry in entries:
                mark, color = LEVEL_MARK.get(entry.level, ("·", MUTED))
                text_style = ERR if entry.level == ERROR else (
                    WARN_COLOR if entry.level == WARN else "white"
                )
                line = Text(entry.text, style=text_style, no_wrap=True, overflow="ellipsis")
                line.truncate(room, overflow="ellipsis")
                grid.add_row(entry.clock, Text(mark, style=color), line)
            body = grid
        return _panel(body, "ЖУРНАЛ")

    @staticmethod
    def _help_rows(height: int) -> list[tuple[str, str]]:
        """Собрать справку под доступную высоту, начиная с обязательного."""
        room = max(3, height - 2)  # минус рамка панели

        def cost(indexes: list[int]) -> int:
            # Заголовок + строки на каждый раздел, плюс пустая строка между.
            total = sum(1 + len(HELP_SECTIONS[i][1]) for i in indexes)
            return total + max(0, len(indexes) - 1)

        # Добавляем разделы в порядке важности, пока помещаются.
        chosen: list[int] = []
        by_priority = sorted(range(len(HELP_SECTIONS)), key=lambda i: HELP_SECTIONS[i][2])
        for index in by_priority:
            candidate = sorted(chosen + [index])
            if not chosen or cost(candidate) <= room:
                chosen = candidate

        rows: list[tuple[str, str]] = []
        for position, index in enumerate(sorted(chosen)):
            if position:
                rows.append(("", ""))
            title, body, _ = HELP_SECTIONS[index]
            rows.append((title, ""))
            rows.extend(body)
        return rows[:room]

    def _help_panel(self, height: int) -> RenderableType:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=f"bold {ACCENT_SOFT}", width=6, justify="right", no_wrap=True)
        grid.add_column(overflow="ellipsis", no_wrap=True)
        for key, text in self._help_rows(height):
            if key and not text:
                grid.add_row("", Text(key, style=f"bold {ACCENT}"))
            else:
                grid.add_row(key, Text(text, style="white" if key else MUTED))
        return _panel(grid, "СПРАВКА", hot=True)

    @staticmethod
    def _stats_rows(report: "history.Report | None", room: int) -> list[tuple[str, str]]:
        """Сводка под доступную высоту. Пустая правая колонка = заголовок.

        Первым идёт то, ради чего экран открывают: что принесла неделя.
        Разбивка по резюме обрезается снизу, но молчать об обрезке нельзя —
        то же правило, что у таблицы резюме на главном экране.
        """
        if report is None or report.empty:
            return [
                ("Пока не о чем рассказать", ""),
                ("Прирост считается разностью суточных срезов:", ""),
                ("первый уже сделан, сводка появится завтра.", ""),
            ][:max(1, room)]

        rows: list[tuple[str, str]] = [(f"За {report.days} дней", "")]
        rows.append(("Просмотров", _signed(report.views)))
        # «К прошлой неделе» — про просмотры, поэтому идёт вплотную к ним:
        # между ними ничего не вставлять, иначе строка читается как чужая.
        change = report.change
        rows.append(("К прошлой неделе", _signed(change) if change else "столько же"))
        if report.has_talks:
            rows.append(("Приглашений", _signed(report.invitations_gained)))
        rows.append(("Поднятий", str(report.bumps)))
        per_bump = report.per_bump
        rows.append(("На одно поднятие", _decimal(per_bump) if per_bump is not None else "—"))
        # О пропусках говорим, только когда они есть: строка на счету.
        if report.covered < report.days:
            rows.append(("Данные за", f"{report.covered} из {report.days} дней"))

        # Итоги считаем раньше разбивки и место под них резервируем. Разбивка
        # длинная и её не жалко обрезать, а «приглашений 39 из 355» — то, ради
        # чего экран открывают во второй раз. Раньше список резюме съедал всё,
        # и итогов не было видно ни на одном размере окна.
        footer = [("", ""), ("Всего просмотров", str(report.total_views))]
        if report.has_talks:
            footer.append(("Приглашений", f"{report.invitations} из {report.talks}"))
        footer.append(("Наблюдаем с", _day(report.since)))
        if room < len(rows) + len(footer):
            footer = []

        # Разбивке нужны отбивка, заголовок и хотя бы одна строка.
        if room >= len(rows) + len(footer) + 3:
            rows.append(("", ""))
            rows.append(("По резюме", ""))
            free = room - len(rows) - len(footer)
            shown = report.resumes
            if len(shown) > free:
                # Место под строку «и ещё N» отнимаем у самих резюме.
                shown = shown[:max(0, free - 1)]
            for item in shown:
                value = f"{_signed(item.views)} · всего {item.total}"
                if item.invitations:
                    value += f" · пригл. {item.invitations}"
                rows.append((item.title, value))
            hidden = len(report.resumes) - len(shown)
            if hidden > 0:
                rows.append((f"…и ещё {hidden}", ""))

        rows.extend(footer)
        return rows[:room]

    def _stats_panel(self, height: int) -> RenderableType:
        # expand=True обязателен: без него grid ужимается по содержимому,
        # ratio у первой колонки не действует и значения виснут посередине.
        grid = Table.grid(padding=(0, 2), expand=True)
        grid.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
        grid.add_column(justify="right", no_wrap=True)
        for label, value in self._stats_rows(self.stats, max(3, height - 2)):
            if not label and not value:
                grid.add_row("", "")
            elif not value:
                grid.add_row(Text(label, style=f"bold {ACCENT}"), "")
            else:
                grid.add_row(Text(label, style=MUTED), Text(value, style="bold white"))
        return _panel(grid, "СТАТИСТИКА", hot=True)


    @staticmethod
    def _tab_name(index: int, tab: TabInfo, limit: int = TAB_NAME_LIMIT) -> str:
        """Подпись вкладки. Имя владельца известно не сразу — до тех пор номер."""
        name = tab.label.strip() or f"Аккаунт {index + 1}"
        if len(name) > limit:
            name = name[:max(1, limit - 1)] + "…"
        return name

    def _tab_chunk(self, line: Text, chunk: str, tab: TabInfo, *, current: bool) -> None:
        """Открытая вкладка — белым, остальные серым, без цветных плашек.

        Значок состояния красится по фазе у всех вкладок: по нему видно, чем
        занят аккаунт, на который сейчас не смотрят.
        """
        body = "bold white" if current else MUTED
        line.append(chunk[:1], style=body)
        line.append(chunk[1:2], style=PHASE_STYLE.get(tab.phase, ACCENT))
        line.append(chunk[2:], style=body)

    def _tabs_line(self, tabs: list[TabInfo], active: int, width: int) -> Text:
        """Полоса вкладок. Открытая обязана в неё попасть, остальные — как выйдет."""
        # Имена ужимаем под число вкладок: короткая подпись у всех полезнее,
        # чем полная у двоих и «+4» вместо всех остальных.
        limit = max(6, min(TAB_NAME_LIMIT, width // max(1, len(tabs)) - 6))
        chunks = [f" ● {index + 1} {self._tab_name(index, tab, limit)} "
                  for index, tab in enumerate(tabs)]

        # Начало сдвигаем ровно настолько, чтобы открытая вкладка поместилась:
        # без неё непонятно, к какому аккаунту относится всё остальное.
        start = 0
        while start < active and sum(len(c) + 1 for c in chunks[start:active + 1]) - 1 > width:
            start += 1

        line = Text(no_wrap=True, overflow="crop")
        shown = 0
        for index in range(start, len(chunks)):
            gap = 1 if line.cell_len else 0
            if line.cell_len + gap + len(chunks[index]) > width:
                break
            if gap:
                line.append(" ")
            self._tab_chunk(line, chunks[index], tabs[index], current=index == active)
            shown += 1

        if not shown:
            # Даже открытая вкладка целиком не влезла — покажем сколько влезет.
            self._tab_chunk(line, chunks[active], tabs[active], current=True)
            line.truncate(max(1, width), overflow="ellipsis")
            return line

        hidden = len(tabs) - shown
        tail = f" +{hidden}"
        if hidden and line.cell_len + len(tail) <= width:
            line.append(tail, style=MUTED)
        line.truncate(max(1, width), overflow="crop")
        return line

    def _footer(self, width: int, *, multi: bool = False) -> RenderableType:
        """Подсказки по клавишам: сколько влезло, столько и показываем."""
        text = Text(no_wrap=True, overflow="crop")
        used = 0
        for key, label in hotkeys(multi):
            chunk = len(key) + len(label) + 5
            if used + chunk > width:
                break
            text.append(f" {key} ", style=f"bold {ACCENT_SOFT} reverse")
            text.append(f" {label}   ", style=MUTED)
            used += chunk
        return text

    # ------------------------------------------------------------ сборка

    @staticmethod
    def _log_size(height: int) -> int:
        """Высота панели журнала. Ноль — окно слишком низкое даже для неё.

        В совсем маленьком окне журнал уступает место таблице: подписанная
        коробка на две строки пользы не приносит, а последняя запись всё равно
        видна в строке под телом.
        """
        if height >= 34:
            return 10
        if height >= 28:
            return 8
        if height >= 22:
            return 6
        if height >= 18:
            return 4
        return 0

    def render(
        self,
        snap: Snapshot,
        tabs: list[TabInfo] | None = None,
        active: int = 0,
    ) -> RenderableType:
        width, height = self.console.size
        wide = width >= WIDE_LAYOUT_WIDTH
        log_size = self._log_size(height)
        # Полоса вкладок появляется только со второго аккаунта: одному
        # переключаться некуда, а строка в низком окне на счету.
        tabs = list(tabs or [])
        tabs_size = 1 if len(tabs) > 1 else 0

        # Логотип показываем везде, где он не съедает содержимое: считаем, что
        # останется телу, и сравниваем с необходимым минимумом. Жёсткий порог
        # по высоте убирал заголовок и в тех окнах, где места хватало с лихвой.
        art_rows = banner.art_height(width, compact=False)
        tall_header = art_rows > 0 and (
            height - (art_rows + 1) - log_size - tabs_size - 2
            >= (MIN_WIDE_BODY if wide else MIN_NARROW_BODY)
        )
        header_size = (art_rows + 1) if tall_header else COMPACT_HEADER_ROWS

        # Сколько строк реально достанется телу: панели считают по этому
        # числу, и ошибиться нельзя — лишнее они обрежут молча.
        body_height = max(3, height - header_size - log_size - tabs_size - 2)

        rows = [Layout(name="header", size=header_size)]
        if tabs_size:
            rows.append(Layout(name="tabs", size=tabs_size))
        rows.append(Layout(name="body", ratio=1))
        if log_size:
            rows.append(Layout(name="log", size=log_size))
        rows.append(Layout(name="toast", size=1))
        rows.append(Layout(name="footer", size=1))

        root = Layout(name="root")
        root.split_column(*rows)

        root["header"].update(self._header(snap, width, tall_header))
        if tabs_size:
            root["tabs"].update(self._tabs_line(tabs, active, width))

        if self.show_help:
            root["body"].update(self._help_panel(body_height))
        elif self.show_stats:
            root["body"].update(self._stats_panel(body_height))
        elif wide:
            side_width = 34 if width >= 108 else 30
            root["body"].split_row(
                Layout(name="resumes", ratio=1),
                Layout(name="side", size=side_width),
            )
            table, title = self._resume_table(
                snap, width - side_width - 4, body_height - TABLE_CHROME_ROWS
            )
            root["body"]["resumes"].update(_panel(table, title))
            root["body"]["side"].update(
                self._status_panel(snap, body_height, side_width)
            )
        else:
            # Сводка сверху, таблица под ней. Сводка ужимается первой: пустая
            # подписанная коробка вместо таблицы — худшее, что здесь может быть.
            side_size = min(COMPACT_STATUS_ROWS, max(3, body_height - MIN_TABLE_PANEL))
            table_size = body_height - side_size
            root["body"].split_column(
                Layout(name="side", size=side_size),
                Layout(name="resumes", ratio=1),
            )
            table, title = self._resume_table(
                snap, width - 4, table_size - TABLE_CHROME_ROWS
            )
            root["body"]["side"].update(self._compact_status(snap, width, side_size))
            root["body"]["resumes"].update(_panel(table, title))

        if log_size:
            root["log"].update(self._log_panel(log_size - 2, width))
        root["toast"].update(self._toast_line(width, last_log=not log_size))
        root["footer"].update(Align.left(self._footer(width - 1, multi=tabs_size > 0)))
        return root
