# -*- coding: utf-8 -*-
"""Собрать docs/preview.png — картинку для README.

Дашборд рисуется настоящим кодом с демонстрационными данными, вставляется в
окно на мягком фоне и переводится в PNG.

Почему PNG, а не SVG: в SVG текст остаётся текстом, и каждый браузер рисует
его своим шрифтом. Ширины символов при этом разъезжаются, а блочный логотип
покрывается швами. Растр выглядит одинаково у всех.

Для перевода в PNG нужен безголовый браузер — Edge или Chrome. Если ни одного
нет, рядом останется docs/preview.svg — им можно воспользоваться как есть.

Запуск: python tools\\make_preview.py
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import huntercli.paths as paths
from huntercli import force_utf8_output

force_utf8_output()

#: Конфиг и журнал при генерации картинки трогать нельзя — ни рабочие в
#: %LOCALAPPDATA%, ни оставшиеся рядом с программой от старых версий.
paths.base_dir = lambda: os.path.join(ROOT, "tools")
paths.state_dir = lambda: os.path.join(ROOT, "tools")

from rich.console import Console

from huntercli import APP_NAME
from huntercli.engine import Phase, Snapshot
from huntercli.hh import Resume
from huntercli.logbus import LogBus
from huntercli.ui import banner
from huntercli.ui.dashboard import Dashboard, TabInfo
from huntercli.ui.theme import THEME

#: Размер терминала в символах. Подобран так, чтобы поместился полный логотип
#: (74 колонки) и осталось место на таблицу с журналом.
#: Размер картинки фиксирован. Ширина и высота окна из него вычитаются, а не
#: наоборот: так размер не «уплывает» при правках интерфейса.
CANVAS_W, CANVAS_H = 1600, 1000

#: Размер терминала подобран под этот холст. Ограничения снизу: от 103 колонок
#: рядом с надписью помещается значок, от 32 строк панель статуса показывает
#: обратный отсчёт, а названия резюме перестают обрезаться. Ещё одна строка
#: уходит под полосу вкладок — на картинке аккаунта два. Больше брать некуда:
#: окно должно оставить поля на холсте.
COLUMNS, ROWS = 112, 33

CORNER = 12

#: Запас снизу. Rich оставляет под содержимым всего 8 px (сверху — 40 px под
#: заголовок), из-за чего строка горячих клавиш прижималась к краю окна.
BOTTOM_PAD = 26

OUTPUT_PNG = os.path.join(ROOT, "docs", "preview.png")
OUTPUT_SVG = os.path.join(ROOT, "docs", "preview.svg")

#: Шрифт, под который считается вёрстка. Cascadia Mono идёт с Windows, и у неё
#: блоки (█), буквы и символы рамок имеют одинаковую ширину — 0.5859 от кегля.
#: Если подставить сюда шрифт с другим шагом, Rich растянет строки до «своей»
#: ширины и по логотипу пойдут швы.
FONT_STACK = '"Cascadia Mono", "Cascadia Code", Consolas, monospace'
FONT_ASPECT = 0.5859

#: Чем переводить SVG в PNG. Edge стоит на любой Windows, поэтому он первый,
#: Chrome — запасной. Перебор нужен не для красоты: у Edge 151 ключ
#: --screenshot молча ничего не делает (проверено 2026-08-24 — код возврата
#: нулевой, stderr пустой, файла нет), а Chrome ту же команду выполняет.
BROWSER_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

#: Шаблон без «хрома» Rich: рамку рисуем свою, чтобы окно выглядело как
#: настоящее приложение Windows, а не как терминал macOS с тремя кружками.
BARE_SVG = """\
<svg class="rich-terminal" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
<style>
.{unique_id}-matrix {{
    font-family: FONT_STACK_PLACEHOLDER;
    font-size: {char_height}px;
    line-height: {line_height}px;
}}
{styles}
</style>
<defs>
<clipPath id="{unique_id}-clip-terminal">
  <rect x="0" y="0" width="{terminal_width}" height="{terminal_height}" />
