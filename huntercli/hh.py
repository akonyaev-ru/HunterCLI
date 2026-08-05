"""Клиент api.hh.ru: список резюме, поднятие, обновление токена."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from . import auth
from .config import Config
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
    def __init__(self, cfg: Config, log: LogBus) -> None:
        self.cfg = cfg
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
        return {"Authorization": f"Bearer {self.cfg.access_token}"}

    def _check_token_usable(self) -> None:
        """HTTP-заголовки кодируются latin-1: битый токен ловим до запроса.

        Иначе requests падает UnicodeEncodeError, и вместо понятного «войдите
        заново» пользователь видел бы бесконечную «внутреннюю ошибку».
        """
        if not token_is_sendable(self.cfg.access_token):
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
        if self.cfg.needs_refresh:
            self.try_refresh()

    def try_refresh(self) -> bool:
        if not self.cfg.refresh_token:
            return False
        try:
            payload = auth.refresh_token(self.cfg.refresh_token)
        except auth.AuthError as exc:
            self.log.error(f"Не удалось обновить токен: {exc}")
            return False
        self.cfg.apply_token(payload)
        self.cfg.save()
        self.log.ok("Токен доступа обновлён автоматически")
        return True

    # --------------------------------------------------------------- API

    def whoami(self) -> str:
        response = self._call("GET", "/me")
        if response.status_code != 200:
            return ""
        data = _json_or_none(response) or {}
        name = " ".join(
            str(data.get(key) or "").strip()
            for key in ("first_name", "last_name")
        ).strip()
        return name or str(data.get("email") or "")

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
