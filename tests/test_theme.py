# -*- coding: utf-8 -*-
"""Логотип и палитра.

Палитра красная целиком, поэтому легко случайно сделать сообщение об ошибке
неотличимым от обычного оформления. Здесь это проверяется числом, а не на глаз.
"""

from __future__ import annotations

from harness import Report, sandbox

sandbox()

from huntercli import APP_NAME
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

    report.section("Под логотипом — только имя владельца")
    wide = banner.subtitle("Иван И.", 120).plain
    report.check("названия нет — оно уже нарисовано выше", APP_NAME not in wide, f"-> {wide!r}")
    report.check("версии нет", "v2" not in wide and "2.0.0" not in wide)
    report.check("описания нет", "автопилот" not in wide and "autopilot" not in wide)
    report.check("имя владельца на месте", wide.strip() == "Иван И.", f"-> {wide!r}")

    empty = banner.subtitle("", 120).plain
    report.check("до первой синхронизации подпись пустая", not empty.strip(), f"-> {empty!r}")

    narrow = banner.subtitle("Иван И.", 40, with_name=True).plain
    report.check("без логотипа название возвращается", APP_NAME in narrow, f"-> {narrow!r}")
    lonely = banner.subtitle("", 40, with_name=True).plain
    report.check("без логотипа и без имени — только название",
                 lonely.strip() == APP_NAME, f"-> {lonely!r}")

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
