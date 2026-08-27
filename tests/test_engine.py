# -*- coding: utf-8 -*-
"""Движок целиком — против поддельного api.hh.ru на localhost."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness import Report, quiet_server, sandbox

sandbox()

import huntercli.hh as hh
from huntercli import auth
from huntercli.config import Config
from huntercli.engine import BumpEngine, Phase
from huntercli.logbus import LogBus

PORT = 8797
DEAD_URL = "http://127.0.0.1:9"  # заведомо закрытый порт: соединение отвергается сразу


def _iso(delta_sec: float) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=delta_sec)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+0000")


class FakeHH:
    """Состояние поддельного hh.ru, общее для обработчиков запросов."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.valid_token = "GOOD-TOKEN"
        self.publish_calls: list[str] = []
        self.resumes = {
            "r1": {"id": "r1", "title": "Юрист", "can_publish_or_update": True,
                   "next_publish_at": _iso(-60), "total_views": 100, "new_views": 4,
                   "finished": True, "blocked": False, "status": {"name": "Опубликовано"}},
            "r2": {"id": "r2", "title": "Дизайнер", "can_publish_or_update": True,
                   "next_publish_at": _iso(-30), "total_views": 12, "new_views": 0,
                   "finished": True, "blocked": False, "status": {"name": "Опубликовано"}},
        }
        self.quota_blocked = {"r2"}
        # Обращения к работодателям: две страницы, чтобы проверить перебор.
        # Состояния взяты у настоящего сервиса: response, discard, invitation.
        self.talks_pages = [
            [{"state": {"id": "response"}, "resume": {"id": "r1"}}] * 2
            + [{"state": {"id": "invitation"}, "resume": {"id": "r1"}}]
            + [{"state": {"id": "discard"}, "resume": {"id": "r2"}}],
            [{"state": {"id": "invitation"}, "resume": {"id": "r1"}}]
            + [{"state": {"id": "response"}, "resume": {"id": "нет такого"}}],
        ]
        self.talks_calls: list[int] = []
        self.talks_broken = False