</clipPath>
{lines}
</defs>
<g transform="translate({terminal_x}, {terminal_y})" clip-path="url(#{unique_id}-clip-terminal)">
{backgrounds}
<g class="{unique_id}-matrix">
{matrix}
</g>
</g>
</svg>
"""


def build_log() -> LogBus:
    """Журнал как при двух аккаунтах: записи подписаны их владельцами.

    Без подписи остаётся только строка запуска — она общая для программы,
    ровно так это выглядит и в жизни.
    """
    log = LogBus(to_file=False)
    log.info(f"{APP_NAME} запущен")
    log.step("Алексей К. · Синхронизация: резюме — 3, под автопилотом — 2")
    log.ok("Алексей К. · «Ведущий юрист по корпоративному праву» поднято в поиске")
    log.ok("Мария С. · «Финансовый аналитик» поднято в поиске")
    log.step("Мария С. · Токен доступа обновлён автоматически")

    # Все записи родились в одну секунду, и на картинке колонка времени
    # выглядела бы бесполезной: пять одинаковых отметок подряд. Разносим их
    # по правдоподобному ходу событий — запуск, синхронизация, два поднятия
    # с паузой, продление токена.
    offsets = (0, 4, 9, 71, 96)
    start = time.time() - offsets[-1]
    entries = log._entries  # приватное поле: это генератор картинки, а не код программы
    spaced = [replace(entry, at=start + shift)
              for entry, shift in zip(entries, offsets)]
    entries.clear()
    entries.extend(spaced)
    return log


def build_tabs() -> list[TabInfo]:
    """Две вкладки: открытая ждёт своего времени, соседняя как раз поднимает."""
    return [
        TabInfo("Алексей К.", Phase.WAITING),
        TabInfo("Мария С.", Phase.BUMPING),
    ]


def build_snapshot() -> Snapshot:
    now = datetime.now(timezone.utc)
    return Snapshot(
        phase=Phase.WAITING,
        detail="ждём разрешённого времени",
        account="Алексей К.",
        started_at=time.time() - 9_240,
        session_bumps=2,
        total_bumps=148,
        last_bump_at=time.time() - 512,
        last_sync_at=time.time() - 24,
        next_action_at=time.time() + 11_930,
        wait_span=14_400,
        token_seconds_left=12.6 * 86_400,
        resumes=[
            Resume(id="1", title="Ведущий юрист по корпоративному праву",
                   next_publish_at=now + timedelta(hours=3, minutes=18),
                   total_views=1204, new_views=12,
                   planned_at=time.time() + 11_930),
            Resume(id="2", title="Руководитель юридического департамента",
                   next_publish_at=now + timedelta(hours=3, minutes=21),
                   total_views=386, new_views=4,
                   planned_at=time.time() + 12_100),
            Resume(id="3", title="Юрист по интеллектуальной собственности",
                   total_views=57, planned_at=None),
        ],
    )


def render_terminal() -> tuple[str, float, float]:
    """Отрисовать дашборд и вернуть SVG вместе с его размерами."""
    console = Console(
        theme=THEME, highlight=False, file=io.StringIO(),
        width=COLUMNS, height=ROWS, force_terminal=True,
        color_system="truecolor", record=True,
    )
    board = Dashboard(console, build_log())
    # Кадр заведомо вне моргания: на статичной картинке глаз должен быть открыт.
    board.tick = 20
    assert not banner.blinking(board.tick), "выбран кадр с закрытым глазом"
    console.print(board.render(build_snapshot(), build_tabs(), 0))

    svg = console.export_svg(
        title="",
        code_format=BARE_SVG.replace("FONT_STACK_PLACEHOLDER", FONT_STACK),
        unique_id="hunter",
        font_aspect_ratio=FONT_ASPECT,
    )

    # Подстраховка на случай, если картинку всё же смотрят как SVG чужим
    # шрифтом: растягивать лучше сами символы, чем промежутки между ними —
    # иначе блоки логотипа расходятся швами.
    svg = svg.replace("textLength=", 'lengthAdjust="spacingAndGlyphs" textLength=')

    box = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    if not box:
        raise RuntimeError("не удалось прочитать размеры отрисованного терминала")
    return svg, float(box.group(1)), float(box.group(2))


def canvas_size(inner_w: float, inner_h: float) -> tuple[int, int]:
    """Размер картинки. Фиксирован, но проверяем, что окно в него влезает."""
    win_w, win_h = inner_w, inner_h + BOTTOM_PAD
    if win_w > CANVAS_W - 80 or win_h > CANVAS_H - 60:
        raise RuntimeError(
            f"окно {win_w:.0f}x{win_h:.0f} не помещается в холст "
            f"{CANVAS_W}x{CANVAS_H}: уменьшите COLUMNS/ROWS"
        )
    return CANVAS_W, CANVAS_H


def silk(canvas_w: float, canvas_h: float) -> str:
    """Шёлковые волны на фоне.

    Три мягкие ленты: светлый разлив сверху, тёмная волна снизу справа и
    светлая полоса поверх неё. Всё сильно размыто, поэтому границы не читаются
    как линии — получается перетекание, а не рисунок.
    """
    def sweep(start: float, mid_a: float, mid_b: float, end: float) -> str:
        """Кривая через весь кадр слева направо, с заходом за края."""
        return (
            f"M {canvas_w * -0.12:.0f} {canvas_h * start:.0f} "
            f"C {canvas_w * 0.22:.0f} {canvas_h * mid_a:.0f}, "
            f"{canvas_w * 0.58:.0f} {canvas_h * mid_b:.0f}, "
            f"{canvas_w * 1.12:.0f} {canvas_h * end:.0f}"
        )

    #: (кривая, цвет, толщина в долях высоты, прозрачность)
    #: Полосы держатся подальше от кромок окна. Иначе окно перекрывает край
    #: светлой полосы, обрез повторяет её скруглённый угол — и на фоне
    #: проступает контур «второго окна».
    bands = [
        (sweep(0.34, 0.10, 0.24, -0.06), "#ffffff", 0.20, 0.85),
        (sweep(0.12, -0.08, 0.04, -0.20), "#bcbcc8", 0.16, 0.40),
        (sweep(1.16, 0.92, 1.10, 0.72), "#ffffff", 0.17, 0.85),
        (sweep(1.34, 1.12, 1.30, 0.94), "#b6b6c4", 0.20, 0.50),
    ]
    return "\n".join(
        f'    <path d="{path}" stroke="{color}" stroke-width="{canvas_h * thick:.0f}" '
        f'stroke-linecap="round" fill="none" opacity="{alpha}"/>'
        for path, color, thick, alpha in bands
    )


def compose(terminal: str, inner_w: float, inner_h: float) -> str:
    """Обернуть терминал в окно и положить на фон."""
    win_w, win_h = inner_w, inner_h + BOTTOM_PAD
    canvas_w, canvas_h = canvas_size(inner_w, inner_h)
    win_x, win_y = (canvas_w - win_w) / 2, (canvas_h - win_h) / 2
    waves = silk(canvas_w, canvas_h)

    # Заголовок окна занимает верхнюю полосу: Rich уже оставил под неё отступ
    # (padding_top), поэтому текст дашборда под неё не залезает.
    bar_h = 40
    buttons = ""
    for index, glyph in enumerate(("─", "□", "✕")):
        cx = win_w - 46 * (3 - index) + 23
        buttons += (
            f'<text x="{cx:.0f}" y="{bar_h / 2 + 5:.0f}" text-anchor="middle" '
            f'font-family="Segoe UI, Arial" font-size="14" fill="#b9b0ad">{glyph}</text>'
        )

    # Терминал вставляем в его собственном размере: если растянуть его на всю
    # высоту окна вместе с запасом снизу, текст поедет по вертикали.
    terminal_body = terminal.replace(
        '<svg class="rich-terminal"',
        f'<svg class="rich-terminal" x="{win_x}" y="{win_y}" '
        f'width="{inner_w}" height="{inner_h}"',
        1,
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w:.0f}" \
height="{canvas_h:.0f}" viewBox="0 0 {canvas_w:.0f} {canvas_h:.0f}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#fcfcfd"/>
      <stop offset="42%" stop-color="#e9e9ed"/>
      <stop offset="100%" stop-color="#c9c9d2"/>
    </linearGradient>
    <!-- Размытие делает из трёх фигур единое перетекание. Без него это
         читалось бы как аппликация из кусков бумаги. -->
    <filter id="silk" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="78"/>
    </filter>
    <!-- Смещение должно быть заметно меньше размытия. Иначе тень повторяет
         скруглённый силуэт окна со сдвигом, и он читается как контур
         «второго окна» под настоящим. -->
    <filter id="shadow" x="-25%" y="-25%" width="150%" height="160%">
      <feDropShadow dx="0" dy="10" stdDeviation="44" flood-color="#1a1214"
                    flood-opacity="0.32"/>
    </filter>
    <clipPath id="window">
      <rect x="{win_x}" y="{win_y}" width="{win_w:.0f}" height="{win_h:.0f}" rx="{CORNER}"/>
    </clipPath>
  </defs>

  <rect width="100%" height="100%" fill="url(#bg)"/>
  <g filter="url(#silk)">
{waves}
  </g>

  <rect x="{win_x}" y="{win_y}" width="{win_w:.0f}" height="{win_h:.0f}" rx="{CORNER}"
        fill="#0f0d0d" filter="url(#shadow)"/>

  <g clip-path="url(#window)">
    {terminal_body}
    <g transform="translate({win_x}, {win_y})">
      <rect x="0" y="0" width="{win_w:.0f}" height="{bar_h}" fill="#1c1717"/>
      <rect x="0" y="{bar_h - 1}" width="{win_w:.0f}" height="1" fill="#2e2626"/>
      <text x="18" y="{bar_h / 2 + 5:.0f}" font-family="Segoe UI, Arial" font-size="14"
            fill="#d8cdc9">{APP_NAME}</text>
      {buttons}
    </g>
  </g>
  <rect x="{win_x}" y="{win_y}" width="{win_w:.0f}" height="{win_h:.0f}" rx="{CORNER}"
        fill="none" stroke="#00000022"/>
</svg>
"""


