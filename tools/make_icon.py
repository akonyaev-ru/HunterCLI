# -*- coding: utf-8 -*-
"""Собрать иконку приложения и матрицу логотипа из logo.png.

На выходе два файла:
  icon.ico          — иконка для .exe, сразу во всех нужных размерах;
  huntercli/logo.py — та же картинка матрицей, чтобы рисовать её в консоли
                      без Pillow и без самого PNG рядом с программой.

Про размытие иконки. Windows берёт из .ico размер, ближайший к нужному, и
если точного нет — масштабирует сам, грубо. Отсюда мыло в панели задач и в
рамке окна у программ, куда положили одну картинку 256x256. Поэтому здесь
кладутся все ходовые размеры разом, а промежуточные (20, 40) берутся из
мастер-картинки кратного размера, чтобы края пиксель-арта не поплыли.

Нужен Pillow: python -m pip install Pillow
Запуск: python tools\\make_icon.py
"""

from __future__ import annotations

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from huntercli import force_utf8_output  # noqa: E402

force_utf8_output()

from PIL import Image  # noqa: E402

SOURCE = os.path.join(ROOT, "logo.png")
ICON = os.path.join(ROOT, "icon.ico")
MATRIX_MODULE = os.path.join(ROOT, "huntercli", "logo.py")

#: Логическая сетка пиксель-арта в исходном файле.
COLS, ROWS = 22, 14

#: Мастер-картинка: 768 делится нацело на 16, 24, 32, 48, 64, 96, 128 и 256,
#: поэтому для них уменьшение идёт усреднением по площади, без ряби.
MASTER = 768
CELL = 32                      # 22*32 = 704 по ширине, 14*32 = 448 по высоте

ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

#: Цвета сверху вниз — те же, что у надписи HUNTER CLI в консоли.
FLAME = ["#FF6A4D", "#FF4433", "#FF2A24", "#F0101F", "#D6001C", "#B80019", "#990016"]


def _hex(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def extract() -> list[str]:
    """Прочитать logo.png и вернуть строки из '#' и '.'."""
    image = Image.open(SOURCE).convert("RGBA")
    pixels = image.load()
    width, height = image.size

    def ink(x: int, y: int) -> bool:
        red, green, blue, alpha = pixels[x, y]
        return alpha > 128 and (red + green + blue) / 3 < 128

    columns = [x for x in range(width) if any(ink(x, y) for y in range(height))]
    rows = [y for y in range(height) if any(ink(x, y) for x in range(width))]
    x0, x1, y0, y1 = min(columns), max(columns), min(rows), max(rows)
    cell_w = (x1 - x0 + 1) / COLS
    cell_h = (y1 - y0 + 1) / ROWS

    matrix: list[str] = []
    for row in range(ROWS):
        line = []
        for col in range(COLS):
            # Голосуем по центру клетки: края в исходнике слегка неровные.
            left, right = x0 + cell_w * (col + 0.3), x0 + cell_w * (col + 0.7)
            top, bottom = y0 + cell_h * (row + 0.3), y0 + cell_h * (row + 0.7)
            filled = total = 0
            for y in range(int(top), max(int(bottom), int(top) + 1)):
                for x in range(int(left), max(int(right), int(left) + 1)):
                    total += 1
                    filled += ink(x, y)
            line.append("#" if filled * 2 > total else ".")
        matrix.append("".join(line))
    return matrix


def write_matrix(matrix: list[str]) -> None:
    lines = "\n".join(f'    "{row}",' for row in matrix)
    MATRIX_MODULE_TEXT = f'''"""Логотип в виде матрицы. Файл создаётся tools/make_icon.py — не править.

Каждый символ — логический пиксель: «#» закрашен, «.» пуст. В консоли строки
рисуются полублоками, поэтому две строки матрицы занимают одну строку экрана
и пиксели получаются квадратными.
"""

WIDTH = {COLS}
HEIGHT = {ROWS}

LOGO = (
{lines}
)
'''
    io.open(MATRIX_MODULE, "w", encoding="utf-8", newline="\n").write(MATRIX_MODULE_TEXT)


def render_master(matrix: list[str]) -> Image.Image:
    """Нарисовать логотип крупно: цвет по строкам, фон прозрачный."""
    canvas = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    pixels = canvas.load()
    art_w, art_h = COLS * CELL, ROWS * CELL
    off_x, off_y = (MASTER - art_w) // 2, (MASTER - art_h) // 2

    for row, line in enumerate(matrix):
        red, green, blue = _hex(FLAME[min(row * len(FLAME) // ROWS, len(FLAME) - 1)])
        for col, char in enumerate(line):
            if char != "#":
                continue
            for y in range(off_y + row * CELL, off_y + (row + 1) * CELL):
                for x in range(off_x + col * CELL, off_x + (col + 1) * CELL):
                    pixels[x, y] = (red, green, blue, 255)
    return canvas


def main() -> int:
    if not os.path.exists(SOURCE):
        print(f"Не найден {SOURCE}", file=sys.stderr)
        return 1

    matrix = extract()
    write_matrix(matrix)
    print(f"Матрица: {MATRIX_MODULE} ({COLS}x{ROWS})")

    master = render_master(matrix)
    frames = []
    for size in ICON_SIZES:
        # Кратные размеры уменьшаем усреднением по площади: для пиксель-арта
        # это даёт чистый край. Для некратных берём LANCZOS.
        method = Image.BOX if MASTER % size == 0 else Image.LANCZOS
        frames.append(master.resize((size, size), method))

    frames[-1].save(ICON, format="ICO", sizes=[(s, s) for s in ICON_SIZES],
                    append_images=frames[:-1])
    print(f"Иконка: {ICON} — размеры {', '.join(str(s) for s in ICON_SIZES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
