# -*- coding: utf-8 -*-
"""История просмотров и сводка по ней.

Считается всё из разностей накопительного счётчика, поэтому здесь проверяются
прежде всего неудобные случаи: счётчик сбросился, программа не работала
несколько дней, за сутки записали десять раз.
"""

from __future__ import annotations

import io
import os

from harness import Report, sandbox

sandbox()

from huntercli import history, paths


class FakeResume:
    """От резюме истории нужны ровно три поля."""

    def __init__(self, identity: str, title: str, total_views: int | None) -> None:
        self.id = identity
        self.title = title
        self.total_views = total_views


def _reset() -> None:
    try:
        os.remove(paths.history_path())
    except OSError:
        pass


def _feed(uid: str, points: dict[str, int], *, identity: str = "1", title: str = "Юрист") -> None:
    """Разложить готовый ряд «дата -> счётчик» по дням."""
    for day, value in sorted(points.items()):
        history.record_views(uid, [FakeResume(identity, title, value)], now=day)


def run() -> bool:
    report = Report("История просмотров")

    report.section("Пока данных нет")
    _reset()
    empty = history.report("нет-такого")
    report.check("сводка пустая, без падения", empty.empty)
    report.check("нули, а не мусор", empty.views == 0 and empty.bumps == 0)
    report.check("отдача поднятия неизвестна", empty.per_bump is None)

    report.section("Прирост считается разностью соседних точек")
    _reset()
    _feed("a", {"2026-08-20": 100, "2026-08-21": 110, "2026-08-22": 135})
    week = history.report("a", now="2026-08-22")
    report.check("прирост за окно верный", week.views == 35, f"-> {week.views}")
    report.check("текущее значение счётчика", week.total_views == 135, f"-> {week.total_views}")
    report.check("первая точка прироста не даёт", week.covered == 3, f"-> {week.covered}")
    report.check("дата начала запомнена", week.since == "2026-08-20", f"-> {week.since}")

    report.section("Счётчик сбросился — минуса быть не должно")
    _reset()
    # Резюме пересоздали: у сервиса счётчик начался заново.
    _feed("a", {"2026-08-20": 900, "2026-08-21": 5, "2026-08-22": 12})
    after = history.report("a", now="2026-08-22")
    report.check("прирост не ушёл в минус", after.views == 7, f"-> {after.views}")

    report.section("За сутки записываем сколько угодно раз")
    _reset()
    for value in (100, 104, 109):
        history.record_views("a", [FakeResume("1", "Юрист", value)], now="2026-08-21")
    history.record_views("a", [FakeResume("1", "Юрист", 130)], now="2026-08-22")
    same_day = history.report("a", now="2026-08-22")
    report.check("итог суток — последнее значение", same_day.views == 21, f"-> {same_day.views}")
    report.check("день не задвоился", same_day.covered == 2, f"-> {same_day.covered}")

    report.section("Сравнение с предыдущим таким же окном")
    _reset()
    # Первая неделя даёт +10, вторая +40: динамика должна быть +30.
    _feed("a", {"2026-08-08": 0, "2026-08-14": 10, "2026-08-21": 50})
    change = history.report("a", days=7, now="2026-08-21")
    report.check("прирост окна", change.views == 40, f"-> {change.views}")
    report.check("прирост прошлого окна", change.views_before == 10, f"-> {change.views_before}")
    report.check("динамика посчитана", change.change == 30, f"-> {change.change}")

    report.section("Отдача поднятия")
    _reset()
    _feed("a", {"2026-08-21": 100, "2026-08-22": 140})
    for _ in range(4):
        history.record_bump("a", now="2026-08-22")
    paid = history.report("a", now="2026-08-22")
    report.check("поднятия сосчитаны", paid.bumps == 4, f"-> {paid.bumps}")
    report.check("просмотров на поднятие", paid.per_bump == 10.0, f"-> {paid.per_bump}")
    # Поднятия вне окна в расчёт не идут.
    history.record_bump("a", now="2026-07-01")
    report.check("старое поднятие в окно не попало",
                 history.report("a", now="2026-08-22").bumps == 4)

    report.section("Пропуски признаём честно")
    _reset()
    # Программа не работала 20-го и 21-го: данных за окно только два дня.
    _feed("a", {"2026-08-19": 10, "2026-08-22": 40})
    gaps = history.report("a", days=7, now="2026-08-22")
    report.check("дней с данными меньше длины окна", gaps.covered == 2, f"-> {gaps.covered}")
    report.check("длина окна на месте", gaps.days == 7)
    report.check("накопленное за простой не потеряно", gaps.views == 30, f"-> {gaps.views}")

    report.section("Разбивка по резюме")
    _reset()
    _feed("a", {"2026-08-21": 10, "2026-08-22": 90}, identity="1", title="Юрист")
    _feed("a", {"2026-08-21": 10, "2026-08-22": 15}, identity="2", title="Дизайнер")
    _feed("a", {"2026-08-21": 7, "2026-08-22": 7}, identity="3", title="Курьер")
    split = history.report("a", now="2026-08-22")
    titles = [item.title for item in split.resumes]
    report.check("резюме перечислены", len(split.resumes) == 3, f"-> {titles}")
    report.check("сильное резюме первое", titles[0] == "Юрист", f"-> {titles}")
    report.check("слабое последнее", titles[-1] == "Курьер", f"-> {titles}")
    report.check("сумма разбивки равна общему приросту",
                 sum(item.views for item in split.resumes) == split.views,
                 f"-> {split.views}")
    report.check("общий счётчик — сумма по резюме", split.total_views == 112,
                 f"-> {split.total_views}")

    report.section("Аккаунты не смешиваются")
    _reset()
    _feed("a", {"2026-08-21": 0, "2026-08-22": 50})
    _feed("b", {"2026-08-21": 0, "2026-08-22": 5})
    report.check("у каждого свой прирост",
                 history.report("a", now="2026-08-22").views == 50
                 and history.report("b", now="2026-08-22").views == 5)
    report.check("история отключённого аккаунта убирается", history.forget("b"))
    report.check("после этого он пуст", history.report("b", now="2026-08-22").empty)
    report.check("соседа не задело", history.report("a", now="2026-08-22").views == 50)

    report.section("Резюме без счётчика пропускаем")
    _reset()
    report.check("одно «неизвестно» — записывать нечего",
                 history.record_views("a", [FakeResume("1", "Юрист", None)]) is False)
    report.check("файла не появилось", not os.path.exists(paths.history_path()))

    report.section("Битый файл не роняет программу")
    _reset()
    with io.open(paths.history_path(), "w", encoding="utf-8") as fh:
        fh.write("{это не json")
    report.check("чтение вернуло пустую историю", history.load()["accounts"] == {})
    report.check("сводка тоже пустая", history.report("a").empty)
    report.check("поверх мусора пишется нормально",
                 history.record_views("a", [FakeResume("1", "Юрист", 5)]))

    report.section("Старое прореживается")
    _reset()
    _feed("a", {"2025-01-01": 1, "2026-08-21": 100, "2026-08-22": 120})
    stored = history.load()["accounts"]["a"]["resumes"]["1"]["days"]
    report.check("точка старше года выброшена", "2025-01-01" not in stored,
                 f"-> {sorted(stored)}")
    report.check("свежие точки на месте", len(stored) == 2, f"-> {sorted(stored)}")

    _reset()
    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
