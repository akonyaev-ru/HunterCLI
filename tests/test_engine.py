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
        # Коллекция active: та, где живут приглашения и кандидаты на уборку.
        # У настоящего сервиса в ней три десятка обращений против четырёх
        # сотен во всех, и запрашивается она одной страницей.
        day = 86400
        stamp = lambda ago: time.strftime(
            "%Y-%m-%dT%H:%M:%S+0300", time.localtime(time.time() - ago * day))
        self.active_items = [
            {"id": "n1", "state": {"id": "invitation"}, "read": False,
             "updated_at": stamp(1), "vacancy": {"name": "Юрист",
                                                 "employer": {"name": "Ромашка"}}},
            {"id": "n2", "state": {"id": "invitation"}, "read": True,
             "updated_at": stamp(9), "vacancy": {"name": "Юрист-2",
                                                 "employer": {"name": "Ромашка"}}},
            {"id": "n3", "state": {"id": "discard"}, "read": True,
             "updated_at": stamp(2), "vacancy": {"name": "Отказали",
                                                 "employer": {"name": "Одуванчик"}}},
            {"id": "n4", "state": {"id": "response"}, "read": True,
             "updated_at": stamp(400), "vacancy": {"name": "Висяк",
                                                   "employer": {"name": "Лопух"}}},
            {"id": "n5", "state": {"id": "response"}, "read": True,
             "updated_at": stamp(3), "vacancy": {"name": "Свежий",
                                                 "employer": {"name": "Клевер"}}},
        ]
        # Подбор вакансий под резюме и справочник валют.
        self.similar_calls: list[str] = []
        self.dict_calls = 0
        self.vacancies = [
            {"salary": {"from": 100000, "to": 140000, "currency": "RUR", "gross": False}},
            {"salary": {"from": 160000, "to": None, "currency": "RUR", "gross": False}},
            {"salary": {"from": 200000, "to": 240000, "currency": "RUR", "gross": True}},
            {"salary": {"from": 2000, "to": None, "currency": "EUR", "gross": False}},
            {"salary": None},
            {"salary": None},
        ]
        self.active_calls = 0
        self.hidden: list[str] = []
        self.hide_code = 204


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

            query = parse_qs(urlparse(self.path).query)
            if (query.get("status") or [""])[0] == "active":
                with STATE.lock:
                    STATE.active_calls += 1
                    items = [dict(it) for it in STATE.active_items
                             if it["id"] not in STATE.hidden]
                return self._send(200, {"items": items, "found": len(items),
                                        "pages": 1, "page": 0, "per_page": 100})
            page = int((query.get("page") or ["0"])[0])
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
        if self.path == "/dictionaries":
            with STATE.lock:
                STATE.dict_calls += 1
            return self._send(200, {"currency": [
                {"code": "RUR", "rate": 1}, {"code": "EUR", "rate": 0.00997}]})
        if "/similar_vacancies" in self.path:
            from urllib.parse import urlparse
            rid = urlparse(self.path).path.split("/")[2]
            with STATE.lock:
                STATE.similar_calls.append(rid)
                items = [dict(v) for v in STATE.vacancies]
            return self._send(200, {"items": items, "found": len(items),
                                    "pages": 1, "page": 0, "per_page": 100})
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

    def do_DELETE(self):
        if not self._authorized():
            return self._send(403, {"description": "Forbidden",
                                    "errors": [{"value": "bad_authorization",
                                                "type": "oauth"}]})
        # Скрытие живёт только по коллекции active — проверено пробой у
        # настоящего сервиса: другие имена коллекций DELETE не принимают.
        if not self.path.startswith("/negotiations/active/"):
            return self._send(405, {"errors": [{"value": "", "type": "method_not_allowed"}]})
        talk_id = self.path.rsplit("/", 1)[-1]
        with STATE.lock:
            if STATE.hide_code != 204:
                return self._send(STATE.hide_code,
                                  {"errors": [{"value": "collection",
                                               "type": "bad_argument"}]})
            STATE.hidden.append(talk_id)
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

        report.section("Приглашение замечается сразу")
        # В коллекции active два приглашения, но прочитанное человек уже видел.
        snap = engine.snapshot()
        report.check("непрочитанное приглашение сосчитано",
                     snap.invitations_pending == 1, f"-> {snap.invitations_pending}")
        report.check("прочитанное приглашение не считается",
                     snap.invitations_pending < 2, f"-> {snap.invitations_pending}")
        report.check("про приглашение сказано в журнале",
                     any("Приглашение!" in e.text for e in engine.log.tail(200)))
        report.check("активная коллекция берётся одним запросом, а не перебором",
                     STATE.active_calls >= 1 and STATE.talks_calls == [0, 1],
                     f"-> active={STATE.active_calls}, полный={STATE.talks_calls}")

        report.section("Мёртвые обращения убираются")
        _wait_for(lambda: len(STATE.hidden) >= 2)
        report.check("отказ убран", "n3" in STATE.hidden, f"-> {STATE.hidden}")
        report.check("висяк старше срока убран", "n4" in STATE.hidden, f"-> {STATE.hidden}")
        report.check("свежий отклик не тронут", "n5" not in STATE.hidden, f"-> {STATE.hidden}")
        # Главное правило: приглашения не скрываются ни при каких настройках.
        report.check("непрочитанное приглашение не тронуто",
                     "n1" not in STATE.hidden, f"-> {STATE.hidden}")
        report.check("прочитанное приглашение тоже не тронуто",
                     "n2" not in STATE.hidden, f"-> {STATE.hidden}")
        report.check("каждое скрытие названо в журнале",
                     sum("Убрано из активных" in e.text for e in engine.log.tail(200)) == 2,
                     f"-> {[e.text for e in engine.log.tail(200) if 'Убрано' in e.text]}")
        report.check("причина скрытия указана",
                     any("отказ" in e.text for e in engine.log.tail(200))
                     and any("без ответа" in e.text for e in engine.log.tail(200)))
        report.check("приглашение осталось в заголовке после уборки",
                     engine.snapshot().invitations_pending == 1)

        before_hidden = list(STATE.hidden)
        engine.request_sync()
        time.sleep(3)
        report.check("за сутки убираем один раз", STATE.hidden == before_hidden,
                     f"-> {STATE.hidden}")
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

        report.section("Зарплаты по профилю считаются раз в сутки")
        from huntercli import history as _h

        _wait_for(lambda: STATE.similar_calls != [])
        rep = _h.report(engine.account.uid)
        report.check("сводка по зарплатам записана", rep.has_salary,
                     f"-> медиана {rep.salary_median}")
        # 100–140 -> 120000; 160000; 200–240 до вычета -> 191400; 2000 EUR -> ~200600.
        report.check("медиана посчитана", 170000 <= rep.salary_median <= 180000,
                     f"-> {rep.salary_median}")
        # Доля указавших — часть ответа: две вакансии из шести без зарплаты.
        # Резюме под автопилотом два, выборка складывается по обоим: 12 и 8.
        report.check("просмотрено больше, чем с зарплатой",
                     rep.salary_count == 8 and rep.salary_total == 12,
                     f"-> {rep.salary_count} из {rep.salary_total}")
        report.check("курсы взяты у сервиса", STATE.dict_calls >= 1)
        # Выключенное резюме владельца не интересует, а стоит три запроса.
        report.check("считаем только по резюме под автопилотом",
                     set(STATE.similar_calls) <= {"r1", "r2"},
                     f"-> {STATE.similar_calls}")
        report.check("про зарплаты сказано в журнале",
                     any("Зарплаты по профилю" in e.text for e in engine.log.tail(200)))

        before_calls = list(STATE.similar_calls)
        engine.request_sync()
        time.sleep(3)
        report.check("за сутки считаем один раз", STATE.similar_calls == before_calls,
                     f"-> {STATE.similar_calls}")

        report.section("Страховки уборки")
        # Выключатель: скрытие необратимо, поэтому передумать можно только
        # заранее — и способ обязан работать без пересборки.
        STATE.hidden.clear()
        engine5, account5, _ = _make_engine("GOOD-TOKEN")
        account5.settings.cleanup_enabled = False
        engine5.start()
        _wait_for(lambda: engine5.snapshot().invitations_pending == 1, 30)
        report.check("с выключенной уборкой приглашения всё равно видны",
                     engine5.snapshot().invitations_pending == 1)
        report.check("с выключенной уборкой ничего не скрыто", STATE.hidden == [],
                     f"-> {STATE.hidden}")
        before_similar = list(STATE.similar_calls)
        account5.settings.salary_enabled = False
        engine5.request_sync()
        time.sleep(3)
        report.check("выключенные зарплаты не запрашиваются",
                     STATE.similar_calls == before_similar,
                     f"-> {len(STATE.similar_calls)} против {len(before_similar)}")
        engine5.stop()
        engine5.join()

        # Отказ по самой операции (не тот метод, не та коллекция) означает, что
        # сломано всё: программа обязана перестать долбить, а не ходить по кругу.
        STATE.hidden.clear()
        STATE.hide_code = 400
        engine6, account6, log6 = _make_engine("GOOD-TOKEN")
        engine6.start()
        _wait_for(lambda: engine6._cleanup_broken, 30)
        report.check("сломанная уборка отключает себя", engine6._cleanup_broken)
        report.check("и говорит об этом один раз",
                     sum("Уборка отключена" in e.text for e in log6.tail(200)) == 1,
                     f"-> {[e.text for e in log6.tail(200) if 'Уборка' in e.text]}")
        report.check("ничего не скрыто при поломке", STATE.hidden == [], f"-> {STATE.hidden}")
        engine6.stop()
        engine6.join()
        STATE.hide_code = 204

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
