# -*- coding: utf-8 -*-
"""Поток входа через окно WebView — без настоящего окна.

Настоящее окно в тестах не открыть, поэтому подменяем модуль `webview`
заглушкой. Но аргументы, с которыми мы его зовём, сверяются с НАСТОЯЩЕЙ
сигнатурой pywebview: именно из-за расхождения (`private_mode` у
`create_window`, где его нет) приложение падало при первом же входе.
"""

from __future__ import annotations

import inspect
import sys
import threading
import time
import types
from http.cookies import SimpleCookie

from harness import Report, sandbox

sandbox()

from huntercli import auth

#: Настоящие сигнатуры pywebview — по ним проверяем свои вызовы.
try:
    import webview as _real_webview

    REAL_SIGNATURES = {
        "create_window": inspect.signature(_real_webview.create_window),
        "start": inspect.signature(_real_webview.start),
    }
except Exception:  # pywebview не установлен — проверить нечего
    REAL_SIGNATURES = {}


def _jar(**pairs) -> list[SimpleCookie]:
    """Куки в том виде, в каком их отдаёт pywebview: список SimpleCookie."""
    cookies = []
    for name, value in pairs.items():
        cookie = SimpleCookie()
        cookie[name] = value
        cookies.append(cookie)
    return cookies


class FakeWindow:
    """Окно, которое по шагам «логинится»: сначала аноним, потом соискатель."""

    def __init__(self, steps: list[list[SimpleCookie]], url: str = "https://hh.ru/oauth/authorize"):
        self.steps = steps
        self.url = url
        self.destroyed = False
        self.evaluated: list[str] = []
        self._step = 0

    def get_current_url(self) -> str:
        return self.url

    def get_cookies(self):
        step = self.steps[min(self._step, len(self.steps) - 1)]
        self._step += 1
        return step

    def evaluate_js(self, code: str) -> str:
        self.evaluated.append(code)
        return "FakeBrowser/1.0"

    def destroy(self) -> None:
        self.destroyed = True


class FakeWindowThatBreaks(FakeWindow):
    """Окно, на котором наблюдатель падает — окно всё равно обязано закрыться."""

    def get_cookies(self):
        raise RuntimeError("WebView2 отвалился")

    def get_current_url(self):
        raise RuntimeError("WebView2 отвалился")


def _install_fake_webview(window: FakeWindow, calls: dict) -> None:
    module = types.ModuleType("webview")

    def create_window(*args, **kwargs):
        calls["create_window"] = (args, kwargs)
        return window

    def start(*args, **kwargs):
        calls["start"] = (args, kwargs)
        # Настоящий start() держит поток, пока окно не закроют.
        deadline = time.time() + 30
        while not window.destroyed and time.time() < deadline:
            time.sleep(0.05)

    module.create_window = create_window
    module.start = start
    sys.modules["webview"] = module


