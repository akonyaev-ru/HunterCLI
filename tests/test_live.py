# -*- coding: utf-8 -*-
"""Проверки против настоящего hh.ru. Вход в аккаунт не требуется.

Запускается только с ключом --live: python tests\\run_all.py --live
"""

from __future__ import annotations

import requests

from harness import Report, sandbox

sandbox()

from huntercli import auth, hh
from huntercli.config import Config
from huntercli.logbus import LogBus


def run() -> bool:
    report = Report("Проверки против настоящего hh.ru")

    report.section("Страница авторизации")
    try:
        response = requests.get(auth.build_auth_url(), allow_redirects=False, timeout=20,
                                headers={"User-Agent": auth.BROWSER_UA})
    except requests.RequestException as exc:
        report.check("hh.ru доступен", False, f"-> {exc}")
        return report.summary()

    report.check("hh.ru отвечает на ссылку входа", response.status_code in (200, 302),
                 f"-> {response.status_code}")
    report.check("незалогиненного уводит на вход",
                 auth._is_login_redirect(response.headers.get("Location", "")),
                 f"-> {response.headers.get('Location', '')[:70]}")

    report.section("Явный redirect_uri по-прежнему запрещён")
    forbidden = requests.get(
        auth.build_auth_url() + "&redirect_uri=hh-android%3A%2F%2Foauth%2Fcode",
        allow_redirects=False, timeout=20, headers={"User-Agent": auth.BROWSER_UA},
    )
    report.check("hh.ru отвергает переданный redirect_uri", forbidden.status_code == 400,
                 f"-> {forbidden.status_code} (если стало 302 — можно упростить вход)")

    report.section("Доигрывание по кукам без входа")
    report.check("анонимная сессия распознана, падения нет",
                 auth.complete_with_cookies({"hhtoken": "not-real", "hhrole": "anonymous"}) is None)

    report.section("Как именно api.hh.ru отказывает по токену")
    bad = requests.get(
        "https://api.hh.ru/resumes/mine", timeout=20,
        headers={"User-Agent": hh.CLIENT_UA, "HH-User-Agent": hh.CLIENT_UA,
                 "Authorization": "Bearer DEFINITELY-NOT-VALID-0123456789"},
    )
    report.check("на плохой токен приходит 403 (не 401)", bad.status_code == 403,
                 f"-> {bad.status_code}")
    kinds = hh.error_kinds(bad.json() if bad.content else None)
    report.check("ответ распознаётся как проблема токена -> сработает продление",
                 bool(kinds & hh.AUTH_ERROR_MARKERS), f"-> {kinds}")

    report.section("api.hh.ru с негодным токеном")
    log = LogBus(to_file=False)
    cfg = Config()
    cfg.apply_token({"access_token": "DEFINITELY-NOT-VALID-0123456789", "expires_in": 1209599})
    report.raises("негодный токен -> TokenError", hh.TokenError, hh.HHClient(cfg, log).resumes)

    report.section("Обмен неверного кода")
    report.raises("неверный код -> AuthError", auth.AuthError,
                  auth.exchange_code, "DEFINITELY-NOT-A-VALID-CODE")

    return report.summary()


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
