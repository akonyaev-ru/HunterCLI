"""OAuth-авторизация на hh.ru.

Почему всё устроено именно так
------------------------------
1. hh.ru отвергает ЛЮБОЙ явный `redirect_uri` для этого client_id (400,
   «Некорректный параметр redirect_uri») — включая тот, что зашит в приложении.
   Поэтому параметр не передаётся вовсе, сервер сам подставляет свой.
2. Зарегистрированный redirect — кастомная схема `hh-android://oauth/code`.
   WebView2 не просто отказывается по ней переходить, он ещё и НЕ отдаёт наружу
   тот 302-ответ, в заголовке `Location` которого лежит код: события
   `WebResourceResponseReceived` и `NavigationStarting` для него не срабатывают,
   `get_current_url()` остаётся на старом адресе. Именно поэтому наивный вариант
   («ждём, пока в адресной строке появится hh-android://») навсегда зависал.
3. Зато `window.get_cookies()` в WebView2 отдаёт куки вместе с HttpOnly —
   в том числе `hhtoken`, который недоступен из `document.cookie`.

Отсюда рабочая схема: пользователь логинится в окне WebView, мы снимаем куки
сессии и доигрываем OAuth уже в Python через `requests`, где `Location`
у 302-ответа читается свободно.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from typing import Any, Callable, Iterable
from urllib.parse import urljoin

import requests

from . import paths

CLIENT_ID = "HIOMIAS39CA9DICTA7JIO64LQKQJF5AGIK74G9ITJKLNEDAOH5FHS5G1JI7FOEGD"
CLIENT_SECRET = "V9M870DE342BGHFRUJ5FTCGCUA1482AN0DI8C5TFI9ULMA89H10N60NOP8I4JMVS"

AUTHORIZE_URL = "https://hh.ru/oauth/authorize"
TOKEN_URL = "https://hh.ru/oauth/token"
APPROVE_URL = "https://hh.ru/oauth/approve"
REDIRECT_SCHEME = "hh-android://"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

StatusFn = Callable[[str], None]


class AuthError(Exception):
    """Не удалось получить или обновить токен."""


# --------------------------------------------------------------------- URL


def build_auth_url() -> str:
    """Ссылка на страницу авторизации.

    `redirect_uri` намеренно НЕ передаётся — см. модульный docstring.
    """
    return (
        f"{AUTHORIZE_URL}?response_type=code&client_id={CLIENT_ID}"
        "&skip_choose_account=true"
    )


_CODE_RE = re.compile(r"[?&#]code=([^&\s\"']+)")


def extract_code(text: str | None) -> str | None:
    """Достать `code` из URL (или из строки, которую вставил пользователь)."""
    if not text:
        return None
    text = text.strip().strip('"').strip("'")
    match = _CODE_RE.search(text)
    if match:
        return match.group(1)
    # Пользователь мог скопировать только сам код.
    if re.fullmatch(r"[A-Za-z0-9_\-]{8,}", text) and not text.lower().startswith("http"):
        return text
    return None


# ------------------------------------------------------------ обмен токена


def _post_token(payload: dict[str, str]) -> dict[str, Any]:
    try:
        response = requests.post(
            TOKEN_URL,
            data=payload,
            headers={"User-Agent": BROWSER_UA},
            timeout=25,
        )
    except requests.RequestException as exc:
        raise AuthError(f"нет связи с сервисом: {exc}") from exc

    if response.status_code != 200:
        detail = response.text.strip()
        try:
            body = response.json()
            detail = body.get("error_description") or body.get("error") or detail
        except ValueError:
            pass
        raise AuthError(f"сервис отклонил запрос ({response.status_code}): {detail[:200]}")

    try:
        data = response.json()
    except ValueError as exc:
        raise AuthError("в ответ на запрос токена пришёл не-JSON") from exc

    if not data.get("access_token"):
        raise AuthError("в ответе сервиса нет access_token")
    return data


def exchange_code(code: str) -> dict[str, Any]:
    """Обменять authorization code на токен."""
    return _post_token(
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
        }
    )


def refresh_token(token: str) -> dict[str, Any]:
    """Обновить токен по refresh_token.

    hh.ru ждёт запрос вообще без client_id/secret, но на некоторых аккаунтах
    принимает и с ними — пробуем оба варианта, прежде чем сдаться.
    """
    try:
        return _post_token({"grant_type": "refresh_token", "refresh_token": token})
    except AuthError:
        return _post_token(
            {
                "grant_type": "refresh_token",
                "refresh_token": token,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            }
        )


# ------------------------------------------- доигрывание OAuth по кукам


_FORM_RE = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S | re.I)
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']")


def _attrs(fragment: str) -> dict[str, str]:
    return {k.lower(): v for k, v in _ATTR_RE.findall(fragment)}


def _find_approve_form(html: str) -> tuple[str, dict[str, str]] | None:
    """Найти в HTML форму подтверждения доступа и её скрытые поля."""
    for raw_attrs, body in _FORM_RE.findall(html):
        attrs = _attrs(raw_attrs)
        action = attrs.get("action", "")
        if "oauth" not in action and "approve" not in action:
            continue
        fields: dict[str, str] = {}
        for tag in _INPUT_RE.findall(body):
            item = _attrs(tag)
            name = item.get("name")
            if not name:
                continue
            if item.get("type", "").lower() in {"button", "reset"}:
                continue
            fields[name] = item.get("value", "")
        return action, fields
    return None


def _absolute(url: str) -> str:
    """Достроить относительный адрес относительно страницы авторизации."""
    return urljoin(AUTHORIZE_URL, url)


#: Куда hh.ru уводит неавторизованного. Проверять на подстроку «auth» нельзя:
#: она есть в каждом адресе потока (oauth, authorize).
_LOGIN_MARKERS = ("/account/login", "/account/signup", "/auth/login", "/login?")


def _is_login_redirect(location: str) -> bool:
    lowered = (location or "").lower()
    return any(marker in lowered for marker in _LOGIN_MARKERS)


def _code_from_response(response: requests.Response) -> str | None:
    location = response.headers.get("Location", "")
    code = extract_code(location)
    if code:
        return code
    return extract_code(response.url)


def _http_safe(jar: dict[str, str]) -> dict[str, str]:
    """Отсеять куки, которые нельзя положить в HTTP-заголовок.

    Заголовки кодируются latin-1. Нужные нам куки (hhtoken, _xsrf, hhuid) —
    всегда ASCII, а вот всякие пользовательские настройки могут оказаться и с
    кириллицей. Без этой чистки requests падает UnicodeEncodeError.
    """
    clean: dict[str, str] = {}
    for name, value in jar.items():
        try:
            name.encode("latin-1")
            str(value).encode("latin-1")
        except (UnicodeEncodeError, AttributeError):
            continue
        clean[name] = value
    return clean


def complete_with_cookies(
    jar: dict[str, str],
    user_agent: str | None = None,
    trace: Callable[[str], None] | None = None,
) -> str | None:
    """По кукам залогиненной сессии получить authorization code.

    Возвращает код или None, если сессия ещё не авторизована (тогда имеет смысл
    подождать и попробовать снова).
    """

    def note(message: str) -> None:
        if trace:
            trace(message)

    jar = _http_safe(jar)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent or BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9",
        }
    )
    # Без явного домена: куки уедут на любой хост, куда мы сами постучимся,
    # а стучимся мы только на hh.ru.
    session.cookies.update(jar)

    url = build_auth_url()
    try:
        first = session.get(url, allow_redirects=False, timeout=25)
    except Exception as exc:
        note(f"запрос авторизации не прошёл: {exc}")
        return None

    code = _code_from_response(first)
    if code:
        return code

    # hh.ru может увести через один-два промежуточных редиректа внутри потока,
    # прежде чем показать страницу подтверждения. Идём по ним до упора.
    for _ in range(4):
        if first.status_code not in (301, 302, 303, 307, 308):
            break
        location = first.headers.get("Location", "")
        if _is_login_redirect(location):
            note("сессия ещё не авторизована — ждём вход")
            return None
        try:
            first = session.get(_absolute(location), allow_redirects=False, timeout=25)
        except Exception as exc:
            note(f"переход по редиректу не удался: {exc}")
            return None
        code = _code_from_response(first)
        if code:
            return code

    html = first.text or ""
    if _is_login_redirect(first.url) or ("account/login" in html and "oauth" not in html):
        note("сессия ещё не авторизована — ждём вход")
        return None

    xsrf = jar.get("_xsrf", "")
    headers = {"Referer": url}
    if xsrf:
        headers["X-Xsrftoken"] = xsrf

    # Вариант 1: разобрать реальную форму подтверждения со страницы.
    form = _find_approve_form(html)
    if form:
        action, fields = form
        fields.setdefault("_xsrf", xsrf)
        note("нашли форму подтверждения доступа")
        try:
            approved = session.post(
                _absolute(action),
                data=fields,
                headers=headers,
                allow_redirects=False,
                timeout=25,
            )
            code = _code_from_response(approved)
            if code:
                return code
        except Exception as exc:
            note(f"подтверждение не прошло: {exc}")

    # Вариант 2: страница отрисована на клиенте — бьём по /oauth/approve сами.
    if xsrf:
        note("пробуем подтвердить доступ напрямую")
        for payload in (
            {"_xsrf": xsrf, "client_id": CLIENT_ID, "response_type": "code"},
            {"_xsrf": xsrf},
        ):
            try:
                approved = session.post(
                    APPROVE_URL,
                    data=payload,
                    headers=headers,
                    allow_redirects=False,
                    timeout=25,
                )
            except Exception:
                continue
            code = _code_from_response(approved)
            if code:
                return code

    note("код получить не удалось, пробуем ещё раз")
    return None


# ------------------------------------------------------- поток через WebView

#: Куки, по которым понятно, что человек вошёл в аккаунт.
_SESSION_COOKIE = "hhtoken"

#: Сколько подряд неудачных опросов окна терпим, прежде чем сдаться.
#: Одиночные сбои у WebView2 бывают и это нормально, но если окно не отвечает
#: совсем — лучше честно закрыться и предложить другой способ входа, чем молча
#: крутиться до конца таймаута.
MAX_WINDOW_ERRORS = 10


def _cookies_to_dict(cookies: Iterable[Any]) -> dict[str, str]:
    """pywebview отдаёт список SimpleCookie — разложить в обычный словарь."""
    jar: dict[str, str] = {}
    for cookie in cookies or []:
        try:
            for name in cookie.keys():
                jar[name] = cookie[name].value
        except AttributeError:
            continue
    return jar


def _logged_in(jar: dict[str, str]) -> bool:
    if _SESSION_COOKIE not in jar:
        return False
    role = (jar.get("hhrole") or "").lower()
    if role and role == "anonymous":
        return False
    return True


def webview_available() -> tuple[bool, str]:
    try:
        import webview  # noqa: F401
    except Exception as exc:  # pragma: no cover - зависит от окружения
        return False, str(exc)
    return True, ""


#: Папка сессии окна входа для версий до появления нескольких аккаунтов.
LEGACY_SESSION_DIR = "webview"


def session_dir(uid: str = "") -> str:
    """Где окно входа хранит свою сессию. У каждого аккаунта папка своя.

    С общей папкой второй вход молча попадал бы в тот же аккаунт, что и
    первый: куки уже лежат на диске, и форма входа даже не показывается.
    """
    name = f"{LEGACY_SESSION_DIR}-{uid}" if uid else LEGACY_SESSION_DIR
    return os.path.join(paths.state_dir(), name)


def forget_session(uid: str) -> None:
    """Убрать сохранённую сессию окна входа — аккаунт отключён."""
    if not uid:
        return
    shutil.rmtree(session_dir(uid), ignore_errors=True)


def drop_legacy_session() -> bool:
    """Убрать общую папку сессии, оставшуюся от версий с одним аккаунтом.

    Она больше не используется ни одним аккаунтом, а весит немало: это
    полноценный профиль WebView2.
    """
    path = session_dir()
    if not os.path.isdir(path):
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.isdir(path)


def run_webview_flow(
    status: StatusFn,
    timeout_sec: int = 900,
    uid: str = "",
) -> dict[str, Any] | None:
    """Открыть окно входа hh.ru и вернуть токен.

    Должно вызываться из главного потока: pywebview требует главный поток
    для цикла обработки сообщений GUI.
    """
    try:
        import webview
    except Exception as exc:
        raise AuthError(f"встроенный браузер недоступен: {exc}") from exc

    outcome: dict[str, Any] = {}
    storage = session_dir(uid)

    def worker(window: Any) -> None:
        # Что бы здесь ни случилось, окно обязано закрыться: упавший в прошлой
        # версии наблюдатель как раз и оставлял окно висеть навсегда.
        try:
            _watch(window)
        except Exception as exc:
            outcome["error"] = str(exc)
        finally:
            try:
                window.destroy()
            except Exception:
                pass

    def _watch(window: Any) -> None:
        deadline = time.time() + timeout_sec
        user_agent: str | None = None
        last_signature: str | None = None
        last_attempt = 0.0
        announced = False
        failures = 0

        while time.time() < deadline and not outcome:
            time.sleep(1.0)

            # Вдруг повезёт и код всё-таки окажется в адресной строке.
            try:
                current = window.get_current_url()
            except Exception:
                current = None
            if current and REDIRECT_SCHEME in current:
                code = extract_code(current)
                if code:
                    outcome["code"] = code
                    break

            try:
                jar = _cookies_to_dict(window.get_cookies())
            except Exception as exc:
                failures += 1
                if failures >= MAX_WINDOW_ERRORS:
                    raise AuthError(f"окно входа не отвечает: {exc}") from exc
                continue
            failures = 0

            if not _logged_in(jar):
                continue

            if not announced:
                announced = True
                status("Вход выполнен — забираем токен...")

            signature = "|".join(f"{k}={jar[k]}" for k in sorted(jar) if k in
                                 ("hhtoken", "hhuid", "crypted_hhuid", "_xsrf"))
            now = time.time()
            if signature == last_signature and now - last_attempt < 8:
                continue
            last_signature, last_attempt = signature, now

            if user_agent is None:
                try:
                    user_agent = window.evaluate_js("navigator.userAgent")
                except Exception:
                    user_agent = BROWSER_UA

            code = complete_with_cookies(jar, user_agent)
            if code:
                outcome["code"] = code
                break

    window = webview.create_window(
        "Вход в аккаунт — Hunter CLI",
        build_auth_url(),
        width=520,
        height=760,
    )
    threading.Thread(target=worker, args=(window,), daemon=True).start()
    # private_mode/storage_path принимает только start(), не create_window().
    # Сессию храним на диске, чтобы повторный вход проходил в один клик.
    webview.start(private_mode=False, storage_path=storage)

    code = outcome.get("code")
    if not code:
        if outcome.get("error"):
            raise AuthError(f"сбой при получении токена: {outcome['error']}")
        return None

    status("Код получен, меняем на токен...")
    return exchange_code(code)


# --------------------------------------- запасной путь: системный браузер


def register_protocol_handler() -> tuple[bool, str]:
    """Прописать hh-android:// на себя в HKCU, чтобы браузер отдал нам код."""
    if sys.platform != "win32":
        return False, "поддерживается только на Windows"
    try:
        import winreg
    except ImportError as exc:  # pragma: no cover
        return False, str(exc)

    executable = sys.executable
    if getattr(sys, "frozen", False):
        command = f'"{executable}" "%1"'
    else:
        entry = os.path.join(paths.base_dir(), "main.py")
        command = f'"{executable}" "{entry}" "%1"'

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\hh-android") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "URL:hh-android Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
            with winreg.CreateKey(key, r"shell\open\command") as cmd:
                winreg.SetValue(cmd, "", winreg.REG_SZ, command)
    except OSError as exc:
        return False, str(exc)
    return True, ""


def write_handoff(url: str) -> None:
    """Вызывается экземпляром, который Windows запустила с hh-android://..."""
    payload = {"url": url, "code": extract_code(url), "at": time.time()}
    with open(paths.handoff_path(), "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def read_handoff(max_age_sec: int = 900) -> str | None:
    path = paths.handoff_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if time.time() - float(payload.get("at") or 0) > max_age_sec:
        return None
    return payload.get("code") or extract_code(payload.get("url"))


def clear_handoff() -> None:
    try:
        os.remove(paths.handoff_path())
    except OSError:
        pass