def rasterize(width: int, height: int) -> bool:
    """Перевести готовый SVG в PNG безголовым браузером.

    Браузеры перебираются по очереди: молчаливый отказ (нулевой код возврата
    и никакого файла) — тоже отказ, просто его не на что списать.
    """
    browsers = [path for path in BROWSER_PATHS if os.path.exists(path)]
    if not browsers:
        print("Ни Edge, ни Chrome не найдены — оставляю только SVG", file=sys.stderr)
        return False

    with tempfile.TemporaryDirectory() as work:
        page = os.path.join(work, "page.html")
        shot = os.path.join(work, "shot.png")
        shutil.copy(OUTPUT_SVG, os.path.join(work, "preview.svg"))
        io.open(page, "w", encoding="utf-8").write(
            '<!doctype html><meta charset="utf-8">'
            '<body style="margin:0"><img src="preview.svg" '
            f'style="display:block;width:{width}px;height:{height}px"></body>'
        )
        for browser in browsers:
            if os.path.exists(shot):
                os.remove(shot)
            result = subprocess.run(
                [browser, "--headless", "--disable-gpu", f"--screenshot={shot}",
                 f"--window-size={width},{height}", "--default-background-color=FFFFFFFF",
                 "--hide-scrollbars", f"file:///{page.replace(os.sep, '/')}"],
                capture_output=True, timeout=180,
            )
            if os.path.exists(shot):
                shutil.copy(shot, OUTPUT_PNG)
                return True
            print(f"{os.path.basename(browser)} картинку не отдал "
                  f"(код {result.returncode}) — пробую следующий", file=sys.stderr)
    return False


def main() -> int:
    terminal, inner_w, inner_h = render_terminal()
    svg = compose(terminal, inner_w, inner_h)

    os.makedirs(os.path.dirname(OUTPUT_SVG), exist_ok=True)
    io.open(OUTPUT_SVG, "w", encoding="utf-8", newline="\n").write(svg)

    width, height = canvas_size(inner_w, inner_h)
    if not rasterize(width, height):
        return 1

    print(f"Готово: {OUTPUT_PNG} — {width}x{height}, "
          f"{os.path.getsize(OUTPUT_PNG) // 1024} КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
