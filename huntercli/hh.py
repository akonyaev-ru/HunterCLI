"""Клиент api.hh.ru: список резюме, поднятие, обновление токена."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from . import auth, salary
from .config import Account
from .logbus import LogBus

API_ROOT = "https://api.hh.ru"
CLIENT_UA = "HunterCLI/2.0 (resume autopilot)"

RETRYABLE = {429, 500, 502, 503, 504}

#: (сколько ждём соединения, сколько ждём ответ). Короткий первый таймаут
#: нужен, чтобы пропажу сети было видно в интерфейсе сразу, а не через минуту.
TIMEOUT = (8, 25)


class HHError(Exception):
    """Ошибка обращения к API."""


class NetworkError(HHError):
    """Сеть недоступна."""


class TokenError(HHError):
    """Токен истёк или отозван — нужна повторная авторизация."""


def token_is_sendable(token: str) -> bool:
    """Можно ли положить токен в HTTP-заголовок (только latin-1, без пробелов)."""
    if not token or token.strip() != token:
        return False
    try:
        token.encode("latin-1")
    except UnicodeEncodeError:
        return False
    return True


def parse_hh_time(value: str | None) -> datetime | None:
    """Разобрать дату hh.ru (`2026-08-05T14:02:11+0300`)."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    # hh отдаёт смещение без двоеточия — fromisoformat до 3.11 такое не ест.
    if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


#: Сколько обращений просим за раз. Сотня — потолок сервиса.
TALKS_PAGE = 100
#: Коллекция `active` — три десятка обращений, одной страницы хватает с запасом.
ACTIVE_PAGE = 100
#: Подбор вакансий под резюме: страница и сколько их брать.
#: 300 вакансий дают около сотни чисел с зарплатой — на них медиана
#: устойчива. Одна страница дала бы три десятка, и цифра бы прыгала.
SALARY_PAGE = 100
SALARY_PAGES = 3
#: Предохранитель от бесконечного цикла на неожиданном ответе: двадцать
#: страниц — это две тысячи обращений, больше у соискателя не бывает.
TALKS_MAX_PAGES = 20

#: Состояния обращения у сервиса. Отклик отправлен и ждёт ответа, отказ —
#: работодатель ответил нет, приглашение — позвал.
TALK_STATES = ("response", "discard", "invitation")


@dataclass
class Talks:
    """Сводка по обращениям к работодателям на момент опроса.

    Числа накопительные, как и счётчик просмотров: прирост считается
    разностью суточных срезов.
    """

    total: int = 0
    invitations: int = 0
    responses: int = 0
    discards: int = 0
    #: id резюме -> сколько обращений с него отправлено.
    by_resume: dict[str, int] = field(default_factory=dict)
    #: id резюме -> сколько приглашений оно принесло.
    invitations_by_resume: dict[str, int] = field(default_factory=dict)


@dataclass
class ActiveTalk:
    """Обращение из коллекции `active` — той, где идёт живая работа.

    Всего обращений может быть под четыре сотни (проверено: 356 на 2026-08-28),
    но в `active` их три десятка: остальное сервис уводит в архив сам. Важно,
    что скрытие работает ТОЛЬКО по этой коллекции (см. `hide`), поэтому и
    уборка, и поиск приглашений идут по ней.
    """

    id: str = ""
    state: str = ""
    #: Прочитано ли обращение владельцем. Непрочитанное приглашение — это то,
    #: о чём надо сказать сразу; прочитанное человек уже видел.
    read: bool = True
    updated_at: float = 0.0
    #: Название вакансии и работодателя — только для строки журнала об уборке.
    vacancy: str = ""
    employer: str = ""

    @property
    def is_invitation(self) -> bool:
        return self.state == "invitation"

    def stale_days(self, now: float) -> float:
        """Сколько суток не было движения. Без отметки времени — ноль."""
        return max(0.0, (now - self.updated_at) / 86400) if self.updated_at else 0.0

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "ActiveTalk":
        state = raw.get("state")
        vacancy = raw.get("vacancy") if isinstance(raw.get("vacancy"), dict) else {}
        employer = vacancy.get("employer") if isinstance(vacancy.get("employer"), dict) else {}
        return cls(
            id=str(raw.get("id") or ""),
            state=str((state or {}).get("id") or "") if isinstance(state, dict) else "",
            read=bool(raw.get("read", True)),
            updated_at=_stamp(raw.get("updated_at") or raw.get("created_at")),
            vacancy=str(vacancy.get("name") or ""),
            employer=str(employer.get("name") or ""),
        )


