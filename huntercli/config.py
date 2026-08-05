"""Загрузка, миграция и сохранение config.json."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from . import paths, secrets

SCHEMA_VERSION = 2

#: Токен hh.ru живёт 14 суток. Обновляем заранее, чтобы не ловить 401.
REFRESH_MARGIN_SEC = 2 * 24 * 3600


@dataclass
class Settings:
    #: Разброс задержки после разрешённого времени, чтобы не выглядеть роботом.
    jitter_min_sec: int = 45
    jitter_max_sec: int = 240
    #: Как часто перечитывать список резюме, даже когда поднимать ещё рано.
    sync_interval_sec: int = 900
    #: id резюме, которыми управляем. None = все.
    managed_resumes: list[str] | None = None
    #: Не поднимать ночью (часы локального времени). None = круглосуточно.
    quiet_hours: list[int] | None = None
    #: Сколько строк журнала держать в памяти.
    log_lines: int = 400

    def is_managed(self, resume_id: str) -> bool:
        return self.managed_resumes is None or resume_id in self.managed_resumes

    def quiet_now(self, hour: int) -> bool:
        if not self.quiet_hours or len(self.quiet_hours) != 2:
            return False
        start, end = self.quiet_hours
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end  # интервал через полночь


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
class Config:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0
    account: str = ""
    settings: Settings = field(default_factory=Settings)
    stats: Stats = field(default_factory=Stats)
    #: True, если файл на диске не удалось расшифровать / прочитать.
    corrupted: bool = False

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
        self.access_token = ""
        self.refresh_token = ""
        self.expires_at = 0.0
        self.account = ""

    # ------------------------------------------------------------ хранение

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "auth": {
                "access_token": secrets.protect(self.access_token),
                "refresh_token": secrets.protect(self.refresh_token),
                "expires_at": self.expires_at,
                "account": self.account,
            },
            "settings": asdict(self.settings),
            "stats": asdict(self.stats),
        }

    def save(self) -> None:
        path = paths.config_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


def _migrate_v1(raw: dict[str, Any], cfg: Config) -> None:
    """Конфиг 1.x: {"token": "Bearer USER...", "resume_id": "..."}."""
    token = (raw.get("token") or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    cfg.access_token = token
    # В v1 не было refresh_token и срока — считаем, что срок неизвестен.
    cfg.expires_at = 0.0
    legacy_id = raw.get("resume_id")
    if legacy_id:
        cfg.settings.managed_resumes = [str(legacy_id)]


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

    if int(raw.get("version", 1)) < 2:
        _migrate_v1(raw, cfg)
        return cfg

    auth = raw.get("auth") or {}
    cfg.access_token = secrets.unprotect(auth.get("access_token", ""))
    cfg.refresh_token = secrets.unprotect(auth.get("refresh_token", ""))
    cfg.expires_at = float(auth.get("expires_at") or 0.0)
    cfg.account = auth.get("account", "") or ""
    if auth.get("access_token") and not cfg.access_token:
        # Файл есть, но расшифровать не удалось (например, скопирован с другой
        # машины). Не падаем — просто просим войти заново.
        cfg.corrupted = True

    known = {f for f in Settings.__dataclass_fields__}
    settings = {k: v for k, v in (raw.get("settings") or {}).items() if k in known}
    cfg.settings = Settings(**settings)

    known = {f for f in Stats.__dataclass_fields__}
    stats = {k: v for k, v in (raw.get("stats") or {}).items() if k in known}
    cfg.stats = Stats(**stats)

    return cfg
