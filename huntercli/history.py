"""История просмотров: одна точка в сутки на каждое резюме.

Список резюме приходит с каждой синхронизацией и несёт накопительный счётчик
`total_views`. Сам по себе он ничего не говорит — важен прирост. Здесь счётчик
раскладывается по дням, и из разностей считается всё остальное: сколько
просмотров принесло поднятие и растёт ли результат от недели к неделе.

Почему `new_views` для этого не годится: это «новые с вашего последнего
просмотра», сервис обнуляет счётчик, когда владелец открывает резюме у себя на
сайте. История на нём врала бы, причём незаметно.

Файл отдельный от `config.json` намеренно: конфиг проходит миграции схемы и
хранит зашифрованные доступы, а тут открытые числа, которые не жалко.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable

from . import paths

SCHEMA_VERSION = 1

#: Сколько суток храним. Год с запасом: файл всё равно измеряется килобайтами,
#: а сравнить «этот сентябрь с прошлым» когда-нибудь захочется.
KEEP_DAYS = 400

#: Окно сводки по умолчанию.
WINDOW_DAYS = 7

#: Чтение-правка-запись идут под общей блокировкой: движков столько же, сколько
#: аккаунтов, и пишут они в один файл из разных потоков. Файл перечитывается
#: каждый раз намеренно — так не бывает расхождения кэшей, а обращений всего
#: несколько в час.
_LOCK = threading.Lock()


def today() -> str:
    return date.today().isoformat()


def _shift(day: str, delta: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()


def load() -> dict[str, Any]:
    """Прочитать файл. Отсутствие или порча — пустая история, а не ошибка."""
    try:
        with open(paths.history_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"version": SCHEMA_VERSION, "accounts": {}}
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), dict):
        return {"version": SCHEMA_VERSION, "accounts": {}}
    data["version"] = SCHEMA_VERSION
    return data


def save(data: dict[str, Any]) -> bool:
    """Записать через временный файл. False — не смогли, и это не беда."""
    path = paths.history_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _account(data: dict[str, Any], uid: str) -> dict[str, Any]:
    entry = data.setdefault("accounts", {}).setdefault(uid, {})
    entry.setdefault("resumes", {})
    entry.setdefault("bumps", {})
    return entry


def _prune(entry: dict[str, Any], now: str) -> None:
    edge = _shift(now, -KEEP_DAYS)
    for resume in entry["resumes"].values():
        resume["days"] = {d: v for d, v in (resume.get("days") or {}).items() if d >= edge}
    entry["bumps"] = {d: v for d, v in entry["bumps"].items() if d >= edge}


def record_views(uid: str, resumes: Iterable[Any], *, now: str = "") -> bool:
    """Запомнить сегодняшний счётчик просмотров каждого резюме.

    За сутки синхронизаций десятки, и каждая перезаписывает точку этого дня:
    последнее значение суток и есть итог дня.

    От `resumes` нужны три поля: `id`, `title`, `total_views`. Резюме без
    счётчика пропускаем — ноль и «неизвестно» это разные вещи.
    """
    now = now or today()
    with _LOCK:
        data = load()
        entry = _account(data, uid)
        touched = False
        for item in resumes:
            views = getattr(item, "total_views", None)
            identity = str(getattr(item, "id", "") or "")
            if views is None or not identity:
                continue
            slot = entry["resumes"].setdefault(identity, {"title": "", "days": {}})
            slot["title"] = getattr(item, "title", "") or slot.get("title", "")
            slot.setdefault("days", {})[now] = int(views)
            touched = True
        if not touched:
            return False
        _prune(entry, now)
        return save(data)


def record_bump(uid: str, *, now: str = "") -> bool:
    """Отметить успешное поднятие: из этих чисел считается отдача поднятия."""
    now = now or today()
    with _LOCK:
        data = load()
        entry = _account(data, uid)
        entry["bumps"][now] = int(entry["bumps"].get(now, 0) or 0) + 1
        _prune(entry, now)
        return save(data)


def forget(uid: str) -> bool:
    """Убрать историю отключённого аккаунта."""
    with _LOCK:
        data = load()
        if uid not in data.get("accounts", {}):
            return False
        del data["accounts"][uid]
        return save(data)


def _daily_gains(days: dict[str, Any]) -> dict[str, int]:
    """Прирост по дням: разность соседних точек.

    Отрицательная разность означает, что счётчик у сервиса начался заново
    (резюме пересоздали) — записывать «минус тысяча просмотров» нельзя, такой
    день считаем нулевым. У самой первой точки прироста нет: не с чем сравнить.
    """
    gains: dict[str, int] = {}
    previous: int | None = None
    for day in sorted(days):
        try:
            value = int(days[day])
        except (TypeError, ValueError):
            continue
        if previous is not None:
            gains[day] = max(0, value - previous)
        previous = value
    return gains


@dataclass
class ResumeStat:
    """Одно резюме в сводке."""

    title: str
    views: int
    total: int


@dataclass
class Report:
    """Сводка за окно."""

    days: int = WINDOW_DAYS
    views: int = 0
    #: Столько же дней перед окном — с этим и сравниваем.
    views_before: int = 0
    bumps: int = 0
    #: За сколько дней окна данные вправду есть. Программу не держат
    #: включённой круглые сутки, и молчать об этом нечестно.
    covered: int = 0
    total_views: int = 0
    since: str = ""
    resumes: list[ResumeStat] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.since or not self.resumes

    @property
    def change(self) -> int:
        return self.views - self.views_before

    @property
    def per_bump(self) -> float | None:
        """Просмотров на одно поднятие. None — поднятий за окно не было."""
        return self.views / self.bumps if self.bumps else None


def report(uid: str, *, days: int = WINDOW_DAYS, now: str = "") -> Report:
    """Сводка по аккаунту за последние `days` суток."""
    now = now or today()
    entry = load().get("accounts", {}).get(uid) or {}
    resumes = entry.get("resumes")
    if not isinstance(resumes, dict) or not resumes:
        return Report(days=days)

    start = _shift(now, -(days - 1))
    prev_start = _shift(now, -(2 * days - 1))
    covered: set[str] = set()
    stats: list[ResumeStat] = []
    seen: list[str] = []
    views = before = total = 0

    for slot in resumes.values():
        points = slot.get("days")
        if not isinstance(points, dict) or not points:
            continue
        covered.update(day for day in points if start <= day <= now)
        seen.extend(points)
        gains = _daily_gains(points)
        gained = sum(v for day, v in gains.items() if start <= day <= now)
        views += gained
        before += sum(v for day, v in gains.items() if prev_start <= day < start)
        current = int(points.get(max(points), 0) or 0)
        total += current
        stats.append(ResumeStat(slot.get("title") or "Без названия", gained, current))

    bumps = entry.get("bumps") or {}
    # По убыванию отдачи: разговор начинается с резюме, которое работает.
    stats.sort(key=lambda item: (-item.views, -item.total, item.title))
    return Report(
        days=days,
        views=views,
        views_before=before,
        bumps=sum(int(v or 0) for day, v in bumps.items() if start <= day <= now),
        covered=len(covered),
        total_views=total,
        since=min(seen) if seen else "",
        resumes=stats,
    )
