"""Загрузка, миграция и сохранение config.json.

Аккаунтов может быть несколько: у каждого свой доступ, свой выбор резюме и
своя статистика. Общие настройки (разброс, тихие часы, сон) одни на всех —
это настройки программы, а не аккаунта.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from . import paths, secrets

SCHEMA_VERSION = 3

#: Токен hh.ru живёт 14 суток. Обновляем заранее, чтобы не ловить 401.
REFRESH_MARGIN_SEC = 2 * 24 * 3600

#: Больше и не нужно: у каждого аккаунта свой поток и свои обращения к сервису,
#: а полоса вкладок в узком окне перестаёт читаться.
MAX_ACCOUNTS = 8

#: Файл пишут потоки всех аккаунтов сразу. Без общей блокировки два сохранения
#: столкнулись бы на одном и том же временном файле и оставили обрывок.
_SAVE_LOCK = threading.Lock()


@dataclass
class Settings:
    #: Разброс задержки после разрешённого времени, чтобы не выглядеть роботом.
    jitter_min_sec: int = 45
    jitter_max_sec: int = 240
    #: Как часто перечитывать список резюме, даже когда поднимать ещё рано.
    sync_interval_sec: int = 900
    #: Не поднимать ночью (часы локального времени). None = круглосуточно.
    quiet_hours: list[int] | None = None
    #: Сколько строк журнала держать в памяти.
    log_lines: int = 400
    #: Не давать компьютеру засыпать по бездействию, пока программа работает.
    #: На явный сон (крышка, «Пуск → Сон») не влияет: это решает Windows.
    prevent_sleep: bool = True
    #: Будить компьютер из сна к моменту поднятия. Требует, чтобы в настройках
    #: питания были разрешены таймеры пробуждения.
    wake_from_sleep: bool = True

    def quiet_now(self, hour: int) -> bool:
        if not self.quiet_hours or len(self.quiet_hours) != 2:
            return False
        start, end = self.quiet_hours
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end  # интервал через полночь


#: Настройки для аккаунта, оторванного от конфига (такое бывает только в
#: тестах). Общие значения по умолчанию — лучше, чем падение на None.
_DETACHED_SETTINGS = Settings()


@dataclass
class Stats:
    total_bumps: int = 0
    failed_bumps: int = 0
    first_run: float = field(default_factory=time.time)
    last_bump_at: float | None = None
    #: Последние поднятия: [{"at": ts, "resume": "название"}]
    recent: list[dict[str, Any]] = field(default_factory=list)

    def record_bump(self, title: str, when: float) -> None:
        self.total_bumps += 1
        self.last_bump_at = when
        self.recent.insert(0, {"at": when, "resume": title})
        del self.recent[40:]


@dataclass
class Account:
    """Один подключённый аккаунт со своим доступом и своей статистикой."""

    #: Свой ключ, а не выданный сервисом: по нему называется папка сессии окна
    #: входа, и он должен существовать ещё до того, как вход состоится.
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    #: Имя владельца — то, что показывается на вкладке.
    name: str = ""
    #: Идентификатор владельца у сервиса. Нужен, чтобы не подключить дважды
    #: один и тот же аккаунт: два автопилота на одних резюме только мешают.
    person_id: str = ""
    #: id резюме, которыми управляем. None = все.
    managed_resumes: list[str] | None = None
    stats: Stats = field(default_factory=Stats)
    #: True, если сохранённый доступ не удалось расшифровать.
    corrupted: bool = False
    #: Конфиг-владелец: общие настройки и запись на диск. Не сериализуется.
    owner: "Config | None" = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------ окружение

    @property
    def settings(self) -> Settings:
        return self.owner.settings if self.owner is not None else _DETACHED_SETTINGS

    def save(self) -> None:
        if self.owner is not None:
            self.owner.save()

    # ---------------------------------------------------------------- токен

    @property
    def authorized(self) -> bool:
        return bool(self.access_token)

    @property
    def seconds_left(self) -> float:
        if not self.expires_at:
            return 0.0
        return max(0.0, self.expires_at - time.time())

    @property
    def needs_refresh(self) -> bool:
        if not self.refresh_token:
            return False
        if not self.expires_at:
            return False
        return self.seconds_left < REFRESH_MARGIN_SEC

    def apply_token(self, payload: dict[str, Any]) -> None:
        """Записать ответ /oauth/token."""
        self.access_token = payload.get("access_token", "") or ""
        self.refresh_token = payload.get("refresh_token", "") or self.refresh_token
        expires_in = payload.get("expires_in")
        self.expires_at = time.time() + float(expires_in) if expires_in else 0.0

    def clear_token(self) -> None:
        # Имя оставляем: по нему видно, какой именно вкладке нужен вход.
        # При следующем входе его всё равно перепроверят — экран входа
        # обнуляет и имя, и владельца, потому что войти могли в другой аккаунт.
        self.access_token = ""
        self.refresh_token = ""
        self.expires_at = 0.0

    # --------------------------------------------------------------- резюме

    def is_managed(self, resume_id: str) -> bool:
        return self.managed_resumes is None or resume_id in self.managed_resumes

    # ------------------------------------------------------------ хранение

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "access_token": secrets.protect(self.access_token),
            "refresh_token": secrets.protect(self.refresh_token),
            "expires_at": self.expires_at,
            "name": self.name,
            "person_id": self.person_id,
            "managed_resumes": self.managed_resumes,
            "stats": asdict(self.stats),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Account":
        account = cls(uid=str(raw.get("uid") or uuid.uuid4().hex[:12]))
        account.access_token = secrets.unprotect(raw.get("access_token", ""))
        account.refresh_token = secrets.unprotect(raw.get("refresh_token", ""))
        account.expires_at = float(raw.get("expires_at") or 0.0)
        account.name = raw.get("name", "") or ""
        account.person_id = str(raw.get("person_id") or "")
        managed = raw.get("managed_resumes")
        account.managed_resumes = [str(item) for item in managed] if managed is not None else None
        account.stats = _stats_from(raw.get("stats"))
        # Файл есть, а расшифровать не удалось (например, скопирован с другой
        # машины). Не падаем — просто просим войти заново.
        account.corrupted = bool(raw.get("access_token")) and not account.access_token
        return account


@dataclass
class Config:
    #: Хотя бы один аккаунт есть всегда: пустой означает «ещё не входили».
    accounts: list[Account] = field(default_factory=list)
    #: Номер аккаунта, чья вкладка открыта.
    active: int = 0
    settings: Settings = field(default_factory=Settings)
    #: True, если файл на диске не удалось прочитать или расшифровать.
    corrupted: bool = False

    def __post_init__(self) -> None:
        self.adopt()

    # ------------------------------------------------------------ аккаунты

    def adopt(self) -> None:
        """Признать аккаунты своими и привести номер вкладки в границы."""
        if not self.accounts:
            self.accounts = [Account()]
        for account in self.accounts:
            account.owner = self
        self.active = min(max(0, self.active), len(self.accounts) - 1)

    @property
    def account(self) -> Account:
        """Аккаунт открытой вкладки."""
        return self.accounts[self.active]

    def add_account(self) -> Account:
        account = Account(owner=self)
        self.accounts.append(account)
        return account

    def drop_account(self, index: int) -> None:
        """Убрать аккаунт. Последний не исчезает, а становится пустым."""
        if not 0 <= index < len(self.accounts):
            return
        del self.accounts[index]
        if not self.accounts:
            self.accounts = [Account()]
        self.adopt()
        self.active = min(index, len(self.accounts) - 1)

    def find_person(self, person_id: str, *, skip: int = -1) -> int:
        """Номер аккаунта того же владельца или -1. Пустой id не совпадает ни с чем."""
        if not person_id:
            return -1
        for index, account in enumerate(self.accounts):
            if index != skip and account.person_id == person_id:
                return index
        return -1

    @property
    def authorized(self) -> bool:
        """Есть ли хоть один аккаунт, в который мы вошли."""
        return any(account.authorized for account in self.accounts)

    # ------------------------------------------------------------ хранение

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "active": self.active,
            "accounts": [account.to_dict() for account in self.accounts],
            "settings": asdict(self.settings),
        }

    def save(self) -> None:
        path = paths.config_path()
        tmp = path + ".tmp"
        with _SAVE_LOCK:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
            os.replace(tmp, path)


def _known(raw: object, kind: type) -> dict[str, Any]:
    """Отсеять поля, которых в текущей версии структуры уже (или ещё) нет."""
    fields = {name for name in kind.__dataclass_fields__}
    return {k: v for k, v in (raw or {}).items() if k in fields} if isinstance(raw, dict) else {}


def _stats_from(raw: object) -> Stats:
    return Stats(**_known(raw, Stats))


def _migrate_v1(raw: dict[str, Any], cfg: Config) -> None:
    """Конфиг 1.x: {"token": "Bearer USER...", "resume_id": "..."}."""
    account = cfg.account
    token = (raw.get("token") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    account.access_token = token
    # В v1 не было refresh_token и срока — считаем, что срок неизвестен.
    account.expires_at = 0.0
    legacy_id = raw.get("resume_id")
    if legacy_id:
        account.managed_resumes = [str(legacy_id)]


def _migrate_v2(raw: dict[str, Any], cfg: Config) -> None:
    """Конфиг 2.x: один аккаунт в разделе auth, выбор резюме — в настройках."""
    auth = raw.get("auth") or {}
    settings = raw.get("settings") or {}
    account = cfg.account
    account.access_token = secrets.unprotect(auth.get("access_token", ""))
    account.refresh_token = secrets.unprotect(auth.get("refresh_token", ""))
    account.expires_at = float(auth.get("expires_at") or 0.0)
    account.name = auth.get("account", "") or ""
    account.managed_resumes = settings.get("managed_resumes")
    account.stats = _stats_from(raw.get("stats"))
    if auth.get("access_token") and not account.access_token:
        account.corrupted = True
        cfg.corrupted = True
    cfg.settings = Settings(**_known(settings, Settings))


def load() -> Config:
    cfg = Config()
    path = paths.config_path()
    if not os.path.exists(path):
        return cfg

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception:
        cfg.corrupted = True
        return cfg

    if not isinstance(raw, dict):
        cfg.corrupted = True
        return cfg

    version = int(raw.get("version", 1) or 1)
    if version < 2:
        _migrate_v1(raw, cfg)
        return cfg
    if version < 3:
        _migrate_v2(raw, cfg)
        return cfg

    accounts = [Account.from_dict(item) for item in raw.get("accounts") or []
                if isinstance(item, dict)]
    if accounts:
        cfg.accounts = accounts
    if any(account.corrupted for account in cfg.accounts):
        cfg.corrupted = True
    cfg.active = int(raw.get("active") or 0)
    cfg.settings = Settings(**_known(raw.get("settings"), Settings))
    cfg.adopt()
    return cfg
