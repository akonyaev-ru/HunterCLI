# -*- coding: utf-8 -*-
"""Доигрывание OAuth по кукам — на всех формах ответа, которые даёт hh.ru."""

from __future__ import annotations

import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness import Report, quiet_server, sandbox

sandbox()

from huntercli import auth

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"

#: Каким сценарием отвечает поддельный hh.ru прямо сейчас.
MODE = {"name": "anonymous"}
SEEN_COOKIES: list[str | None] = []

APPROVE_PAGE = """<!doctype html><html><body>
<div class="oauth-app"><h1>Доступ к аккаунту</h1>
<form action="/oauth/approve?client_id=CID" method="POST" class="approve">
  <input type="hidden" name="_xsrf" value="XSRF-FROM-PAGE">
  <input type="hidden" name="client_id" value="CID">
  <input type="submit" name="approve" value="Продолжить">
</form></div></body></html>"""

SPA_PAGE = """<!doctype html><html><body><div id="HH-React-Root"></div></body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _reply(self, code, body=b"", location=None):
        self.send_response(code)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        SEEN_COOKIES.append(self.headers.get("Cookie"))
        if not self.path.startswith("/oauth/authorize"):
            return self._reply(404)
        mode = MODE["name"]
        if mode == "anonymous":
            return self._reply(302, location="/account/login?backurl=%2Foauth%2Fauthorize")
        if mode == "instant":
            return self._reply(302, location="hh-android://oauth/code?code=INSTANT-CODE")
        if mode == "hop":
            return self._reply(302, location="/oauth/authorize?client_id=CID&hop=1")
        if mode == "form":
            return self._reply(200, APPROVE_PAGE.encode("utf-8"))
        if mode == "spa":
            return self._reply(200, SPA_PAGE.encode("utf-8"))
        return self._reply(500)

    def do_POST(self):
        SEEN_COOKIES.append(self.headers.get("Cookie"))
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
        if not self.path.startswith("/oauth/approve"):
            return self._reply(404)
        if "_xsrf=XSRF-FROM-PAGE" in body and "approve=" in body:
            return self._reply(302, location="hh-android://oauth/code?code=FORM-CODE")
        if "_xsrf=XSRF-FROM-COOKIE" in body:
            if "client_id=" not in body:
                return self._reply(400, b"need client_id")
            return self._reply(302, location="hh-android://oauth/code?code=SPA-CODE")
        return self._reply(403, b"bad xsrf")


JAR_ANON = {"hhtoken": "anon-token", "hhrole": "anonymous"}
JAR_USER = {"hhtoken": "user-token", "hhrole": "applicant", "hhuid": "42",
            "crypted_hhuid": "zzz", "_xsrf": "XSRF-FROM-COOKIE", "__ddg1": "ddg"}


def run() -> bool:
    report = Report("Авторизация")

    server = quiet_server(ThreadingHTTPServer)(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    saved = auth.AUTHORIZE_URL, auth.APPROVE_URL, auth.TOKEN_URL
    auth.AUTHORIZE_URL = f"{BASE}/oauth/authorize"
    auth.APPROVE_URL = f"{BASE}/oauth/approve"

    try:
        report.section("Сценарии страницы авторизации")
        MODE["name"] = "anonymous"
        report.check("не вошли — кода нет, ждём дальше",
                     auth.complete_with_cookies(JAR_ANON) is None)

        MODE["name"] = "instant"
        SEEN_COOKIES.clear()
        report.check("доступ уже выдан — код сразу в Location",
                     auth.complete_with_cookies(JAR_USER) == "INSTANT-CODE")
        report.check("куки сессии действительно уходят на сервер",
                     SEEN_COOKIES and "user-token" in (SEEN_COOKIES[0] or ""))

        MODE["name"] = "form"
        report.check("страница с формой подтверждения разобрана",
                     auth.complete_with_cookies(JAR_USER) == "FORM-CODE")

        MODE["name"] = "spa"
        report.check("страница без формы: _xsrf берём из куки",
                     auth.complete_with_cookies(JAR_USER) == "SPA-CODE")
        report.check("без _xsrf честно возвращаем None",
                     auth.complete_with_cookies({"hhtoken": "t"}) is None)

        MODE["name"] = "hop"
        report.check("промежуточный редирект внутри oauth не обрывает вход",
                     auth.complete_with_cookies(JAR_USER) == "SPA-CODE")

        report.section("Устойчивость к плохим данным")
        MODE["name"] = "spa"
        mixed = dict(JAR_USER, display="полный", regions="Москва")
        report.check("куки с кириллицей отброшены, поток жив",
                     auth.complete_with_cookies(mixed) == "SPA-CODE")
        report.check("_http_safe режет нелатиницу",
                     auth._http_safe({"a": "ok", "b": "кир"}) == {"a": "ok"})

        report.section("Признак выполненного входа")
        report.check("аноним не считается вошедшим", not auth._logged_in(JAR_ANON))
        report.check("соискатель считается вошедшим", auth._logged_in(JAR_USER))
        report.check("без hhtoken не вошёл", not auth._logged_in({"hhrole": "applicant"}))

        report.section("Разбор куки из pywebview")
        first, second = SimpleCookie(), SimpleCookie()
        first["hhtoken"] = "AAA"
        second["hhrole"] = "applicant"
        report.check("SimpleCookie раскладывается в словарь",
                     auth._cookies_to_dict([first, second, None, "мусор"])
                     == {"hhtoken": "AAA", "hhrole": "applicant"})
        report.check("пустой список не ломает", auth._cookies_to_dict([]) == {})

        report.section("Передача кода из системного браузера")
        auth.clear_handoff()
        report.check("пусто — значит None", auth.read_handoff() is None)
        auth.write_handoff("hh-android://oauth/code?code=FROM-BROWSER")
        report.check("код прочитан обратно", auth.read_handoff() == "FROM-BROWSER")
        report.check("устаревшая передача игнорируется", auth.read_handoff(max_age_sec=-1) is None)
        auth.clear_handoff()
        report.check("после очистки снова пусто", auth.read_handoff() is None)

        report.section("Ошибки обмена токена")
        auth.TOKEN_URL = f"{BASE}/nope"
        report.raises("ответ не 200 -> AuthError", auth.AuthError, auth.exchange_code, "X")
        auth.TOKEN_URL = "http://127.0.0.1:9/token"
        report.raises("сеть недоступна -> AuthError", auth.AuthError, auth.exchange_code, "X")
    finally:
        auth.AUTHORIZE_URL, auth.APPROVE_URL, auth.TOKEN_URL = saved
        server.shutdown()
        server.server_close()

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
