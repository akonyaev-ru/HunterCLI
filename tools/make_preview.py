# -*- coding: utf-8 -*-
"""Собрать docs/preview.png — картинку для README.

Дашборд рисуется настоящим кодом с демонстрационными данными, вставляется в
окно на мягком фоне (как скриншот у Umbra) и переводится в PNG.

Почему PNG, а не SVG: в SVG текст остаётся текстом, и каждый браузер рисует
его своим шрифтом. Ширины символов при этом разъезжаются, а блочный логотип
покрывается швами. Растр выглядит одинаково у всех.

Для перевода в PNG нужен Microsoft Edge (есть на любой Windows). Если его нет,
рядом останется docs/preview.svg — им можно воспользоваться как есть.

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
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import huntercli.paths as paths
from huntercli import force_utf8_output

force_utf8_output()

#: Конфиг и журнал при генерации картинки трогать нельзя.
paths.base_dir = lambda: os.path.join(ROOT, "tools")

from rich.console import Console

from huntercli import APP_NAME
from huntercli.engine import Phase, Snapshot
from huntercli.hh import Resume
from huntercli.logbus import LogBus
from huntercli.ui.dashboard import Dashboard
from huntercli.ui.theme import THEME

#: Размер терминала в символах. Подобран так, чтобы поместился полный логотип
#: (74 колонки) и осталось место на таблицу с журналом.
#: Высота не меньше 30 строк: ниже этого дашборд прячет логотип и рисует
#: сжатую шапку — для картинки в README нужен полный вид.
COLUMNS, ROWS = 112, 32

#: Поля вокруг окна и скругление — на глаз, как у скриншота Umbra.
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 96, 80, 112
CORNER = 12

OUTPUT_PNG = os.path.join(ROOT, "docs", "preview.png")
OUTPUT_SVG = os.path.join(ROOT, "docs", "preview.svg")

#: Шрифт, под который считается вёрстка. Cascadia Mono идёт с Windows, и у неё
#: блоки (█), буквы и символы рамок имеют одинаковую ширину — 0.5859 от кегля.
#: Если подставить сюда шрифт с другим шагом, Rich растянет строки до «своей»
#: ширины и по логотипу пойдут швы.
FONT_STACK = '"Cascadia Mono", "Cascadia Code", Consolas, monospace'
FONT_ASPECT = 0.5859

EDGE_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
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
    log = LogBus(to_file=False)
    log.info(f"{APP_NAME} запущен")
    log.step("Синхронизация: резюме — 3, под автопилотом — 2")
    log.ok("«Ведущий юрист по корпоративному праву» поднято в поиске")
    log.ok("«Руководитель юридического департамента» поднято в поиске")
    log.step("Токен доступа обновлён автоматически")
    return log


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
    board.tick = 3
    console.print(board.render(build_snapshot()))

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


def compose(terminal: str, inner_w: float, inner_h: float) -> str:
    """Обернуть терминал в окно и положить на фон."""
    win_w, win_h = inner_w, inner_h
    canvas_w = win_w + MARGIN_X * 2
    canvas_h = win_h + MARGIN_TOP + MARGIN_BOTTOM
    win_x, win_y = MARGIN_X, MARGIN_TOP

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

    terminal_body = terminal.replace(
        '<svg class="rich-terminal"',
        f'<svg class="rich-terminal" x="{win_x}" y="{win_y}" '
        f'width="{win_w}" height="{win_h}"',
        1,
    )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_w:.0f}" \
height="{canvas_h:.0f}" viewBox="0 0 {canvas_w:.0f} {canvas_h:.0f}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#fdf6f4"/>
      <stop offset="45%" stop-color="#fbe6e0"/>
      <stop offset="100%" stop-color="#f3c9c0"/>
    </linearGradient>
    <linearGradient id="sheen" x1="0%" y1="100%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.55"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="150%">
      <feDropShadow dx="0" dy="26" stdDeviation="30" flood-color="#7a1a12"
                    flood-opacity="0.28"/>
    </filter>
    <clipPath id="window">
      <rect x="{win_x}" y="{win_y}" width="{win_w:.0f}" height="{win_h:.0f}" rx="{CORNER}"/>
    </clipPath>
  </defs>

  <rect width="100%" height="100%" fill="url(#bg)"/>
  <path d="M0 {canvas_h * 0.78:.0f} Q {canvas_w * 0.3:.0f} {canvas_h * 0.62:.0f} \
{canvas_w * 0.58:.0f} {canvas_h * 0.74:.0f} T {canvas_w:.0f} {canvas_h * 0.66:.0f} \
L {canvas_w:.0f} {canvas_h:.0f} L 0 {canvas_h:.0f} Z" fill="url(#sheen)"/>

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
    """Перевести готовый SVG в PNG через безголовый Edge."""
    browser = next((path for path in EDGE_PATHS if os.path.exists(path)), None)
    if not browser:
        print("Microsoft Edge не найден — оставляю только SVG", file=sys.stderr)
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
        result = subprocess.run(
            [browser, "--headless", "--disable-gpu", f"--screenshot={shot}",
             f"--window-size={width},{height}", "--default-background-color=FFFFFFFF",
             "--hide-scrollbars", f"file:///{page.replace(os.sep, '/')}"],
            capture_output=True, timeout=180,
        )
        if not os.path.exists(shot):
            print(f"Edge не отдал картинку: {result.stderr[:200]!r}", file=sys.stderr)
            return False
        shutil.copy(shot, OUTPUT_PNG)
    return True


def main() -> int:
    terminal, inner_w, inner_h = render_terminal()
    svg = compose(terminal, inner_w, inner_h)

    os.makedirs(os.path.dirname(OUTPUT_SVG), exist_ok=True)
    io.open(OUTPUT_SVG, "w", encoding="utf-8", newline="\n").write(svg)

    width = int(inner_w + MARGIN_X * 2)
    height = int(inner_h + MARGIN_TOP + MARGIN_BOTTOM)
    if not rasterize(width, height):
        return 1

    print(f"Готово: {OUTPUT_PNG} — {width}x{height}, "
          f"{os.path.getsize(OUTPUT_PNG) // 1024} КБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
