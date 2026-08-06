# -*- coding: utf-8 -*-
"""Логотип и палитра.

Палитра красная целиком, поэтому легко случайно сделать сообщение об ошибке
неотличимым от обычного оформления. Здесь это проверяется числом, а не на глаз.
"""

from __future__ import annotations

import io

from harness import Report, sandbox

sandbox()

from rich.console import Console

from huntercli import APP_NAME, APP_TAGLINE, AUTHOR, __version__
from huntercli import logo
from huntercli.ui import banner, theme


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _distance(first: str, second: str) -> float:
    """Грубое расстояние между цветами в RGB — хватает, чтобы поймать слипание."""
    a, b = _rgb(first), _rgb(second)
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _luminance(value: str) -> float:
    red, green, blue = _rgb(value)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def run() -> bool:
    report = Report("Логотип и палитра")

    report.section("Логотип собран ровно")
    for name, art in (("HUNTER CLI", banner.BIG), ("HUNTER", banner.MID)):
        widths = {len(line) for line in art}
        report.check(f"«{name}»: все строки одной длины", len(widths) == 1, f"-> {widths}")
        report.check(f"«{name}»: шесть строк", len(art) == 6, f"-> {len(art)}")
    report.check("полный логотип шире короткого", banner.BIG_WIDTH > banner.MID_WIDTH,
                 f"-> {banner.BIG_WIDTH} и {banner.MID_WIDTH}")

    report.section("Логотип выбирается по ширине окна")
    report.check("широкое окно -> HUNTER CLI", banner._art_for(120) is banner.BIG)
    report.check("среднее окно -> HUNTER", banner._art_for(60) is banner.MID)
    report.check("узкое окно -> без логотипа", banner._art_for(40) is None)
    report.check("на границе полного логотипа не рвётся",
                 banner._art_for(banner.BIG_WIDTH + 4) is banner.BIG)
    report.check("на границе короткого не рвётся",
                 banner._art_for(banner.MIN_ART_WIDTH) is banner.MID)

    report.section("Подпись под логотипом: описание и версия")
    wide = banner.subtitle(120).plain
    report.check("описание на месте", APP_TAGLINE in wide, f"-> {wide!r}")
    report.check("версия на месте", f"версия {__version__}" in wide)
    report.check("автора в подписи нет", AUTHOR not in wide)
    report.check("названия нет — оно уже нарисовано выше", APP_NAME not in wide)
    report.check("разделитель — тире, не точка", "—" in wide and "·" not in wide)
    report.check("вокруг разделителя по одному пробелу", " — " in wide and "  —" not in wide)

    lonely = banner.subtitle(40, with_name=True).plain
    report.check("без логотипа название возвращается", APP_NAME in lonely, f"-> {lonely!r}")

    report.check("в подписи только символы, которые есть в шрифтах терминала",
                 all(ord(ch) < 0x2500 or ch == "—" for ch in wide),
                 f"-> {sorted({ch for ch in wide if ord(ch) >= 0x2500})}")

    report.section("Подпись стоит под надписью, а не съезжает")
    # Значок слева тянет оптический центр на себя, поэтому подпись считается
    # от надписи. Округление должно совпадать с Align.center, иначе строка
    # уезжает на столбец — на неподвижном экране это заметно.
    for width in (84, 100, 110, 120, 132, 140, 160):
        art = banner._art_for(width)
        sub = banner.subtitle(width)
        placed = banner._place_subtitle(width, art, sub)
        left = len(placed.plain) - len(placed.plain.lstrip(" "))
        if banner._shows_mark(width, art):
            word_start = ((width - banner.FULL_WIDTH) // 2
                          + banner.MARK_WIDTH + banner.MARK_GAP)
        else:
            word_start = (width - len(art[0])) // 2
        offset = (left + sub.cell_len / 2) - (word_start + len(art[0]) / 2)
        report.check(f"{width}: подпись по центру надписи", abs(offset) <= 0.5,
                     f"-> сдвиг {offset:+.1f}")

    report.section("Отбивки вокруг логотипа")
    output = io.StringIO()
    console = Console(file=output, width=120, force_terminal=False, color_system=None)
    console.print(banner.render(120, compact=False, tick=0))
    rows = [line.rstrip() for line in output.getvalue().splitlines()]
    filled = [index for index, row in enumerate(rows) if row]
    report.check("шапка начинается с пустой строки: логотип не впритык к краю",
                 rows and rows[0] == "", f"-> {rows[0][:30]!r}" if rows else "пусто")
    report.check("под подписью пустая строка: шапка не срастается с интерфейсом",
                 rows and rows[-1] == "")
    report.check("между логотипом и подписью пустых строк нет",
                 filled == list(range(filled[0], filled[-1] + 1)),
                 f"-> занятые строки {filled}")
    report.check("высота шапки совпадает с той, что закладывает дашборд",
                 len(rows) == banner.art_height(120),
                 f"-> {len(rows)} против {banner.art_height(120)}")

    report.section("Заголовок на экранах приветствия и входа")
    # Эти экраны печатаются один раз, через plain_header. Раньше подпись там
    # прижималась к левому краю логотипа: строки отдавались невыровненными, а
    # внешний Align.center сдвигал блок целиком.
    output = io.StringIO()
    console = Console(file=output, width=120, force_terminal=False, color_system=None)
    console.print(banner.plain_header(120))
    printed = [line.rstrip() for line in output.getvalue().splitlines() if line.strip()]
    word_row = next(row for row in printed if "██╗" in row)
    word_start, word_end = word_row.index("██╗"), len(word_row)
    sub_row = printed[-1]
    sub_start, sub_end = len(sub_row) - len(sub_row.lstrip(" ")), len(sub_row)
    shift = (sub_start + sub_end) / 2 - (word_start + word_end) / 2
    report.check("подпись по центру надписи", abs(shift) <= 0.5, f"-> сдвиг {shift:+.1f}")
    report.check("глаз на этих экранах открыт",
                 all(mark.plain == open_mark.plain for mark, open_mark
                     in zip(banner.mark_lines(), banner.mark_lines(20))))

    report.section("Значок: размер и моргание")
    report.check("значок высотой с надпись",
                 len(banner.mark_lines()) == len(banner.BIG),
                 f"-> {len(banner.mark_lines())} и {len(banner.BIG)}")
    report.check("ширина как у иконки: глаз в консоли и на значке одинаковый",
                 logo.WIDTH == 22, f"-> {logo.WIDTH}")
    report.check("все кадры моргания того же размера",
                 all(len(f) == len(logo.LOGO) and all(len(r) == logo.WIDTH for r in f)
                     for f in logo.BLINK))
    report.check("кадров моргания несколько", len(logo.BLINK) >= 3,
                 f"-> {len(logo.BLINK)}")

    def ink_rows(frame):
        """Номера строк, где глаз (без украшений) закрашен."""
        return [i for i, row in enumerate(frame[:10]) if "#" in row]

    open_rows = ink_rows(logo.LOGO)
    shut_rows = ink_rows(logo.BLINK[len(logo.BLINK) // 2])
    report.check("в середине моргания глаз сомкнут в узкую щель",
                 len(shut_rows) < len(open_rows) / 3,
                 f"-> строк {len(shut_rows)} против {len(open_rows)}")
    report.check("щель приходится на середину глаза, а не на край",
                 min(open_rows) < shut_rows[0] < max(open_rows),
                 f"-> щель на строке {shut_rows[0]}, глаз {min(open_rows)}..{max(open_rows)}")

    half = ink_rows(logo.BLINK[0])
    report.check("веки идут навстречу: сначала щель шире, потом уже",
                 len(half) > len(shut_rows), f"-> {len(half)} и {len(shut_rows)}")
    report.check("моргание симметрично: последний кадр как первый",
                 logo.BLINK[0] == logo.BLINK[-1])

    def edges(frame) -> tuple[int, int]:
        """Самая верхняя и самая нижняя закрашенные строки кадра."""
        rows = [index for index, row in enumerate(frame) if "#" in row]
        return (min(rows), max(rows)) if rows else (-1, -1)

    open_top, open_bottom = edges(logo.LOGO)
    report.check("верхнее веко опускается",
                 all(edges(f)[0] > open_top for f in logo.BLINK),
                 f"-> {[edges(f)[0] for f in logo.BLINK]} против {open_top}")
    # Точки и полоска под глазом — нижние ресницы, а не отдельное украшение.
    # Пока они стояли на месте, моргание выглядело односторонним.
    report.check("нижнее веко поднимается навстречу",
                 all(edges(f)[1] < open_bottom for f in logo.BLINK),
                 f"-> {[edges(f)[1] for f in logo.BLINK]} против {open_bottom}")

    open_frames = [t for t in range(banner.BLINK_PERIOD) if not banner.blinking(t)]
    report.check("моргание редкое, глаз почти всегда открыт",
                 len(open_frames) / banner.BLINK_PERIOD > 0.95,
                 f"-> открыт {len(open_frames)} кадров из {banner.BLINK_PERIOD}")
    report.check("период около десяти секунд при восьми кадрах в секунду",
                 9 <= banner.BLINK_PERIOD / 8 <= 11,
                 f"-> {banner.BLINK_PERIOD / 8:.1f} с")
    report.check("кадры моргания идут подряд",
                 all(banner.blinking(banner.BLINK_START + t)
                     for t in range(banner.BLINK_FRAMES)))
    report.check("значок меняется в кадре моргания",
                 banner.mark_lines(banner.BLINK_START)[2].plain
                 != banner.mark_lines(20)[2].plain)

    # Экран приветствия и экран входа рисуются один раз и счётчика кадров не
    # ведут — им достаётся нулевой кадр. Пока моргание стояло в начале
    # периода, глаз на них был закрыт всегда.
    report.check("нулевой кадр — открытый глаз, а не моргание",
                 not banner.blinking(0) and banner._frame(0) == logo.LOGO)
    report.check("моргание всё же случается внутри периода",
                 any(banner.blinking(t) for t in range(banner.BLINK_PERIOD)))

    report.section("Палитра красная")
    for name in ("ACCENT", "ACCENT_SOFT", "FRAME", "FRAME_HOT", "ERR", "MUTED"):
        value = getattr(theme, name)
        report.check(f"{name} — корректный цвет", len(value) == 7 and value.startswith("#"),
                     f"-> {value}")
    red, green, blue = _rgb(theme.ACCENT)
    report.check("основной акцент действительно красный", red > 200 and green < 90 and blue < 90,
                 f"-> {theme.ACCENT}")
    report.check("градиент заканчивается тёмно-красным",
                 _luminance(theme.FLAME[-1]) < _luminance(theme.FLAME[0]),
                 f"-> {theme.FLAME[0]} → {theme.FLAME[-1]}")
    report.check("градиент идёт от светлого к тёмному без скачков вверх",
                 all(_luminance(theme.FLAME[i]) >= _luminance(theme.FLAME[i + 1])
                     for i in range(len(theme.FLAME) - 1)),
                 f"-> {[round(_luminance(c)) for c in theme.FLAME]}")

    report.section("Ошибку видно на красном фоне")
    report.check("ERR отличим от основного акцента", _distance(theme.ERR, theme.ACCENT) > 40,
                 f"-> {_distance(theme.ERR, theme.ACCENT):.0f}")
    report.check("ERR отличим от светлого акцента", _distance(theme.ERR, theme.ACCENT_SOFT) > 40,
                 f"-> {_distance(theme.ERR, theme.ACCENT_SOFT):.0f}")
    report.check("ERR отличим от рамки", _distance(theme.ERR, theme.FRAME) > 60,
                 f"-> {_distance(theme.ERR, theme.FRAME):.0f}")
    report.check("успех остаётся зелёным", _rgb(theme.OK)[1] > _rgb(theme.OK)[0])
    report.check("предупреждение остаётся жёлтым",
                 _rgb(theme.WARN)[0] > 200 and _rgb(theme.WARN)[1] > 120)
    report.check("серый нейтральный, без бурого",
                 max(_rgb(theme.MUTED)) - min(_rgb(theme.MUTED)) < 30, f"-> {theme.MUTED}")

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
