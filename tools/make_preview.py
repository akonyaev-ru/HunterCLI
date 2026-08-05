# -*- coding: utf-8 -*-
"""Собрать docs/preview.svg — картинку дашборда для README.

Рисуем настоящий дашборд настоящим кодом, с демонстрационными данными.
Запуск: python tools\\make_preview.py
"""

from __future__ import annotations

import io
import os
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import huntercli.paths as paths

#: Конфиг и журнал при генерации картинки трогать нельзя.
paths.base_dir = lambda: os.path.join(ROOT, "tools")

from rich.console import Console

from huntercli.engine import Phase, Snapshot
from huntercli.hh import Resume
from huntercli.logbus import LogBus
from huntercli.ui.dashboard import Dashboard
from huntercli.ui.theme import THEME

WIDTH, HEIGHT = 118, 34
OUTPUT = os.path.join(ROOT, "docs", "preview.svg")


def build_log() -> LogBus:
    log = LogBus(to_file=False)
    log.info("Hunter CLI 2.0.0 запущен")
    log.step("Синхронизация: резюме — 3, под автопилотом — 2")
    log.ok("«Ведущий юрист по корпоративному праву» поднято в поиске")
    log.ok("«Руководитель юридического департамента» поднято в поиске")
    log.warn("сервис ответил 503, повтор через 3 с")
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


def main() -> int:
    console = Console(
        theme=THEME, highlight=False, file=io.StringIO(),
        width=WIDTH, height=HEIGHT, force_terminal=True,
        color_system="truecolor", record=True,
    )
    board = Dashboard(console, build_log())
    board.tick = 3
    console.print(board.render(build_snapshot()))

    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    console.save_svg(OUTPUT, title="Hunter CLI")
    print(f"Готово: {OUTPUT} ({os.path.getsize(OUTPUT) // 1024} КБ)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