def run() -> bool:
    report = Report("Окно входа (WebView)")

    saved_module = sys.modules.get("webview")
    saved_complete = auth.complete_with_cookies
    saved_exchange = auth.exchange_code

    try:
        report.section("Успешный вход")
        seen_jars: list[dict] = []

        def fake_complete(jar, user_agent=None, trace=None):
            seen_jars.append(jar)
            return "CODE-FROM-COOKIES" if jar.get("hhrole") == "applicant" else None

        def fake_exchange(code):
            return {"access_token": f"TOKEN-FOR-{code}", "refresh_token": "R",
                    "expires_in": 1209599}

        auth.complete_with_cookies = fake_complete
        auth.exchange_code = fake_exchange

        window = FakeWindow([
            _jar(hhtoken="anon", hhrole="anonymous"),   # ещё не вошли
            _jar(hhtoken="live", hhrole="applicant", hhuid="42"),  # вошли
        ])
        calls: dict = {}
        _install_fake_webview(window, calls)

        messages: list[str] = []
        payload = auth.run_webview_flow(messages.append, timeout_sec=20)

        report.check("токен получен", payload and payload.get("access_token")
                     == "TOKEN-FOR-CODE-FROM-COOKIES", f"-> {payload}")
        report.check("окно закрыто после успеха", window.destroyed)
        report.check("анонимную стадию пропустили, не дёргая обмен",
                     all(j.get("hhrole") == "applicant" for j in seen_jars),
                     f"-> {[j.get('hhrole') for j in seen_jars]}")
        report.check("User-Agent окна прочитан для запроса",
                     any("userAgent" in code for code in window.evaluated))
        report.check("пользователю сообщили о ходе дела", len(messages) >= 1,
                     f"-> {messages}")

        report.section("Аргументы вызовов сверены с настоящим pywebview")
        if not REAL_SIGNATURES:
            report.check("pywebview установлен — есть с чем сверять", False,
                         "(модуль не найден, проверка пропущена)")
        else:
            for name, signature in REAL_SIGNATURES.items():
                args, kwargs = calls.get(name, ((), {}))
                try:
                    signature.bind(*args, **kwargs)
                    report.check(f"{name}(): аргументы приняты настоящей сигнатурой", True,
                                 f"-> {sorted(kwargs)}")
                except TypeError as exc:
                    report.check(f"{name}(): аргументы приняты настоящей сигнатурой", False,
                                 f"-> {exc}")
            report.check("сессия окна сохраняется на диск (вход в один клик)",
                         calls.get("start", ((), {}))[1].get("private_mode") is False)

        report.section("Пользователь закрыл окно сам")
        window = FakeWindow([_jar(hhtoken="anon", hhrole="anonymous")])
        _install_fake_webview(window, {})
        closed_early = threading.Timer(2.0, lambda: setattr(window, "destroyed", True))
        closed_early.start()
        report.check("вход не завершён -> None, без исключения",
                     auth.run_webview_flow(lambda _: None, timeout_sec=4) is None)
        closed_early.cancel()

        report.section("Окно перестало отвечать")
        broken = FakeWindowThatBreaks([])
        _install_fake_webview(broken, {})
        saved_limit = auth.MAX_WINDOW_ERRORS
        auth.MAX_WINDOW_ERRORS = 3  # чтобы не ждать полный лимит в тесте
        try:
            report.raises("сдаёмся с понятной ошибкой, а не крутимся до таймаута",
                          auth.AuthError, auth.run_webview_flow, lambda _: None,
                          timeout_sec=60)
        finally:
            auth.MAX_WINDOW_ERRORS = saved_limit
        report.check("окно закрыто, а не висит", broken.destroyed)

        report.section("Одиночный сбой окна прощаем")
        flaky = FakeWindow([
            _jar(hhtoken="anon", hhrole="anonymous"),
            _jar(hhtoken="live", hhrole="applicant"),
        ])
        original_get_cookies = flaky.get_cookies
        hiccups = {"left": 2}

        def sometimes_fails():
            if hiccups["left"] > 0:
                hiccups["left"] -= 1
                raise RuntimeError("разовый сбой WebView2")
            return original_get_cookies()

        flaky.get_cookies = sometimes_fails
        _install_fake_webview(flaky, {})
        payload = auth.run_webview_flow(lambda _: None, timeout_sec=30)
        report.check("после пары сбоев вход всё равно доходит до конца",
                     payload and payload.get("access_token"), f"-> {payload}")

        report.section("pywebview недоступен")
        sys.modules["webview"] = None  # type: ignore[assignment]
        available, _ = auth.webview_available()
        report.check("отсутствие окна определяется заранее", not available)
        report.raises("вход через окно даёт понятную ошибку", auth.AuthError,
                      auth.run_webview_flow, lambda _: None)
    finally:
        auth.complete_with_cookies = saved_complete
        auth.exchange_code = saved_exchange
        if saved_module is not None:
            sys.modules["webview"] = saved_module
        else:
            sys.modules.pop("webview", None)

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
