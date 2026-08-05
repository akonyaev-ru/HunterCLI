"""Полноэкранный дашборд автопилота."""

from __future__ import annotations

import time
from datetime import datetime

from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

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
    ellipsis,
    gradient_bar,
    human_span,
    spinner,
)

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
    ("A", "перевойти"),
    ("1-9", "вкл/выкл резюме"),
    ("H", "справка"),
]

HELP_TEXT = [
    ("Как это работает", ""),
    ("", "Площадка разрешает поднимать резюме раз в 4 часа. Hunter CLI"),
    ("", "спрашивает точное время следующего разрешённого поднятия,"),
    ("", "добавляет случайную задержку (45–240 с) и поднимает резюме сам."),
    ("", ""),
    ("Горячие клавиши", ""),
    ("Q", "выйти (автопилот остановится)"),
    ("R", "перечитать список резюме прямо сейчас"),
    ("B", "поднять всё, что разрешено поднять сию секунду"),
    ("P", "пауза — резюме перестают подниматься, но окно живёт"),
    ("A", "заново пройти вход в аккаунт"),
    ("1…9", "включить или выключить резюме под номером в таблице"),
    ("L", "открыть файл журнала рядом с программой"),
    ("H", "закрыть эту справку"),
    ("", ""),
    ("Фоновая работа", ""),
    ("", "Окно можно свернуть — автопилот продолжит работать."),
    ("", "Закрывать окно нельзя: программа остановится."),
]


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
        self._toast = ""
        self._toast_until = 0.0

    # ------------------------------------------------------------- toasts

    def toast(self, message: str, seconds: float = 4.0) -> None:
        self._toast = message
        self._toast_until = time.time() + seconds

    def _toast_line(self) -> Text:
        if time.time() > self._toast_until or not self._toast:
            return Text("")
        return Text(f"  {self._toast}", style=f"bold {ACCENT_SOFT}")

    # ------------------------------------------------------------- блоки

    def _header(self, snap: Snapshot, width: int, tall: bool) -> RenderableType:
        right = snap.account or ""
        head = banner.render(width, right, compact=not tall)
        return Group(head, _rule(width))

    def _resume_table(self, snap: Snapshot, width: int) -> RenderableType:
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

        if not snap.resumes:
            hint = "ещё не загружено" if snap.phase in (Phase.STARTING, Phase.SYNCING) else "резюме не найдены"
            blanks = ["", "", ""] if roomy else ["", ""]
            table.add_row("", Text(hint, style=MUTED), *blanks)
            return table

        now = time.time()
        for index, item in enumerate(snap.resumes, start=1):
            name = Text(item.title, style="white", no_wrap=True, overflow="ellipsis")
            if item.problem:
                name.append("\n" + item.problem, style=ERR)

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
        return table

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

    def _status_panel(self, snap: Snapshot, height: int, width: int) -> RenderableType:
        inner = max(8, width - 4)
        head = self._phase_head(snap, inner)

        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(style=MUTED, no_wrap=True)
        grid.add_column(justify="right", style="bold white", no_wrap=True)

        def row(key: str, value: str, style_override: str | None = None) -> None:
            grid.add_row(key, Text(value, style=style_override or "bold white"))

        row("В работе", human_span(time.time() - snap.started_at, short=True))
        row("Резюме", f"{snap.managed_count} из {len(snap.resumes)}")
        row("За сеанс", str(snap.session_bumps))
        row("Всего", str(snap.total_bumps))
        row(
            "Последнее",
            datetime.fromtimestamp(snap.last_bump_at).strftime("%d.%m %H:%M")
            if snap.last_bump_at
            else "—",
        )

        if snap.token_seconds_left:
            days = snap.token_seconds_left / 86400
            token_style = OK_COLOR if days > 3 else WARN_COLOR
            row("Токен", f"{days:.0f} дн", token_style)
        else:
            row("Токен", "бессрочно?" if snap.account else "—", MUTED)

        if snap.offline_since:
            row("Без сети", human_span(time.time() - snap.offline_since, short=True), WARN_COLOR)

        parts: list[RenderableType] = [head, Text(), grid]

        countdown = self._countdown(snap, inner)
        if countdown is not None and height >= 12:
            parts.extend([Text(), countdown])

        hot = snap.phase in (Phase.BUMPING, Phase.AUTH)
        return _panel(Group(*parts), "СТАТУС", hot=hot)

    def _compact_status(self, snap: Snapshot, width: int) -> RenderableType:
        """Сводка в три строки для узких окон — экономит место под таблицу."""
        inner = max(8, width - 4)
        style = PHASE_STYLE.get(snap.phase, ACCENT)
        head = Text(no_wrap=True, overflow="ellipsis")
        head.append(f"{spinner(self.tick)} ", style=style)
        head.append(PHASE_LABEL.get(snap.phase, snap.phase.upper()), style=f"bold {style}")
        if snap.detail:
            head.append(f"  ·  {snap.detail}", style=MUTED)
        head.truncate(inner, overflow="ellipsis")

        facts = Text(no_wrap=True, overflow="ellipsis")
        facts.append(f"{snap.managed_count}/{len(snap.resumes)} резюме", style="bold white")
        facts.append("  ·  ", style=MUTED)
        facts.append(f"за сеанс {snap.session_bumps}", style="bold white")
        facts.append("  ·  ", style=MUTED)
        facts.append(f"всего {snap.total_bumps}", style="bold white")
        if snap.token_seconds_left:
            facts.append("  ·  ", style=MUTED)
            facts.append(f"токен {snap.token_seconds_left / 86400:.0f} дн", style=MUTED)

        parts: list[RenderableType] = [head, facts]
        countdown = self._countdown(snap, inner)
        if countdown is not None:
            parts.append(countdown)
        return _panel(Group(*parts), "СТАТУС", hot=snap.phase in (Phase.BUMPING, Phase.AUTH))

    def _countdown(self, snap: Snapshot, width: int) -> RenderableType | None:
        if snap.next_action_at is None:
            return None
        left = max(0.0, snap.next_action_at - time.time())
        span = max(snap.wait_span, 1.0)
        done = 1.0 - min(1.0, left / span)

        line = Table.grid(expand=True)
        line.add_column(no_wrap=True)
        line.add_column(justify="right", no_wrap=True)
        line.add_row(
            Text("Следующее поднятие", style=MUTED),
            Text(human_span(left), style=f"bold {ACCENT_SOFT}"),
        )
        return Group(line, Text.from_markup(gradient_bar(done, max(8, min(width, 40)))))

    def _log_panel(self, lines: int) -> RenderableType:
        entries = self.log.tail(lines)
        if not entries:
            body: RenderableType = Text("пока тихо", style=MUTED)
        else:
            grid = Table.grid(padding=(0, 1))
            grid.add_column(style=MUTED, no_wrap=True, width=8)
            grid.add_column(no_wrap=True, width=1)
            grid.add_column(overflow="ellipsis", ratio=1)
            for entry in entries:
                mark, color = LEVEL_MARK.get(entry.level, ("·", MUTED))
                text_style = ERR if entry.level == ERROR else (
                    WARN_COLOR if entry.level == WARN else "white"
                )
                grid.add_row(
                    entry.clock,
                    Text(mark, style=color),
                    Text(entry.text, style=text_style),
                )
            body = grid
        return _panel(body, "ЖУРНАЛ")

    def _help_panel(self) -> RenderableType:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(style=f"bold {ACCENT_SOFT}", width=6, justify="right")
        grid.add_column(overflow="fold")
        for key, text in HELP_TEXT:
            if key and not text:
                grid.add_row("", Text(key, style=f"bold {ACCENT}"))
            else:
                grid.add_row(key, Text(text, style="white" if key else MUTED))
        return _panel(grid, "СПРАВКА", hot=True)

    def _footer(self, width: int) -> RenderableType:
        """Подсказки по клавишам: сколько влезло, столько и показываем."""
        text = Text(no_wrap=True, overflow="crop")
        used = 0
        for key, label in HOTKEYS:
            chunk = len(key) + len(label) + 5
            if used + chunk > width:
                break
            text.append(f" {key} ", style=f"bold {ACCENT_SOFT} reverse")
            text.append(f" {label}   ", style=MUTED)
            used += chunk
        return text

    # ------------------------------------------------------------ сборка

    def render(self, snap: Snapshot) -> RenderableType:
        width, height = self.console.size
        # Какой именно логотип поместится, решает сам banner: нам важно лишь,
        # хватает ли места хоть на какой-то.
        tall_header = height >= 30 and width >= banner.MIN_ART_WIDTH
        header_size = 8 if tall_header else 2
        log_size = 10 if height >= 34 else (8 if height >= 28 else 6)
        wide = width >= 92

        root = Layout(name="root")
        root.split_column(
            Layout(name="header", size=header_size),
            Layout(name="body", ratio=1),
            Layout(name="log", size=log_size),
            Layout(name="toast", size=1),
            Layout(name="footer", size=1),
        )

        root["header"].update(self._header(snap, width, tall_header))

        if self.show_help:
            root["body"].update(self._help_panel())
        elif wide:
            side_width = 34 if width >= 108 else 30
            root["body"].split_row(
                Layout(name="resumes", ratio=1),
                Layout(name="side", size=side_width),
            )
            root["body"]["resumes"].update(
                _panel(self._resume_table(snap, width - side_width - 4), "РЕЗЮМЕ")
            )
            root["body"]["side"].update(
                self._status_panel(snap, height - header_size - log_size, side_width)
            )
        else:
            root["body"].split_column(
                Layout(name="side", size=6),
                Layout(name="resumes", ratio=1),
            )
            root["body"]["side"].update(self._compact_status(snap, width))
            root["body"]["resumes"].update(_panel(self._resume_table(snap, width - 4), "РЕЗЮМЕ"))

        root["log"].update(self._log_panel(log_size - 2))
        root["toast"].update(self._toast_line())
        root["footer"].update(Align.left(self._footer(width - 1)))
        return root
