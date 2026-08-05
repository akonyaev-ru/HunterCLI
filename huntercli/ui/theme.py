"""Палитра и мелкие помощники отрисовки."""

from __future__ import annotations

from rich.theme import Theme

#: Градиент логотипа: от алого сверху к фирменному тёмно-красному снизу.
#: Ориентир — красный HeadHunter #D6001C.
FLAME = [
    "#FF6A4D",
    "#FF4433",
    "#FF2A24",
    "#F0101F",
    "#D6001C",
    "#B80019",
    "#990016",
]

#: Основной красный: заголовки таблиц, активное действие.
ACCENT = "#F5232B"
#: Светлый коралловый: заголовки панелей, значения времени, горячие клавиши.
#: Намеренно светлее и бледнее, чем ERR, — чтобы ошибку было видно сразу.
ACCENT_SOFT = "#FF9E86"
FRAME = "#6E1219"
FRAME_HOT = "#D6001C"
#: Нейтральный серый: на красной теме тёплый серый отдавал бы бурым.
MUTED = "#8A8080"
OK = "#41D97F"
WARN = "#FFB020"
ERR = "#FF5C5C"
COOL = "#5AC8FA"

THEME = Theme(
    {
        "accent": ACCENT,
        "accent.soft": ACCENT_SOFT,
        "muted": MUTED,
        "ok": OK,
        "warn": WARN,
        "err": ERR,
        "cool": COOL,
        "frame": FRAME,
        "frame.hot": FRAME_HOT,
        "key": f"bold {ACCENT_SOFT}",
        "value": "bold white",
        "heading": f"bold {ACCENT}",
    }
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PULSE = "◜◝◞◟"

#: Значки статусов резюме.
DOT_LIVE = "●"
DOT_IDLE = "○"


def spinner(tick: int) -> str:
    return SPINNER[tick % len(SPINNER)]


def human_span(seconds: float | None, *, short: bool = False) -> str:
    """`14523` -> `4 ч 02 м`. Для коротких интервалов — секунды."""
    if seconds is None:
        return "—"
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours >= 24:
        days, hours = divmod(hours, 24)
        return f"{days} д {hours:02d} ч"
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}" if not short else f"{hours} ч {minutes:02d} м"
    if minutes:
        return f"{minutes:02d}:{secs:02d}"
    return f"{secs} с"


def bar(fraction: float, width: int, *, filled: str = "▰", empty: str = "▱") -> str:
    fraction = min(1.0, max(0.0, fraction))
    done = int(round(fraction * width))
    return filled * done + empty * (width - done)


def gradient_bar(fraction: float, width: int) -> str:
    """Полоса прогресса, окрашенная градиентом по мере заполнения."""
    fraction = min(1.0, max(0.0, fraction))
    done = int(round(fraction * width))
    chunks: list[str] = []
    for index in range(width):
        if index < done:
            color = FLAME[min(len(FLAME) - 1, int(index / max(1, width - 1) * (len(FLAME) - 1)))]
            chunks.append(f"[{color}]▰[/]")
        else:
            chunks.append(f"[{MUTED}]▱[/]")
    return "".join(chunks)


def ellipsis(text: str, width: int) -> str:
    if width <= 1 or len(text) <= width:
        return text
    return text[: width - 1] + "…"