STATE = FakeHH()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code: int, payload: dict | None = None) -> None:
        body = json.dumps(payload or {}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        with STATE.lock:
            return self.headers.get("Authorization") == f"Bearer {STATE.valid_token}"

    def do_GET(self):
        if not self._authorized():
            # Ровно так отвечает настоящий api.hh.ru: 403, а не 401.
            return self._send(403, {"description": "Forbidden",
                                    "errors": [{"value": "bad_authorization",
                                                "type": "oauth"}]})
        if self.path == "/me":
            return self._send(200, {"id": "777", "first_name": "Тест", "last_name": "Тестов"})
        if self.path.startswith("/negotiations"):
            from urllib.parse import parse_qs, urlparse

            page = int((parse_qs(urlparse(self.path).query).get("page") or ["0"])[0])
            with STATE.lock:
                STATE.talks_calls.append(page)
                if STATE.talks_broken:
                    # Не 500: он в RETRYABLE, клиент трижды повторил бы со сном.
                    return self._send(400, {"errors": [{"value": "page",
                                                        "type": "bad_argument"}]})
                pages = STATE.talks_pages
                items = pages[page] if 0 <= page < len(pages) else []
                total = sum(len(chunk) for chunk in pages)
                count = len(pages)
            return self._send(200, {"items": items, "found": total,
                                    "pages": count, "page": page, "per_page": 100})
        if self.path == "/resumes/mine":
            with STATE.lock:
                items = [dict(item) for item in STATE.resumes.values()]
            return self._send(200, {"items": items, "found": len(items)})
        return self._send(404)

    def do_POST(self):
        if not self._authorized():
            # Ровно так отвечает настоящий api.hh.ru: 403, а не 401.
            return self._send(403, {"description": "Forbidden",
                                    "errors": [{"value": "bad_authorization",
                                                "type": "oauth"}]})
        if not self.path.endswith("/publish"):
            return self._send(404)
        resume_id = self.path.split("/")[2]
        with STATE.lock:
            STATE.publish_calls.append(resume_id)
            if resume_id in STATE.quota_blocked:
                return self._send(403, {"errors": [{"type": "bad_argument",
                                                    "value": "quota_exceeded"}]})
            item = STATE.resumes.get(resume_id)
            if item:
                item["can_publish_or_update"] = False
                item["next_publish_at"] = _iso(4 * 3600)
        return self._send(204)


def _wait_for(predicate, seconds: float = 25.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.3)
    return False


def _make_engine(token: str, refresh: str = "RT"):
    """Движок с собственным аккаунтом: конфиг держит его, движок им живёт."""
    log = LogBus(to_file=False)
    account = Config().account
    account.apply_token({"access_token": token, "refresh_token": refresh, "expires_in": 1209599})
    return BumpEngine(account, hh.HHClient(account, log), log), account, log


def run() -> bool:
    report = Report("Движок автопилота")

    server = quiet_server(ThreadingHTTPServer)(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Глобальные настройки модулей обязательно вернуть: иначе следующий тест
    # пойдёт стучаться в наш поддельный (и уже мёртвый) сервер.
    real_api_root, real_refresh = hh.API_ROOT, auth.refresh_token
    hh.API_ROOT = f"http://127.0.0.1:{PORT}"

    try:
        report.section("Обычный цикл: поднятие и отказ по лимиту")
        engine, account, log = _make_engine("GOOD-TOKEN")
        engine.start()
        _wait_for(lambda: len(STATE.publish_calls) >= 2 and engine.snapshot().session_bumps >= 1)
        time.sleep(8)  # контрольная синхронизация после поднятия

        snap = engine.snapshot()
        report.check("оба резюме отправлены на поднятие",
                     set(STATE.publish_calls) == {"r1", "r2"}, f"-> {STATE.publish_calls}")
        report.check("успех посчитан ровно один раз", snap.session_bumps == 1)
        report.check("статистика записана", account.stats.total_bumps == 1)
        report.check("отказ посчитан", account.stats.failed_bumps >= 1)
        report.check("имя аккаунта подтянуто", snap.account == "Тест Тестов", f"-> {snap.account!r}")
        report.check("владелец запомнен — по нему ловятся повторные подключения",
                     account.person_id == "777", f"-> {account.person_id!r}")

        r1 = next(r for r in snap.resumes if r.id == "r1")
        r2 = next(r for r in snap.resumes if r.id == "r2")
        report.check("поднятое уехало на +4 часа",
                     3.9 * 3600 < r1.seconds_to_allowed < 4.1 * 3600,
                     f"-> {r1.seconds_to_allowed / 3600:.2f} ч")
        report.check("к нему добавлен разброс",
                     r1.planned_at - r1.next_publish_at.timestamp() >= 45)
        report.check("причина отказа видна в резюме", "лимит" in r2.problem, f"-> {r2.problem!r}")
        report.check("отказ попал в журнал",
                     any("лимит" in e.text for e in log.tail(50) if e.level == "error"))
        report.check("время следующего действия известно", snap.next_action_at is not None)

        engine.request_sync()
        time.sleep(3)
        report.check("поднятое повторно не трогаем", STATE.publish_calls.count("r1") == 1)
        report.check("после отказа не долбим API каждую минуту",
                     STATE.publish_calls.count("r2") == 1, f"-> {STATE.publish_calls}")
        pause = next(r for r in engine.snapshot().resumes if r.id == "r2").planned_at - time.time()
        report.check("пауза после отказа около 30 минут", 1500 < pause < 1900,
                     f"-> {pause / 60:.0f} мин")

        engine.request_bump()
        time.sleep(3)
        report.check("клавиша B пробивает паузу", STATE.publish_calls.count("r2") == 2)

        report.section("Обращения к работодателям считаются раз в сутки")
        from huntercli import history

        uid = engine.account.uid
        _wait_for(lambda: not history.needs_talks(uid))
        report.check("сводка обращений записана", not history.needs_talks(uid))
        talks_report = history.report(uid)
        report.check("обе страницы разобраны", talks_report.talks == 6,
                     f"-> {talks_report.talks}")
        report.check("приглашения сосчитаны", talks_report.invitations == 2,
                     f"-> {talks_report.invitations}")
        report.check("отклики сосчитаны", talks_report.responses == 3,
                     f"-> {talks_report.responses}")
        report.check("отказы сосчитаны", talks_report.discards == 1,
                     f"-> {talks_report.discards}")
        report.check("страниц запрошено ровно две", STATE.talks_calls == [0, 1],
                     f"-> {STATE.talks_calls}")
        by_resume = {item.title: item.invitations for item in talks_report.resumes}
        report.check("приглашения привязаны к резюме", by_resume.get("Юрист") == 2,
                     f"-> {by_resume}")
        report.check("обращение с чужого резюме в разбивку не попало",
                     sum(by_resume.values()) == 2, f"-> {by_resume}")

        # Второй синхронизации того же дня повторный перебор не нужен.
        before = list(STATE.talks_calls)
        engine.request_sync()
        _wait_for(lambda: engine.snapshot().last_sync_at is not None)
        report.check("за сутки считаем один раз", STATE.talks_calls == before,
                     f"-> {STATE.talks_calls}")

        # Статистика вторична: её поломка не должна ронять движок. Берём
        # отдельный незапущенный движок: у работающего фоновый поток сам
        # ходит за обращениями, и проверка стала бы гонкой.
        with STATE.lock:
            STATE.talks_broken = True
        quiet, _, quiet_log = _make_engine("GOOD-TOKEN")
        quiet._count_talks()  # обязано вернуться само, без исключения
        report.check("неудача подсчёта не выбрасывает исключение", True)
        report.check("о неудаче сказано в журнале",
                     any("посчитать не вышло" in e.text for e in quiet_log.tail(50)),
                     f"-> {[e.text[:40] for e in quiet_log.tail(3)]}")
        report.check("испорченный срез не записан", history.needs_talks(quiet.account.uid))
        with STATE.lock:
            STATE.talks_broken = False

        # А работающий движок тем временем продолжает поднимать.
        with STATE.lock:
            before_calls = len(STATE.publish_calls)
            STATE.resumes["r2"]["can_publish_or_update"] = True
            STATE.resumes["r2"]["next_publish_at"] = _iso(-60)
        engine.request_bump()
        _wait_for(lambda: len(STATE.publish_calls) > before_calls)
        report.check("поднятия идут своим чередом",
                     len(STATE.publish_calls) > before_calls,
                     f"-> было {before_calls}, стало {len(STATE.publish_calls)}")

        report.section("Пауза и выбор резюме")
        report.check("пауза включается", engine.toggle_pause() is True)
        report.check("пауза выключается", engine.toggle_pause() is False)
        verdict = engine.toggle_resume(1)
        report.check("резюме выключается", verdict and "не поднимаем" in verdict, f"-> {verdict}")
        time.sleep(1.5)
        report.check("выключенное не планируется",
                     next(r for r in engine.snapshot().resumes if r.id == "r1").planned_at is None)
        report.check("выбор сохранён в конфиг", account.managed_resumes == ["r2"])
        engine.toggle_resume(1)
        report.check("резюме включается обратно", "r1" in (account.managed_resumes or []))
        report.check("несуществующий номер не ломает", engine.toggle_resume(99) is None)
        engine.stop()
        engine.join()

        report.section("Протухший токен обновляется сам")
        with STATE.lock:
            STATE.valid_token = "NEW-TOKEN"
            STATE.publish_calls.clear()
            STATE.resumes["r1"]["can_publish_or_update"] = True
            STATE.resumes["r1"]["next_publish_at"] = _iso(-10)
            STATE.quota_blocked.clear()

        calls: list[str] = []

        def fake_refresh(token: str) -> dict:
            calls.append(token)
            return {"access_token": "NEW-TOKEN", "refresh_token": "RT2", "expires_in": 1209599}

        auth.refresh_token = fake_refresh
        engine2, account2, _ = _make_engine("STALE")
        engine2.start()
        _wait_for(lambda: bool(calls), 20)
        time.sleep(3)
        report.check("обновление токена запрошено", calls == ["RT"], f"-> {calls}")
        report.check("новый токен сохранён", account2.access_token == "NEW-TOKEN")
        report.check("работа продолжилась без участия человека", "r1" in STATE.publish_calls)
        report.check("повторный вход не потребовался", not engine2.auth_needed)
        engine2.stop()
        engine2.join()

        report.section("Обновить не вышло — просим войти заново")

        def dead_refresh(token: str) -> dict:
            raise auth.AuthError("refresh_token отозван")

        auth.refresh_token = dead_refresh
        engine3, account3, _ = _make_engine("DEAD")
        engine3.start()
        _wait_for(lambda: engine3.auth_needed, 20)
        report.check("движок попросил повторный вход", engine3.auth_needed)
        report.check("негодный токен стёрт", not account3.access_token)
        engine3.stop()
        engine3.join()

        report.section("Сеть пропала")
        server.shutdown()
        server.server_close()
        hh.API_ROOT = DEAD_URL
        engine4, _, _ = _make_engine("GOOD-TOKEN", refresh="")
        engine4.start()
        _wait_for(lambda: engine4.snapshot().offline_since is not None, 30)
        report.check("состояние «нет сети» выставлено",
                     engine4.snapshot().offline_since is not None)
        report.check("движок жив и продолжает попытки", engine4._thread.is_alive())
        report.check("вход заново не требует", not engine4.auth_needed)
        engine4.stop()
        engine4.join()
    finally:
        hh.API_ROOT, auth.refresh_token = real_api_root, real_refresh
        try:
            server.server_close()
        except Exception:
            pass

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