def _stamp(value: Any) -> float:
    """`2026-08-28T09:39:44+0300` -> unix. Мусор и пустое — ноль.

    Сервис отдаёт смещение без двоеточия; `fromisoformat` до 3.11 такое не
    принимал, поэтому разбираем защитно и молча сдаёмся на непонятном.
    """
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        pass
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return 0.0


@dataclass
class Resume:
    id: str
    title: str
    status: str = ""
    can_publish: bool = False
    next_publish_at: datetime | None = None
    total_views: int | None = None
    new_views: int | None = None
    finished: bool = True
    blocked: bool = False
    url: str = ""
    #: Момент, когда МЫ решили поднимать (разрешённое время + разброс).
    planned_at: float | None = field(default=None, compare=False)
    #: Текст последней проблемы именно по этому резюме.
    problem: str = ""
    #: Не трогать это резюме до этого момента (после отказа от hh.ru).
    retry_after: float = field(default=0.0, compare=False)

    @property
    def seconds_to_allowed(self) -> float:
        if not self.next_publish_at:
            return 0.0
        delta = (self.next_publish_at - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Resume":
        status = raw.get("status") or {}
        return cls(
            id=str(raw.get("id") or ""),
            title=(raw.get("title") or "Без названия").strip(),
            status=str(status.get("name") or status.get("id") or ""),
            can_publish=bool(raw.get("can_publish_or_update")),
            next_publish_at=parse_hh_time(raw.get("next_publish_at")),
            total_views=raw.get("total_views"),
            new_views=raw.get("new_views"),
            finished=bool(raw.get("finished", True)),
            blocked=bool(raw.get("blocked", False)),
            url=raw.get("alternate_url") or "",
        )


#: Признаки того, что hh.ru недоволен именно токеном, а не запросом.
#: Важно: api.hh.ru на плохой токен отвечает 403 (не 401) с телом вида
#: {"errors": [{"value": "bad_authorization", "type": "oauth"}]} — без учёта
#: этого автоматическое продление токена не срабатывало бы никогда.
AUTH_ERROR_MARKERS = {
    "oauth",
    "bad_authorization",
    "token_expired",
    "token_revoked",
    "forbidden",
}


def error_kinds(payload: dict[str, Any] | None) -> set[str]:
    """Собрать все type и value из ответа с ошибкой."""
    kinds: set[str] = set()
    for item in (payload or {}).get("errors") or []:
        for key in ("type", "value"):
            value = item.get(key)
            if value:
                kinds.add(str(value))
    return kinds


#: Понятные объяснения кодов ошибок hh.ru.
_ERROR_HINTS = {
    "quota_exceeded": "исчерпан дневной лимит поднятий",
    "not_enough_purchased_services": "услуга поднятия не оплачена",
    "resume_not_finished": "резюме не заполнено до конца",
    "resume_incomplete": "резюме не заполнено до конца",
    "resume_blocked": "резюме заблокировано модерацией",
    "resume_not_published": "резюме снято с публикации",
    "bad_argument": "сервис не принял запрос на поднятие",
}


def explain_errors(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    parts: list[str] = []
    for item in payload.get("errors") or []:
        kind = str(item.get("value") or item.get("type") or "").strip()
        if not kind:
            continue
        parts.append(_ERROR_HINTS.get(kind, kind))
    return "; ".join(dict.fromkeys(parts))


class HHClient:
    """Клиент одного аккаунта: свой токен и своя сессия соединений."""

    def __init__(self, account: Account, log: LogBus) -> None:
        self.account = account
        self.log = log
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": CLIENT_UA,
                "HH-User-Agent": CLIENT_UA,
                "Accept": "application/json",
            }
        )

    # ---------------------------------------------------------- транспорт

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.account.access_token}"}

    def _check_token_usable(self) -> None:
        """HTTP-заголовки кодируются latin-1: битый токен ловим до запроса.

        Иначе requests падает UnicodeEncodeError, и вместо понятного «войдите
        заново» пользователь видел бы бесконечную «внутреннюю ошибку».
        """
        if not token_is_sendable(self.account.access_token):
            raise TokenError("сохранённый токен испорчен")

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        url = path if path.startswith("http") else f"{API_ROOT}{path}"
        last_error: Exception | None = None

        for attempt in range(3):
            try:
                response = self.session.request(
                    method, url, headers=self._auth_headers(), timeout=TIMEOUT, **kwargs
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise NetworkError(str(exc)) from exc

            if response.status_code in RETRYABLE and attempt < 2:
                pause = int(response.headers.get("Retry-After") or 0) or 3 * (attempt + 1)
                self.log.warn(f"сервис ответил {response.status_code}, повтор через {pause} с")
                time.sleep(min(pause, 30))
                continue

            return response

        raise NetworkError(str(last_error) if last_error else "не удалось выполнить запрос")

    def _call(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        """Запрос с автоматическим обновлением токена при 401/403."""
        self._check_token_usable()
        self.ensure_fresh_token()
        response = self._request(method, path, **kwargs)

        if response.status_code in (401, 403):
            kinds = error_kinds(_json_or_none(response))
            # На 401 hh.ru тела с ошибками не даёт — считаем это проблемой токена.
            token_problem = response.status_code == 401 or bool(kinds & AUTH_ERROR_MARKERS)
            if token_problem:
                self.log.warn("сервис не принял токен — пробуем обновить")
                if not self.try_refresh():
                    raise TokenError("токен недействителен, обновить не удалось")
                response = self._request(method, path, **kwargs)
                if response.status_code in (401, 403):
                    kinds = error_kinds(_json_or_none(response))
                    if response.status_code == 401 or (kinds & AUTH_ERROR_MARKERS):
                        raise TokenError("сервис не принимает даже обновлённый токен")

        return response

    # -------------------------------------------------------------- токен

    def ensure_fresh_token(self) -> None:
        if self.account.needs_refresh:
            self.try_refresh()

    def try_refresh(self) -> bool:
        if not self.account.refresh_token:
            return False
        try:
            payload = auth.refresh_token(self.account.refresh_token)
        except auth.AuthError as exc:
            self.log.error(f"Не удалось обновить токен: {exc}")
            return False
        self.account.apply_token(payload)
        self.account.save()
        self.log.ok("Токен доступа обновлён автоматически")
        return True

    # --------------------------------------------------------------- API

    def identity(self) -> tuple[str, str]:
        """Кто владелец аккаунта: (идентификатор, имя). Пустые — не узнали.

        Идентификатор нужен, чтобы не подключить один и тот же аккаунт дважды:
        имя для этого не годится, полных тёзок никто не отменял.
        """
        response = self._call("GET", "/me")
        if response.status_code != 200:
            return "", ""
        data = _json_or_none(response) or {}
        name = " ".join(
            str(data.get(key) or "").strip()
            for key in ("first_name", "last_name")
        ).strip()
        return str(data.get("id") or ""), name or str(data.get("email") or "")

    def resumes(self) -> list[Resume]:
        response = self._call("GET", "/resumes/mine")
        if response.status_code == 403:
            # Проблемы с токеном _call уже отсеял — здесь что-то другое.
            detail = explain_errors(_json_or_none(response))
            raise HHError(detail or "не удалось получить список резюме")
        if response.status_code != 200:
            raise HHError(f"на список резюме пришёл код {response.status_code}")
        data = _json_or_none(response) or {}
        return [Resume.from_api(item) for item in data.get("items", [])]

    def negotiations(self) -> Talks:
        """Пересчитать обращения к работодателям по состояниям.

        Спросить «сколько у меня приглашений» одним запросом нельзя: фильтра
        под них нет. Проверено перебором 2026-08-27 — status принимает
        response, discard, all, active и non_archived, а invitation отвергает
        как bad_argument. Поэтому листаем всё подряд: четыре страницы на 355
        обращений, около двух секунд. Раз в сутки — нормально, на каждой
        синхронизации — перебор.

        Порядок записей сервис не гарантирует по дате (проверено там же:
        27-е, 25-е, 26-е подряд), поэтому остановиться на свежих нельзя.
        """
        talks = Talks()
        for page in range(TALKS_MAX_PAGES):
            response = self._call("GET", "/negotiations",
                                  params={"per_page": TALKS_PAGE, "page": page})
            if response.status_code != 200:
                detail = explain_errors(_json_or_none(response))
                raise HHError(detail or f"на список обращений пришёл код {response.status_code}")
            data = _json_or_none(response) or {}
            items = data.get("items")
            if not isinstance(items, list):
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                talks.total += 1
                state = str(((item.get("state") or {}) if isinstance(item.get("state"), dict)
                             else {}).get("id") or "")
                if state == "invitation":
                    talks.invitations += 1
                elif state == "discard":
                    talks.discards += 1
                elif state == "response":
                    talks.responses += 1
                resume = item.get("resume")
                identity = str((resume or {}).get("id") or "") if isinstance(resume, dict) else ""
                if not identity:
                    continue
                talks.by_resume[identity] = talks.by_resume.get(identity, 0) + 1
                if state == "invitation":
                    talks.invitations_by_resume[identity] = (
                        talks.invitations_by_resume.get(identity, 0) + 1)
            if page + 1 >= int(data.get("pages") or 1):
                break
        return talks

    def active_negotiations(self) -> list[ActiveTalk]:
        """Коллекция `active` одним запросом.

        Почему не полный перебор: `negotiations()` листает ВСЕ обращения (4
        запроса на 356 штук) и нужен для статистики раз в сутки. Здесь другое —
        приглашения и кандидаты на уборку живут только в `active`, а их три
        десятка, то есть одна страница.

        Проверено 2026-08-28: `?status=active` и `?status=non_archived` дают то
        же, что `/negotiations/active`; `?status=invitation` сервис отвергает
        как `bad_argument`, отдельного фильтра под приглашения нет.
        """
        response = self._call("GET", "/negotiations",
                              params={"status": "active", "per_page": ACTIVE_PAGE, "page": 0})
        if response.status_code != 200:
            detail = explain_errors(_json_or_none(response))
            raise HHError(detail or f"на активные обращения пришёл код {response.status_code}")
        data = _json_or_none(response) or {}
        items = data.get("items")
        return [ActiveTalk.from_api(item) for item in items
                if isinstance(item, dict)] if isinstance(items, list) else []

    def currency_rates(self) -> dict[str, float]:
        """Курсы валют от сервиса. Один запрос, 10 валют.

        Свой источник курсов заводить незачем: рядом с вакансией лежит её
        валюта, и пересчитывать надо тем же курсом, каким считает сам сервис.
        """
        response = self._call("GET", "/dictionaries")
        if response.status_code != 200:
            raise HHError(f"на справочники пришёл код {response.status_code}")
        return salary.rates_from_dictionary(_json_or_none(response))

    def similar_vacancies(self, resume_id: str, pages: int = SALARY_PAGES
                          ) -> tuple[list[Any], int]:
        """Вакансии, подобранные сервисом под резюме.

        Возвращает (поля `salary` всех вакансий, сколько вакансий просмотрено).
        В списке лежат и те, где зарплаты нет: их доля — часть ответа, без неё
        медиана вводит в заблуждение.

        Своего поиска по словам тут нет намеренно: подбор делает hh, и он
        учитывает опыт и роль лучше, чем набор ключевых слов.
        """
        found: list[Any] = []
        seen = 0
        for page in range(max(1, pages)):
            response = self._call("GET", f"/resumes/{resume_id}/similar_vacancies",
                                  params={"per_page": SALARY_PAGE, "page": page})
            if response.status_code != 200:
                detail = explain_errors(_json_or_none(response))
                raise HHError(detail or f"на подбор вакансий пришёл код {response.status_code}")
            data = _json_or_none(response) or {}
            items = data.get("items")
            if not isinstance(items, list) or not items:
                break
            for item in items:
                if isinstance(item, dict):
                    seen += 1
                    found.append(item.get("salary"))
            if page + 1 >= int(data.get("pages") or 1):
                break
        return found, seen

    def hide(self, talk_id: str) -> tuple[bool, str]:
        """Скрыть обращение. Возвращает (успех, пояснение).

        Метод и путь сняты пробой 2026-08-28 без токена: у
        `/negotiations/active/{id}` принимаются PUT и DELETE (403), а GET, POST
        и PATCH отвергаются (405). Ни одного другого имени коллекции DELETE не
        принимает — то есть скрывать можно только из `active`.

        Операция необратимая: вернуть скрытое обращение API не даёт.
        """
        response = self._call("DELETE", f"/negotiations/active/{talk_id}")
        if response.status_code in (200, 204):
            return True, ""
        detail = explain_errors(_json_or_none(response))
        return False, detail or f"код {response.status_code}"

    def publish(self, resume_id: str) -> tuple[bool, str]:
        """Поднять резюме. Возвращает (успех, пояснение)."""
        response = self._call("POST", f"/resumes/{resume_id}/publish")

        if response.status_code in (200, 201, 204):
            return True, ""

        detail = explain_errors(_json_or_none(response))
        if response.status_code == 429:
            return False, detail or "слишком часто — сервис просит подождать"
        if response.status_code == 404:
            return False, "резюме не найдено"
        return False, detail or f"сервис вернул код {response.status_code}"


def _json_or_none(response: requests.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
