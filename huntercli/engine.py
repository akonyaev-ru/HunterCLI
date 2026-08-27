"""Фоновый движок: синхронизация резюме, планирование и поднятие."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from . import history
from .config import Account
from .hh import HHClient, HHError, NetworkError, Resume, TokenError
from .logbus import LogBus, TaggedLog
from .power import SleepDetector, WakeTimer

#: Минимальный интервал между обращениями к /resumes/mine, чтобы не долбить API.
MIN_SYNC_GAP_SEC = 45
#: Пауза перед контрольной синхронизацией после поднятия.
POST_BUMP_DELAY_SEC = 4
#: Сколько не трогать резюме, которое hh.ru отказался поднимать. Без этой
#: паузы отказ вроде «исчерпан лимит» приводил бы к попытке каждую минуту.
FAILURE_COOLDOWN_SEC = 30 * 60
#: Шаг «просыпания» цикла — от него зависит отзывчивость на горячие клавиши.
TICK_SEC = 1.0


class Phase:
    STARTING = "starting"
    SYNCING = "syncing"
    WAITING = "waiting"
    BUMPING = "bumping"
    OFFLINE = "offline"
    AUTH = "auth"
    PAUSED = "paused"
    STOPPED = "stopped"


def human_nap(seconds: float) -> str:
    """Длительность сна человеческими словами."""
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{max(1, minutes)} мин"
    return f"{minutes // 60} ч {minutes % 60:02d} мин"


PHASE_LABEL = {
    Phase.STARTING: "ЗАПУСК",
    Phase.SYNCING: "СИНХРОНИЗАЦИЯ",
    Phase.WAITING: "Мониторинг",
    Phase.BUMPING: "ПОДНИМАЕМ",
    Phase.OFFLINE: "НЕТ СЕТИ",
    Phase.AUTH: "НУЖЕН ВХОД",
    Phase.PAUSED: "ПАУЗА",
    Phase.STOPPED: "ОСТАНОВЛЕН",
}


@dataclass
class Snapshot:
    """Согласованный слепок состояния для отрисовки."""

    phase: str = Phase.STARTING
    detail: str = ""
    resumes: list[Resume] = field(default_factory=list)
    account: str = ""
    started_at: float = 0.0
    session_bumps: int = 0
    total_bumps: int = 0
    last_bump_at: float | None = None
    last_sync_at: float | None = None
    next_action_at: float | None = None
    wait_span: float = 0.0
    token_seconds_left: float = 0.0
    paused: bool = False
    offline_since: float | None = None

    @property
    def managed_count(self) -> int:
        return sum(1 for item in self.resumes if item.planned_at is not None)


class BumpEngine:
    """Автопилот одного аккаунта.

    Аккаунтов может быть несколько — тогда рядом работает столько же движков,
    у каждого свой доступ, свой список резюме и своё расписание.
    """

    def __init__(
        self,
        account: Account,
        client: HHClient,
        log: LogBus | TaggedLog,
        *,
        slot: int = 1,
    ) -> None:
        self.account = account
        self.client = client
        self.log = log
        #: Номер вкладки (с 1). Нужен, пока имя владельца ещё не известно.
        self.slot = slot

        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._phase = Phase.STARTING
        self._detail = ""
        self._resumes: list[Resume] = []
        self._account = account.name
        self._started_at = time.time()
        self._session_bumps = 0
        self._last_sync_at: float | None = None
        self._next_action_at: float | None = None
        self._wait_span = 0.0
        self._offline_since: float | None = None
        self._paused = False
        self._force_sync = True
        self._force_bump = False
        self._auth_needed = False
        self._backoff = 30.0
        #: Спрашивали ли уже, кто владелец аккаунта. Сервис мог и не ответить
        #: именем — тогда дёргать его каждую синхронизацию незачем.
        self._identified = False

        self._wake_timer = WakeTimer()
        self._sleep_detector = SleepDetector()

    # ------------------------------------------------------------ фасад UI

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"engine-{self.slot}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._wake_timer.close()

    def join(self, timeout: float = 5.0) -> None:
        if self._thread:
            self._thread.join(timeout)

    def request_sync(self) -> None:
        with self._lock:
            self._force_sync = True
        self._wake.set()

    def request_bump(self) -> None:
        with self._lock:
            self._force_bump = True
            self._force_sync = True
        self._wake.set()

    def toggle_pause(self) -> bool:
        with self._lock:
            self._paused = not self._paused
            paused = self._paused
        self.log.info("Автопилот на паузе" if paused else "Автопилот снова в работе")
        self._wake.set()
        return paused

    def toggle_resume(self, index: int) -> str | None:
        """Включить/выключить резюме по его номеру в таблице (с 1)."""
        with self._lock:
            if not 0 < index <= len(self._resumes):
                return None
            target = self._resumes[index - 1]
            managed = self.account.managed_resumes
            if managed is None:
                managed = [item.id for item in self._resumes]
            if target.id in managed:
                managed = [i for i in managed if i != target.id]
                verdict = f"«{target.title}» больше не поднимаем"
            else:
                managed = managed + [target.id]
                verdict = f"«{target.title}» снова в работе"
            self.account.managed_resumes = managed
            self._replan_locked()
        self.account.save()
        self.log.info(verdict)
        self._wake.set()
        return verdict

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(
                phase=Phase.PAUSED if self._paused and self._phase == Phase.WAITING else self._phase,
                detail=self._detail,
                resumes=list(self._resumes),
                account=self._account,
                started_at=self._started_at,
                session_bumps=self._session_bumps,
                total_bumps=self.account.stats.total_bumps,
                last_bump_at=self.account.stats.last_bump_at,
                last_sync_at=self._last_sync_at,
                next_action_at=self._next_action_at,
                wait_span=self._wait_span,
                token_seconds_left=self.account.seconds_left,
                paused=self._paused,
                offline_since=self._offline_since,
            )

    def brief(self) -> tuple[str, str]:
        """Для полосы вкладок: имя владельца (может быть пустым) и фаза."""
        with self._lock:
            phase = Phase.PAUSED if self._paused and self._phase == Phase.WAITING else self._phase
            return self._account, phase

    @property
    def auth_needed(self) -> bool:
        with self._lock:
            return self._auth_needed

    def clear_auth_flag(self) -> None:
        with self._lock:
            self._auth_needed = False
            self._force_sync = True
            self._phase = Phase.SYNCING
            # Вход мог быть и в другой аккаунт — владельца выясним заново.
            self._account = self.account.name
            self._identified = False
        self._wake.set()

    # -------------------------------------------------------- планирование

    def _set_phase(self, phase: str, detail: str = "") -> None:
        with self._lock:
            self._phase = phase
            self._detail = detail

    def _jitter_for(self, resume: Resume, allowed_ts: float) -> int:
        """Стабильный разброс: пока hh не сдвинет время, отсчёт не «прыгает»."""
        settings = self.account.settings
        low = max(0, int(settings.jitter_min_sec))
        high = max(low, int(settings.jitter_max_sec))
        if high == 0:
            return 0
        return random.Random(f"{resume.id}:{int(allowed_ts)}").randint(low, high)

    def _shift_out_of_quiet(self, when: float) -> float:
        """Сдвинуть время за пределы «тихих часов»."""
        settings = self.account.settings
        if not settings.quiet_hours:
            return when
        moment = datetime.fromtimestamp(when)
        guard = 0
        while settings.quiet_now(moment.hour) and guard < 48:
            moment = moment.replace(minute=0, second=0, microsecond=0)
            when = moment.timestamp() + 3600
            moment = datetime.fromtimestamp(when)
            guard += 1
        return when

    def _replan_locked(self) -> None:
        now = time.time()
        for resume in self._resumes:
            if not self.account.is_managed(resume.id):
                resume.planned_at = None
                continue
            if resume.blocked or not resume.finished:
                resume.planned_at = None
                continue

            allowed = resume.next_publish_at.timestamp() if resume.next_publish_at else now
            if resume.can_publish and allowed <= now:
                planned = now  # можно прямо сейчас — не тянем
            else:
                planned = allowed + self._jitter_for(resume, allowed)
            # После отказа hh.ru выдерживаем паузу, даже если время «разрешено».
            planned = max(planned, resume.retry_after)
            resume.planned_at = self._shift_out_of_quiet(planned)

    # -------------------------------------------------------------- шаги

    def _sync(self) -> None:
        self._set_phase(Phase.SYNCING, "получаем список резюме")
        fresh = self.client.resumes()

        with self._lock:
            previous = {item.id: item for item in self._resumes}
            for item in fresh:
                stale = previous.get(item.id)
                if stale is not None:
                    item.problem = stale.problem
                    item.retry_after = stale.retry_after
            self._resumes = fresh
            self._last_sync_at = time.time()
            self._replan_locked()
            self._offline_since = None
            managed = sum(1 for i in fresh if i.planned_at is not None)

        if not self._identified and (not self._account or not self.account.person_id):
            try:
                person_id, name = self.client.identity()
                self._identified = True
            except HHError:
                person_id, name = "", ""
            if name or person_id:
                with self._lock:
                    self._account = name or self._account
                self.account.name = name or self.account.name
                self.account.person_id = person_id or self.account.person_id
                self.account.save()

        # Срез просмотров за сегодня. Точка одна на сутки, синхронизаций
        # много — каждая просто уточняет её свежим значением.
        history.record_views(self.account.uid, fresh)

        if not fresh:
            self.log.warn("На аккаунте не найдено ни одного резюме")
        else:
            self.log.step(f"Синхронизация: резюме — {len(fresh)}, под автопилотом — {managed}")

    def _due_resumes(self, force: bool) -> list[Resume]:
        now = time.time()
        with self._lock:
            picked = []
            for resume in self._resumes:
                if resume.planned_at is None:
                    continue
                if force and resume.can_publish:
                    picked.append(resume)
                elif resume.can_publish and resume.planned_at <= now:
                    picked.append(resume)
            return picked

    def _bump(self, targets: list[Resume]) -> bool:
        touched = False
        for resume in targets:
            self._set_phase(Phase.BUMPING, resume.title)
            ok, detail = self.client.publish(resume.id)
            if ok:
                touched = True
                moment = time.time()
                with self._lock:
                    self._session_bumps += 1
                    resume.problem = ""
                    resume.retry_after = 0.0
                self.account.stats.record_bump(resume.title, moment)
                history.record_bump(self.account.uid)
                self.log.ok(f"«{resume.title}» поднято в поиске")
            else:
                self.account.stats.failed_bumps += 1
                with self._lock:
                    resume.problem = detail
                    resume.retry_after = time.time() + FAILURE_COOLDOWN_SEC
                    self._replan_locked()
                self.log.error(
                    f"«{resume.title}»: {detail}. "
                    f"Следующая попытка через {FAILURE_COOLDOWN_SEC // 60} мин"
                )
        self.account.save()
        return touched

    def _arm_wake_timer(self, seconds: float) -> None:
        """Разбудить компьютер к моменту поднятия, если он успеет заснуть.

        Ставим будильник чуть раньше срока: системе нужно время подняться,
        а сети — подключиться.
        """
        if not self.account.settings.wake_from_sleep or not self._wake_timer.supported:
            return
        lead = 60.0
        self._wake_timer.arm(max(1.0, seconds - lead))

    def _compute_wait(self) -> float:
        """Сколько спать до следующего осмысленного действия."""
        now = time.time()
        settings = self.account.settings
        with self._lock:
            planned = [r.planned_at for r in self._resumes if r.planned_at is not None]
            sync_due = (self._last_sync_at or 0) + settings.sync_interval_sec

        candidates = [sync_due]
        if planned:
            candidates.append(min(planned))
        target = max(min(candidates), now + 1)

        with self._lock:
            self._next_action_at = min(planned) if planned else None
            self._wait_span = max(1.0, target - now)
        return target - now

    def _sleep(self, seconds: float) -> None:
        """Спать, просыпаясь на команды пользователя.

        Заодно ловим момент, когда система сама уходила в сон: тогда время
        ожидания истекло не по нашим часам, и сверяться с сервером нужно
        немедленно, а не досиживать остаток.
        """
        deadline = time.time() + seconds
        while not self._stop.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            if self._wake.wait(min(TICK_SEC, remaining)):
                self._wake.clear()
                return
            napped = self._sleep_detector.check()
            if napped:
                self.log.info(
                    f"Компьютер просыпался после сна ({human_nap(napped)}) — "
                    "сверяемся с сервером"
                )
                with self._lock:
                    self._force_sync = True
                return

    # -------------------------------------------------------------- цикл

    def _run(self) -> None:
        self.log.info("Автопилот запущен")
        while not self._stop.is_set():
            try:
                self._iteration()
            except TokenError as exc:
                self._on_token_error(exc)
            except NetworkError as exc:
                self._on_network_error(exc)
            except HHError as exc:
                self._set_phase(Phase.WAITING, str(exc))
                self.log.error(f"Ошибка сервиса: {exc}")
                self._sleep(120)
            except Exception as exc:  # движок не имеет права падать
                self.log.error(f"Внутренняя ошибка: {exc!r}")
                self._sleep(60)
        self._set_phase(Phase.STOPPED)

    def _iteration(self) -> None:
        if not self.account.authorized:
            with self._lock:
                self._auth_needed = True
            self._set_phase(Phase.AUTH, "требуется вход в аккаунт")
            self._sleep(2)
            return

        with self._lock:
            force_sync = self._force_sync
            force_bump = self._force_bump
            paused = self._paused
            self._force_sync = False
            self._force_bump = False
            stale = (self._last_sync_at or 0) + MIN_SYNC_GAP_SEC < time.time()

        if force_sync or stale:
            self._sync()
            self._backoff = 30.0

        if paused:
            self._set_phase(Phase.PAUSED, "поднятия приостановлены")
            self._sleep(2)
            return

        targets = self._due_resumes(force_bump)
        if targets:
            if self._bump(targets):
                self._sleep(POST_BUMP_DELAY_SEC)
                self._sync()

        self._set_phase(Phase.WAITING, "ждём разрешённого времени")
        pause = self._compute_wait()
        self._arm_wake_timer(pause)
        self._sleep(pause)

    def _on_token_error(self, exc: TokenError) -> None:
        self.log.error(f"Авторизация слетела: {exc}")
        self.account.clear_token()
        self.account.save()
        with self._lock:
            self._auth_needed = True
        self._set_phase(Phase.AUTH, "требуется повторный вход")

    def _on_network_error(self, exc: NetworkError) -> None:
        with self._lock:
            if self._offline_since is None:
                self._offline_since = time.time()
            self._next_action_at = time.time() + self._backoff
            self._wait_span = self._backoff
        self._set_phase(Phase.OFFLINE, "нет связи с сервисом")
        self.log.warn(f"Сеть недоступна, повтор через {int(self._backoff)} с")
        self._sleep(self._backoff)
        self._backoff = min(self._backoff * 1.8, 600.0)
        with self._lock:
            self._force_sync = True
