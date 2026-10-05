"""Автозапуск вместе с Windows — задание планировщика текущего пользователя.

Почему планировщик, а не ключ Run в реестре
-------------------------------------------
Ключ Run запускает программу в первые секунды после входа. Если .exe лежит
на диске, который подключается не сразу (у владельца — Google Drive, буква G:),
запуск молча промахивается: файла ещё нет. Задание планировщика умеет
задержку при входе — минуты хватает, чтобы диск подключился. Прав
администратора не нужно: задание на вход своего же пользователя с обычными
правами создаётся без них (проверено пробой 2026-10-05).

Включён ли автозапуск, в конфиге не хранится: источник правды — само
задание. Флаг в конфиге разошёлся бы с ним, стоит тронуть задание руками.
"""

from __future__ import annotations

import ctypes
import getpass
import os
import re
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

TASK_NAME = "HunterCLI"

#: Задержка после входа в Windows (ISO 8601). Минуты хватает, чтобы диск
#: с программой подключился и сеть поднялась.
DELAY = "PT1M"

#: Сколько ждём ответа планировщика. Он отвечает за доли секунды; предел —
#: страховка от зависшего вызова, а не ожидание.
TIMEOUT_SEC = 30

_COMMAND_RE = re.compile(r"<Command>(.*?)</Command>", re.S)

_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Hunter CLI: автопилот поднятия резюме, запуск при входе в Windows.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>{delay}</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <WorkingDirectory>{folder}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def supported() -> bool:
    """Автозапуск есть только у собранной программы под Windows.

    Из исходников запускать нечего: задание указывало бы на python.exe, а
    исходники — рабочее место разработчика, не установленная программа.
    """
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def target() -> str:
    """Какой файл ставить в автозапуск — тот, что запущен сейчас."""
    return sys.executable


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    name = os.environ.get("USERNAME", "") or getpass.getuser()
    return f"{domain}\\{name}" if domain else name


def build_xml(executable: str, user: str) -> str:
    """Описание задания для schtasks /XML."""
    return _XML.format(
        user=escape(user),
        delay=DELAY,
        command=escape(executable),
        folder=escape(os.path.dirname(executable)),
    )


def command_in(xml: str) -> str | None:
    """Какой файл запускает задание — из его XML. None — не разобрали."""
    match = _COMMAND_RE.search(xml or "")
    if not match:
        return None
    text = match.group(1).strip()
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"),
                         ("&amp;", "&")):
        text = text.replace(entity, char)
    return text.strip('"') or None


def _schtasks(args: list[str]) -> tuple[int, str]:
    """Вызвать schtasks.exe. (код возврата, вывод).

    Сообщения schtasks переведены на язык системы, поэтому решение принимается
    только по коду возврата, а текст идёт в журнал как есть.
    """
    try:
        done = subprocess.run(
            ["schtasks.exe", *args],
            capture_output=True,
            timeout=TIMEOUT_SEC,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return done.returncode, _decode((done.stdout or b"") + (done.stderr or b""))


def _console_encodings() -> list[str]:
    """Кодировки, в которых schtasks может писать в трубу, — по порядку.

    Пишет он в кодовой странице консоли: проверено 2026-10-05 на машине
    владельца — и XML задания, и сообщения об ошибках пришли в cp866, путь с
    кириллицей читается только так. Страницу спрашиваем у Windows, а cp866 и
    cp1251 — запас на случай, если спросить не вышло.
    """
    names = ["utf-8"]
    try:
        kernel = ctypes.windll.kernel32
        pages = (kernel.GetConsoleOutputCP(), kernel.GetOEMCP())
    except Exception:
        pages = ()
    for page in pages:
        if page and f"cp{page}" not in names:
            names.append(f"cp{page}")
    return names + [name for name in ("cp866", "cp1251") if name not in names]


def _decode(raw: bytes) -> str:
    """Вывод schtasks — текст в кодовой странице консоли.

    UTF-16 разбираем на всякий случай: нулевые байты — верный его признак,
    а в UTF-8 они формально допустимы, и без этой проверки текст разобрался
    бы в строку с нулями между буквами.
    """
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw:
        return raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-16-le",
                          errors="replace").strip()
    for encoding in _console_encodings():
        try:
            return raw.decode(encoding).strip()
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace").strip()


def registered(task: str = TASK_NAME) -> str | None:
    """Путь, который запускает задание автозапуска. None — задания нет."""
    code, output = _schtasks(["/Query", "/TN", task, "/XML"])
    if code != 0:
        return None
    return command_in(output)


def enable(executable: str | None = None, *, task: str = TASK_NAME) -> tuple[bool, str]:
    """Создать (или пересоздать) задание. (успех, пояснение при отказе)."""
    executable = executable or target()
    handle, path = tempfile.mkstemp(prefix="huntercli-task-", suffix=".xml")
    try:
        with os.fdopen(handle, "w", encoding="utf-16") as fh:
            fh.write(build_xml(executable, _current_user()))
        code, output = _schtasks(["/Create", "/TN", task, "/XML", path, "/F"])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if code != 0:
        return False, output or f"планировщик вернул код {code}"
    return True, ""


def disable(*, task: str = TASK_NAME) -> tuple[bool, str]:
    """Удалить задание. Задания и так нет — тоже успех."""
    if registered(task) is None:
        return True, ""
    code, output = _schtasks(["/Delete", "/TN", task, "/F"])
    if code != 0:
        return False, output or f"планировщик вернул код {code}"
    return True, ""


def sync(executable: str | None = None, *, task: str = TASK_NAME) -> str:
    """Держать задание при запущенном файле.

    "missing" — задания нет (его убрали руками — не восстанавливаем);
    "ok" — указывает сюда; "moved" — указывало на другой файл и перенаправлено;
    "failed" — перенаправить не вышло.
    """
    executable = executable or target()
    current = registered(task)
    if current is None:
        return "missing"
    if os.path.normcase(os.path.normpath(current)) == os.path.normcase(os.path.normpath(executable)):
        return "ok"
    ok, _ = enable(executable, task=task)
    return "moved" if ok else "failed"
