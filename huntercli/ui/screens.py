"""Экраны вне дашборда: приветствие, вход, прощание."""

from __future__ import annotations

import time
import webbrowser

from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .. import auth, hh
from ..config import Account
from ..logbus import LogBus
from . import banner
from .theme import ACCENT, ACCENT_SOFT, COOL, ERR, FRAME, FRAME_HOT, MUTED, OK, WARN, human_span

#: Сколько ждём код от системного браузера через обработчик протокола.
BROWSER_WAIT_SEC = 600


def _ask(console: Console, prompt: str, **kwargs: object) -> str | None:
    """Спросить пользователя. None — ввод недоступен или прерван.

    Программу могут запустить без консоли (из планировщика, с перенаправленным
    вводом) — тогда Prompt получает EOF, и падать из-за этого нельзя.
    """
    try:
        return Prompt.ask(prompt, console=console, **kwargs)  # type: ignore[arg-type]
    except (EOFError, KeyboardInterrupt):
        return None


def _shell(console: Console, body: object, title: str, *, hot: bool = False) -> None:
    console.clear()
    console.print()
    # Без Align.center: plain_header уже расставил строки по ширине окна, а
    # внешнее выравнивание сдвинуло бы блок целиком и увело подпись влево.
    console.print(banner.plain_header(console.size.width))
    console.print()
    console.print(
        Panel(
            body,
            title=f"[bold {ACCENT_SOFT}]{title}[/]",
            title_align="left",
            border_style=FRAME_HOT if hot else FRAME,
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()


def welcome(console: Console) -> None:
    body = Group(
        Text("Автопилот поднятия резюме.", style="white"),
        Text(),
        Text("Что программа делает:", style=f"bold {ACCENT}"),
        Text("  · узнаёт, когда резюме можно поднять снова;", style=MUTED),
        Text("  · ждёт этот момент и добавляет случайную задержку;", style=MUTED),
        Text("  · поднимает резюме и показывает результат в этом окне.", style=MUTED),
        Text(),
        Text("Для работы нужно один раз войти в свой аккаунт.", style="white"),
        Text("Пароль программа не видит и не хранит — вход идёт на самом сайте,", style=MUTED),
        Text("а сохраняется только токен доступа.", style=MUTED),
        Text(),
        Text("Аккаунтов можно подключить несколько: каждый на своей вкладке.", style=MUTED),
    )
    _shell(console, body, "ЗНАКОМСТВО")
    _ask(
        console,
        f"[{ACCENT_SOFT}]Нажмите Enter, чтобы продолжить[/]",
        default="",
        show_default=False,
    )


# ------------------------------------------------------------------- вход


def _methods_table(cancel: str) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style=f"bold {ACCENT_SOFT}", justify="right", width=3)
    table.add_column()
    table.add_row("1", Text("Открыть окно входа  ·  рекомендуется", style="white"))
    table.add_row("", Text("Небольшое окно прямо из программы. Вошли — окно закроется само.", style=MUTED))
    table.add_row("2", Text("Войти в обычном браузере", style="white"))
    table.add_row("", Text("Если окно из пункта 1 не открылось или не работает.", style=MUTED))
    table.add_row("3", Text("Вставить ссылку или токен вручную", style="white"))
    table.add_row("", Text("Запасной вариант, когда всё остальное не сработало.", style=MUTED))
    table.add_row("0", Text(cancel, style=MUTED))
    return table


def authorize(
    console: Console,
    log: LogBus,
    account: Account,
    reason: str = "",
    *,
    cancel: str = "Выйти из программы",
) -> bool:
    """Провести пользователя через вход в аккаунт. True, если токен получен."""
    while True:
        body_parts: list[object] = []
        if reason:
            body_parts.extend([Text(reason, style=WARN), Text()])
        body_parts.extend(
            [
                Text("Выберите способ входа:", style="white"),
                Text(),
                _methods_table(cancel),
            ]
        )
        _shell(console, Group(*body_parts), "ВХОД В АККАУНТ", hot=True)

        choice = _ask(
            console,
            f"[{ACCENT_SOFT}]Ваш выбор[/]",
            choices=["1", "2", "3", "0"],
            default="1",
        )
        if choice is None:
            console.print(f"[{MUTED}]Ввод недоступен — выходим.[/]")
            return False
        if choice == "0":
            return False

        try:
            if choice == "1":
                payload = _via_webview(console, log, account.uid)
            elif choice == "2":
                payload = _via_browser(console, log)
            else:
                payload = _via_manual(console, log)
        except auth.AuthError as exc:
            payload = None
            reason = f"Не получилось: {exc}"
            log.error(f"Авторизация: {exc}")
        else:
            reason = "" if payload else "Вход не завершён — попробуйте другой способ."

        if payload:
            account.apply_token(payload)
            # Имя и владельца выясним заново: вход мог быть и в другой аккаунт.
            account.name = ""
            account.person_id = ""
            account.save()
            log.ok("Авторизация пройдена, токен сохранён")
            console.print(f"[{OK}]✓ Готово. Токен получен и сохранён.[/]")
            time.sleep(1.2)
            return True

        console.print(f"[{WARN}]{reason}[/]")
        time.sleep(1.5)


def _via_webview(console: Console, log: LogBus, uid: str = "") -> dict | None:
    available, error = auth.webview_available()
    if not available:
        raise auth.AuthError(f"встроенное окно недоступно ({error}). Выберите пункт 2 или 3")

    console.print()
    console.print(f"[{ACCENT}]Сейчас откроется окно входа.[/]")
    console.print(f"[{MUTED}]  1. Введите телефон или почту и войдите (можно по СМС-коду).[/]")
    console.print(f"[{MUTED}]  2. Если появится кнопка «Продолжить» — нажмите её.[/]")
    console.print(f"[{MUTED}]  3. Окно закроется само, как только программа получит доступ.[/]")
    console.print()
    console.print(f"[{MUTED}]Окно можно закрыть вручную — тогда вернётесь в это меню.[/]")
    console.print()

    def status(message: str) -> None:
        console.print(f"[{COOL}]  › {message}[/]")
        log.step(message)

    return auth.run_webview_flow(status, uid=uid)


def _via_browser(console: Console, log: LogBus) -> dict | None:
    console.print()
    ok, error = auth.register_protocol_handler()
    if ok:
        console.print(f"[{OK}]✓ Программа зарегистрирована как обработчик ссылки.[/]")
    else:
        console.print(f"[{WARN}]Не удалось зарегистрировать обработчик: {error}[/]")
        console.print(f"[{MUTED}]Ничего страшного — код можно будет вставить вручную (пункт 3).[/]")

    auth.clear_handoff()
    url = auth.build_auth_url()
    console.print()
    console.print(f"[{ACCENT}]Открываю браузер. Войдите в аккаунт и нажмите «Продолжить».[/]")
    console.print(f"[{MUTED}]Если в браузере уже открыт другой аккаунт, вход пройдёт в него:[/]")
    console.print(f"[{MUTED}]для второго аккаунта надёжнее пункт 1 или приватное окно.[/]")
    console.print(f"[{MUTED}]Если браузер не открылся, скопируйте ссылку вручную:[/]")
    console.print(f"[{COOL}]{url}[/]")
    console.print()

    try:
        webbrowser.open(url)
    except Exception:
        pass

    console.print(f"[{MUTED}]Жду ответа от браузера. Ctrl+C — вернуться в меню.[/]")
    deadline = time.time() + BROWSER_WAIT_SEC
    try:
        with console.status(f"[{ACCENT_SOFT}]Ожидание подтверждения в браузере...", spinner="dots"):
            while time.time() < deadline:
                code = auth.read_handoff()
                if code:
                    auth.clear_handoff()
                    log.step("Браузер передал код авторизации")
                    return auth.exchange_code(code)
                time.sleep(1.0)
    except KeyboardInterrupt:
        return None

    console.print(f"[{WARN}]Браузер так и не передал код.[/]")
    return None


_MANUAL_HINT = (
    "После нажатия «Продолжить» браузер попробует открыть ссылку вида\n"
    "hh-android://oauth/code?code=XXXX и покажет ошибку — это нормально.\n"
    "Скопируйте эту ссылку из адресной строки и вставьте сюда целиком."
)


def _via_manual(console: Console, log: LogBus) -> dict | None:
    console.print()
    console.print(Text(_MANUAL_HINT, style=MUTED))
    console.print()
    console.print(f"[{MUTED}]Ссылка для входа:[/] [{COOL}]{auth.build_auth_url()}[/]")
    console.print()
    console.print(f"[{MUTED}]Можно вставить: ссылку с code=, сам код или готовый access token.[/]")
    console.print()

    raw = (_ask(console, f"[{ACCENT_SOFT}]Вставьте сюда[/]", default="") or "").strip()
    if not raw:
        return None

    code = auth.extract_code(raw)
    if code and "code=" in raw:
        log.step("Получен код авторизации, меняем на токен")
        return auth.exchange_code(code)

    # Голую строку не отличить на глаз: код и токен выглядят одинаково.
    # Сначала пробуем как код, а если hh.ru не принял — считаем токеном.
    if code:
        try:
            log.step("Пробуем вставленную строку как код авторизации")
            return auth.exchange_code(code)
        except auth.AuthError as exc:
            log.step(f"Как код не подошло ({exc}) — пробуем как готовый токен")

    token = raw.split()[-1]
    if len(token) >= 30:
        if not hh.token_is_sendable(token):
            raise auth.AuthError("в токене есть посторонние символы — скопируйте его заново")
        log.warn("Принят готовый токен: срок жизни неизвестен, обновлять придётся вручную")
        return {"access_token": token, "expires_in": None}

    raise auth.AuthError("не удалось распознать ни ссылку, ни код, ни токен")


# ---------------------------------------------------------------- финал


def farewell(console: Console, session_bumps: int, total_bumps: int, uptime: float) -> None:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=MUTED, justify="right")
    grid.add_column(style="bold white")
    grid.add_row("Отработано", human_span(uptime, short=True))
    grid.add_row("Поднятий за сеанс", str(session_bumps))
    grid.add_row("Поднятий всего", str(total_bumps))

    console.print()
    console.print(
        Panel(
            Group(Text("Автопилот остановлен.", style=f"bold {ACCENT}"), Text(), grid),
            border_style=FRAME,
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
    console.print()


def fatal(console: Console, message: str) -> None:
    console.print()
    console.print(
        Panel(
            Text(message, style="white"),
            title=f"[bold {ERR}]ЧТО-ТО ПОШЛО НЕ ТАК[/]",
            title_align="left",
            border_style=ERR,
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )
