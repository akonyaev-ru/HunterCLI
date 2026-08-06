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

#: Логическая сетка пиксель-арта в исходном файле. Для иконки берём её как
#: есть: чем больше клеток, тем чище край при уменьшении.
COLS, ROWS = 22, 14

#: Сетка для консоли: 12 строк полублоками дают 6 строк экрана, ровно высоту
#: надписи HUNTER CLI. Ширина оставлена как в иконке, чтобы глаз в консоли и
#: глаз на значке выглядели одинаково: сжимаем только по вертикали.
CONSOLE_COLS, CONSOLE_ROWS = 22, 12

#: Строки матрицы, занятые самим глазом. Ниже идут мелкие украшения, которые
#: при моргании остаются на месте.
EYE_ROWS = 10

#: Насколько глаз прикрыт в каждом кадре моргания: 0 — открыт, 1 — закрыт.
#: Веки сходятся к середине и расходятся обратно. При восьми кадрах в секунду
#: это примерно треть секунды, как настоящее моргание.
BLINK_STAGES = (0.5, 1.0, 0.5)

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


def extract(cols: int, rows: int) -> list[str]:
    """Прочитать logo.png и вернуть строки из '#' и '.' в заданной сетке."""
    image = Image.open(SOURCE).convert("RGBA")
    pixels = image.load()
    width, height = image.size

    def ink(x: int, y: int) -> bool:
        red, green, blue, alpha = pixels[x, y]
        return alpha > 128 and (red + green + blue) / 3 < 128

    columns = [x for x in range(width) if any(ink(x, y) for y in range(height))]
    inked = [y for y in range(height) if any(ink(x, y) for x in range(width))]
    x0, x1, y0, y1 = min(columns), max(columns), min(inked), max(inked)
    cell_w = (x1 - x0 + 1) / cols
    cell_h = (y1 - y0 + 1) / rows

    matrix: list[str] = []
    for row in range(rows):
        line = []
        for col in range(cols):
            # Считаем долю закрашенного по всей клетке: при пересъёмке в
            # другую сетку выборка по центру теряла бы тонкие детали.
            left, right = x0 + cell_w * col, x0 + cell_w * (col + 1)
            top, bottom = y0 + cell_h * row, y0 + cell_h * (row + 1)
            filled = total = 0
            for y in range(int(top), max(int(bottom), int(top) + 1)):
                for x in range(int(left), max(int(right), int(left) + 1)):
                    total += 1
                    filled += ink(x, y)
            line.append("#" if total and filled * 2 >= total else ".")
        matrix.append("".join(line))
    return matrix


def blink_frame(matrix: list[str], closed: float) -> list[str]:
    """Кадр моргания: оба века сходятся к середине глаза.

    Глаз сжимается по вертикали к своей середине, а не схлопывается в линию
    по столбцам: верхнее веко идёт вниз, нижнее навстречу ему вверх. Именно
    это читается как моргание. Украшения под глазом не двигаются.
    """
    cols = len(matrix[0])
    eye, tail = matrix[:EYE_ROWS], matrix[EYE_ROWS:]
    filled = [(row, col) for row in range(EYE_ROWS) for col in range(cols)
              if eye[row][col] == "#"]
    if not filled:
        return list(matrix)

    middle = (min(r for r, _ in filled) + max(r for r, _ in filled)) / 2
    scale = 1.0 - closed
    grid = [["."] * cols for _ in range(EYE_ROWS)]
    for row, col in filled:
        target = int(round(middle + (row - middle) * scale))
        if 0 <= target < EYE_ROWS:
            grid[target][col] = "#"
    return ["".join(row) for row in grid] + list(tail)


def write_matrix(open_eye: list[str], frames: list[list[str]]) -> None:
    def block(rows: list[str], indent: str = "    ") -> str:
        return "\n".join(f'{indent}"{row}",' for row in rows)

    animation = "\n".join(
        f"    (\n{block(frame, '        ')}\n    )," for frame in frames
    )

    text = f'''"""Логотип в виде матрицы. Файл создаётся tools/make_icon.py — не править.

Каждый символ — логический пиксель: «#» закрашен, «.» пуст. В консоли строки
рисуются полублоками, поэтому две строки матрицы занимают одну строку экрана:
пиксели получаются квадратными, а высота совпадает с надписью HUNTER CLI.

BLINK — кадры моргания по порядку: веки сходятся к середине глаза и
расходятся обратно.
"""

WIDTH = {CONSOLE_COLS}
HEIGHT = {CONSOLE_ROWS}

LOGO = (
{block(open_eye)}
)

BLINK = (
{animation}
)
'''
    io.open(MATRIX_MODULE, "w", encoding="utf-8", newline="\n").write(text)


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

    console = extract(CONSOLE_COLS, CONSOLE_ROWS)
    frames = [blink_frame(console, stage) for stage in BLINK_STAGES]
    write_matrix(console, frames)
    print(f"Матрица для консоли: {MATRIX_MODULE} "
          f"({CONSOLE_COLS}x{CONSOLE_ROWS}, кадров моргания {len(frames)})")

    detailed = extract(COLS, ROWS)
    master = render_master(detailed)
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
