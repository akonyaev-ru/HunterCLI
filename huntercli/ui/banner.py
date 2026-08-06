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


#: Раз в сколько кадров глаз моргает. Дашборд обновляется примерно восемь раз
#: в секунду, поэтому 82 кадра — это около десяти секунд.
BLINK_PERIOD = 82
#: Кадров в самом моргании: веки сходятся, смыкаются и расходятся обратно.
BLINK_FRAMES = len(logo.BLINK)


#: Моргание стоит в КОНЦЕ периода, а не в начале. Иначе кадр с номером ноль —
#: полуприкрытый глаз, и всё, что рисуется единожды и без счётчика (экран
#: приветствия, экран входа, первый кадр дашборда), показывает закрытый глаз.
BLINK_START = BLINK_PERIOD - BLINK_FRAMES


def blinking(tick: int) -> bool:
    return tick % BLINK_PERIOD >= BLINK_START


def _frame(tick: int) -> tuple[str, ...]:
    phase = tick % BLINK_PERIOD - BLINK_START
    return logo.BLINK[phase] if phase >= 0 else logo.LOGO


def mark_lines(tick: int = 0) -> list[Text]:
    """Значок из logo.png: пиксель-арт полублоками, в цветах надписи."""
    matrix = _frame(tick)
    rows: list[Text] = []
    for index in range(MARK_HEIGHT):
        top = matrix[index * 2]
        below = index * 2 + 1
        bottom = matrix[below] if below < logo.HEIGHT else "." * logo.WIDTH
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


def _with_mark(art: list[str], tick: int) -> list[Text]:
    """Значок слева, надпись справа. Высоты совпадают, поэтому строки идут
    парами без смещения."""
    mark = mark_lines(tick)
    words = _flame_lines(art)
    lines: list[Text] = []
    for index in range(max(MARK_HEIGHT, len(words))):
        row = Text(no_wrap=True)
        if index < len(mark):
            row.append_text(mark[index])
        else:
            row.append(" " * MARK_WIDTH)
        row.append(" " * MARK_GAP)
        if index < len(words):
            row.append_text(words[index])
        lines.append(row)
    return lines


def _shows_mark(width: int, art: list[str] | None) -> bool:
    return art is BIG and width >= FULL_WIDTH + 4


def art_lines(width: int, tick: int = 0) -> list[Text] | None:
    """Готовые строки логотипа под ширину окна."""
    art = _art_for(width)
    if art is None:
        return None
    if _shows_mark(width, art):
        return _with_mark(art, tick)
    return _flame_lines(art)


def _place_subtitle(width: int, art: list[str] | None, sub: Text) -> Text:
    """Подпись центрируется под НАДПИСЬЮ, а не под всем блоком со значком.

    Иначе она уезжает влево: значок тянет оптический центр на себя.
    """
    if not _shows_mark(width, art):
        left = (width - sub.cell_len) / 2
    else:
        # Округление вниз, как у Align.center: она кладёт блок со значком
        # именно так, и от деления пополам подпись уезжала на столбец вправо.
        block_start = (width - FULL_WIDTH) // 2
        word_center = block_start + MARK_WIDTH + MARK_GAP + len(art[0]) / 2
        left = word_center - sub.cell_len / 2

    padded = Text(" " * max(0, int(left)), no_wrap=True)
    padded.append_text(sub)
    return padded


#: Разделитель между частями подписи.
SEPARATOR = " — "


def subtitle(width: int = 120, *, with_name: bool = False) -> Text:
    """Подпись под логотипом: что это и какая версия.

    Название приложения добавляется, только когда логотип не поместился:
    иначе оно уже нарисовано большими буквами прямо над этой строкой.
    """
    chunks: list[tuple[str, str]] = []
    if with_name:
        chunks.append((APP_NAME, f"bold {ACCENT}"))
    if width >= 52:
        chunks.append((APP_TAGLINE, MUTED))
    chunks.append((f"версия {__version__}", MUTED))

    text = Text(no_wrap=True, overflow="ellipsis")
    for index, (value, style) in enumerate(chunks):
        if index:
            text.append(SEPARATOR, style=MUTED)
        text.append(value, style=style)
    return text


#: Пустые строки вокруг логотипа: одна сверху, чтобы он не упирался в край
#: окна, и одна под подписью, чтобы шапка не срасталась с интерфейсом. Между
#: логотипом и подписью отбивки нет намеренно: они читаются как одно целое.
PADDING_ROWS = 2


def art_height(width: int, *, compact: bool = False) -> int:
    """Сколько строк займёт заголовок без линейки под ним.

    Ноль означает, что логотип в такую ширину не помещается вовсе.
    """
    if compact:
        return 0
    lines = art_lines(width)
    return len(lines) + 1 + PADDING_ROWS if lines else 0


def render(width: int, *, compact: bool = False, tick: int = 0) -> Group:
    """Заголовок, подстраивающийся под ширину окна."""
    width = max(1, width)
    art = None if compact else _art_for(width)

    if art is None:
        return Group(Align.center(subtitle(width, with_name=True), width=width))

    parts: list[object] = [Text()]
    parts.extend(Align.center(line, width=width) for line in art_lines(width, tick))
    parts.append(_place_subtitle(width, art, subtitle(width)))
    parts.append(Text())
    return Group(*parts)


def plain_header(width: int = 120) -> Group:
    """Заголовок для обычной (не полноэкранной) печати.

    Строки уже расставлены по всей ширине окна, поэтому оборачивать результат
    в Align.center не нужно. Раньше именно так и делали, и подпись съезжала:
    Align сдвигает многострочный блок целиком, на ширину самой длинной строки,
    а короткая подпись оставалась прижатой к левому краю логотипа.
    """
    art = _art_for(width)
    if art is None:
        return Group(Align.center(subtitle(width, with_name=True), width=width))
    parts: list[object] = [
        Align.center(line, width=width) for line in art_lines(width)
    ]
    parts.append(_place_subtitle(width, art, subtitle(width)))
    return Group(*parts)
