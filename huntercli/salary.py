# -*- coding: utf-8 -*-
"""Зарплаты из вакансий: приведение к одному виду и сводка.

Здесь только чистые функции — ни запросов, ни файлов. Всё, что можно
посчитать без сети, считается тут, и тут же проверяется тестами.

Почему медиана, а не среднее: в выдаче попадаются вакансии с вилкой на
порядок выше остальных, и одна такая тянет среднее за собой. Медиана и
квартили описывают, где на самом деле лежит основная масса.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Iterable

#: Ставка НДФЛ для приведения «до вычета» к «на руки».
#: С 2025 года шкала прогрессивная (15 % свыше 2,4 млн в год), но вакансия
#: говорит про месяц и ничего не говорит про годовой доход человека. Угадывать
#: ставку хуже, чем взять базовую и честно об этом сказать в интерфейсе.
TAX_RATE = 0.13

#: Валюта, к которой всё приводим. У неё курс всегда 1.
BASE_CURRENCY = "RUR"

#: Нижняя граница правдоподобия, рубли в месяц. Ниже — не зарплата, а
#: опечатка или ставка за час: такие числа тянут медиану вниз и врут.
MIN_SANE = 10_000
#: Верхняя граница. Выше — почти всегда годовая сумма в поле месячной.
MAX_SANE = 5_000_000


@dataclass
class Summary:
    """Сводка по зарплатам выборки.

    `total` — сколько вакансий просмотрено, `count` — у скольких зарплата
    вообще указана. Разница между ними обязана быть на экране: зарплату
    публикует меньшинство, и медиана без этой доли вводит в заблуждение.
    """

    median: int = 0
    low: int = 0
    high: int = 0
    count: int = 0
    total: int = 0

    @property
    def empty(self) -> bool:
        return self.count == 0

    @property
    def share(self) -> str:
        """«96 из 300» — то, без чего медиану показывать нельзя."""
        return f"{self.count} из {self.total}"


def to_rub(salary: Any, rates: dict[str, float]) -> float | None:
    """Одна зарплата -> рубли на руки. Непригодное -> None.

    Вилка сводится к середине; указана одна граница — берётся она. Курс
    сервис отдаёт как «сколько единиц валюты в рубле», поэтому пересчёт —
    делением: EUR с курсом 0.00997 даёт около ста рублей за евро.
    """
    if not isinstance(salary, dict):
        return None

    bounds = [float(salary[key]) for key in ("from", "to")
              if isinstance(salary.get(key), (int, float))]
    if not bounds:
        return None
    amount = sum(bounds) / len(bounds)

    code = str(salary.get("currency") or BASE_CURRENCY).upper()
    if code != BASE_CURRENCY:
        rate = rates.get(code)
        if not rate:
            # Незнакомая валюта: молча выбрасываем. Пересчитать нечем, а
            # положить чужие деньги в рублёвый ряд — испортить медиану.
            return None
        amount /= rate

    if salary.get("gross"):
        amount *= 1 - TAX_RATE

    return amount if MIN_SANE <= amount <= MAX_SANE else None


def rates_from_dictionary(payload: Any) -> dict[str, float]:
    """`/dictionaries` -> {код валюты: курс}. Мусор -> пустой словарь."""
    if not isinstance(payload, dict):
        return {}
    out: dict[str, float] = {}
    for item in payload.get("currency") or []:
        if not isinstance(item, dict):
            continue
        code, rate = item.get("code"), item.get("rate")
        if isinstance(code, str) and isinstance(rate, (int, float)) and rate > 0:
            out[code.upper()] = float(rate)
    return out


def summarize(salaries: Iterable[Any], rates: dict[str, float], total: int) -> Summary:
    """Сводка по списку полей `salary` из вакансий.

    `total` передаётся отдельно: в списке лежат все вакансии, включая те, где
    зарплаты нет вовсе, и доля указавших — часть ответа.
    """
    values = [rub for rub in (to_rub(item, rates) for item in salaries) if rub is not None]
    if not values:
        return Summary(total=total)

    values.sort()
    median = statistics.median(values)
    if len(values) >= 4:
        low, _, high = statistics.quantiles(values, n=4)
    else:
        # На двух-трёх числах квартили — выдумка. Показываем размах: он хотя бы
        # честно говорит, что выборка крошечная.
        low, high = values[0], values[-1]
    return Summary(median=_round(median), low=_round(low), high=_round(high),
                   count=len(values), total=total)


def _round(value: float) -> int:
    """К тысяче: точность до рубля тут ложная, а лишние цифры мешают читать."""
    return int(round(value / 1000.0) * 1000)


def human(value: int) -> str:
    """`185000` -> `185 000`.

    Пробел обычный: колонка значений на экране статистики и так `no_wrap`,
    переноситься там нечему, а неразрывный пробел — лишний нетипичный символ
    в интерфейсе, который и без него полон особых знаков.
    """
    return f"{value:,}".replace(",", " ")
