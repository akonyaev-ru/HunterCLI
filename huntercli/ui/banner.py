"""ASCII-заголовок с огненным градиентом."""

from __future__ import annotations

from rich.align import Align
from rich.console import Group
from rich.text import Text

from .. import APP_NAME, APP_TAGLINE, AUTHOR, __version__, logo
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

#: Отступ между значком и надписью.
MARK_GAP = 3
#: Значок рисуется полублоками: две строки матрицы дают одну строку экрана,
#: поэтому логические пиксели выходят квадратными.
MARK_HEIGHT = (logo.HEIGHT + 1) // 2
MARK_WIDTH = logo.WIDTH
FULL_WIDTH = MARK_WIDTH + MARK_GAP + BIG_WIDTH

#: Ниже этой ширины логотип не рисуем совсем.
MIN_ART_WIDTH = MID_WIDTH + 4


def mark_lines() -> list[Text]:
    """Значок из logo.png: пиксель-арт полублоками, в цветах надписи."""
    rows: list[Text] = []
    for index in range(MARK_HEIGHT):
        top = logo.LOGO[index * 2]
        below = index * 2 + 1
        bottom = logo.LOGO[below] if below < logo.HEIGHT else "." * logo.WIDTH
        glyphs = "".join(
            {(True, True): "█", (True, False): "▀", (False, True): "▄"}.get(
                (top[col] == "#", bottom[col] == "#"), " "
            )
            for col in range(logo.WIDTH)
        )
        color = FLAME[min(index, len(FLAME) - 1)]
        rows.append(Text(glyphs, style=color, no_wrap=True))
    return rows


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


def _with_mark(art: list[str]) -> list[Text]:
    """Значок слева, надпись справа. Надпись опущена на строку — так их
    оптические центры совпадают: значок на строку выше."""
    mark = mark_lines()
    words = _flame_lines(art)
    lines: list[Text] = []
    for index in range(MARK_HEIGHT):
        row = Text(no_wrap=True)
        row.append_text(mark[index])
        row.append(" " * MARK_GAP)
        word_index = index - (MARK_HEIGHT - len(words))
        if 0 <= word_index < len(words):
            row.append_text(words[word_index])
        else:
            row.append(" " * len(art[0]))
        lines.append(row)
    return lines


def art_lines(width: int) -> list[Text] | None:
    """Готовые строки логотипа под ширину окна."""
    art = _art_for(width)
    if art is None:
        return None
    if art is BIG and width >= FULL_WIDTH + 4:
        return _with_mark(art)
    return _flame_lines(art)


def subtitle(width: int = 120, *, with_name: bool = False) -> Text:
    """Подпись под логотипом: что это, какая версия и чьё авторство.

    Чем уже окно, тем меньше частей помещается — описание уходит первым.
    Название приложения добавляется, только когда логотип не поместился:
    иначе оно уже нарисовано большими буквами прямо над этой строкой.
    """
    chunks: list[tuple[str, str]] = []
    if with_name:
        chunks.append((APP_NAME, f"bold {ACCENT}"))
    if width >= 76:
        chunks.append((APP_TAGLINE, MUTED))
    chunks.append((f"версия {__version__}", MUTED))
    chunks.append((AUTHOR, ACCENT_SOFT))

    text = Text(no_wrap=True, overflow="ellipsis")
    for index, (value, style) in enumerate(chunks):
        if index:
            text.append("   ·   ", style=MUTED)
        text.append(value, style=style)
    return text


def art_height(width: int, *, compact: bool = False) -> int:
    """Сколько строк займёт логотип. Нужно, чтобы отвести под шапку место."""
    if compact:
        return 0
    lines = art_lines(width)
    return len(lines) if lines else 0


def render(width: int, *, compact: bool = False) -> Group:
    """Заголовок, подстраивающийся под ширину окна."""
    width = max(1, width)
    lines = None if compact else art_lines(width)

    if lines is None:
        return Group(Align.center(subtitle(width, with_name=True), width=width))

    parts = [Align.center(line, width=width) for line in lines]
    parts.append(Align.center(subtitle(width), width=width))
    return Group(*parts)


def plain_header(width: int = 120) -> Group:
    """Заголовок для обычной (не полноэкранной) печати."""
    lines = art_lines(width)
    if lines is None:
        return Group(subtitle(width, with_name=True))
    parts: list[object] = list(lines)
    parts.append(Text())
    parts.append(subtitle(width))
    return Group(*parts)
