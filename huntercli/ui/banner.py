"""ASCII-заголовок с огненным градиентом."""

from __future__ import annotations

from rich.align import Align
from rich.console import Group
from rich.text import Text

from .. import APP_NAME, AUTHOR, __version__
from .theme import ACCENT, ACCENT_SOFT, FLAME, MUTED

#: Полный логотип: HUNTER CLI.
BIG = [
    "██╗  ██╗██╗   ██╗███╗   ██╗████████╗███████╗██████╗     ██████╗██╗     ██╗",
    "██║  ██║██║   ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗   ██╔════╝██║     ██║",
    "███████║██║   ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝   ██║     ██║     ██║",
    "██╔══██║██║   ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗   ██║     ██║     ██║",
    "██║  ██║╚██████╔╝██║ ╚████║   ██║   ███████╗██║  ██║   ╚██████╗███████╗██║",
    "╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝    ╚═════╝╚══════╝╚═╝",
]

#: Укороченный вариант для узких окон: только HUNTER.
MID = [
    "██╗  ██╗██╗   ██╗███╗   ██╗████████╗███████╗██████╗ ",
    "██║  ██║██║   ██║████╗  ██║╚══██╔══╝██╔════╝██╔══██╗",
    "███████║██║   ██║██╔██╗ ██║   ██║   █████╗  ██████╔╝",
    "██╔══██║██║   ██║██║╚██╗██║   ██║   ██╔══╝  ██╔══██╗",
    "██║  ██║╚██████╔╝██║ ╚████║   ██║   ███████╗██║  ██║",
    "╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝",
]

BIG_WIDTH = max(len(line) for line in BIG)
MID_WIDTH = max(len(line) for line in MID)

#: Ниже этой ширины логотип не рисуем совсем.
MIN_ART_WIDTH = MID_WIDTH + 4


def _flame_lines(lines: list[str]) -> list[Text]:
    return [
        Text(line, style=FLAME[min(index, len(FLAME) - 1)], no_wrap=True)
        for index, line in enumerate(lines)
    ]


def _art_for(width: int) -> list[str] | None:
    if width >= BIG_WIDTH + 4:
        return BIG
    if width >= MIN_ART_WIDTH:
        return MID
    return None


def subtitle(right: str = "", width: int = 120, *, with_name: bool = False) -> Text:
    """Подпись под логотипом: версия, автор и владелец аккаунта.

    Чем уже окно, тем меньше частей помещается — отбрасываем с конца.
    Название приложения добавляется, только когда логотип не поместился:
    иначе оно уже нарисовано большими буквами прямо над этой строкой.
    """
    chunks: list[tuple[str, str]] = []
    if with_name:
        chunks.append((APP_NAME, f"bold {ACCENT}"))
    chunks.append((f"v{__version__}", MUTED))
    if width >= 64:
        chunks.append((AUTHOR, MUTED))
    if right and width >= 40:
        chunks.append((right, ACCENT_SOFT))

    text = Text(no_wrap=True, overflow="ellipsis")
    for index, (value, style) in enumerate(chunks):
        if index:
            text.append("   ·   ", style=MUTED)
        text.append(value, style=style)
    return text


def render(width: int, right: str = "", *, compact: bool = False) -> Group:
    """Заголовок, подстраивающийся под ширину окна."""
    width = max(1, width)
    art = None if compact else _art_for(width)

    if art is None:
        return Group(Align.center(subtitle(right, width, with_name=True), width=width))

    parts = [Align.center(line, width=width) for line in _flame_lines(art)]
    parts.append(Align.center(subtitle(right, width), width=width))
    return Group(*parts)


def plain_header(width: int = 120, right: str = "") -> Group:
    """Заголовок для обычной (не полноэкранной) печати."""
    art = _art_for(width)
    if art is None:
        return Group(subtitle(right, width, with_name=True))
    parts: list[object] = list(_flame_lines(art))
    parts.append(Text())
    parts.append(subtitle(right, width))
    return Group(*parts)
