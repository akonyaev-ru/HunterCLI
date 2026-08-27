# -*- mode: python ; coding: utf-8 -*-
"""Сборка Hunter CLI в один .exe.

pywebview на Windows работает через WebView2 и pythonnet (clr). PyInstaller
сам их не находит, поэтому пакеты собираем целиком через collect_all.
"""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

for package in ("webview", "clr_loader", "pythonnet"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

hiddenimports += [
    "clr",
    "huntercli",
    "huntercli.app",
    "huntercli.auth",
    "huntercli.config",
    "huntercli.diagnostics",
    "huntercli.engine",
    "huntercli.hh",
    "huntercli.logbus",
    "huntercli.paths",
    "huntercli.secrets",
    "huntercli.ui.banner",
    "huntercli.ui.dashboard",
    "huntercli.ui.keys",
    "huntercli.ui.screens",
    "huntercli.ui.theme",
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PIL, cryptography и bcrypt приложению не нужны, но затягиваются, если
    # оказались в окружении сборки: `collect_all('webview')` берёт пакет
    # целиком, вместе с бэкендами для macOS и режимом HTTPS-сервера
    # (`webview/__init__.py`, `__generate_ssl_cert`). Оба пути у нас мертвы:
    # `webview.start()` зовётся без ssl, а cryptography там импортируется
    # лениво под `try/except ImportError`. Без этих запретов размер .exe
    # зависит от того, что установлено на машине сборщика: 18 МБ в чистом
    # окружении против 29 МБ в общем (проверено 2026-08-27).
    excludes=['tkinter', 'PySide6', 'PyQt5', 'PyQt6', 'matplotlib', 'numpy',
              'PIL', 'cryptography', 'bcrypt'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='HunterCLI',
    # Свойства файла: версия и автор видны в «Подробно» у .exe.
    version='version_info.txt',
    # icon.ico собран tools/make_icon.py сразу во всех ходовых размерах.
    # Один большой размер класть нельзя: Windows масштабирует его сама, грубо,
    # и в панели задач иконка выглядит размытой.
    icon='icon.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX не применяем. На раннере GitHub его и так нет, шаг молча пропускался,
    # зато при локальной сборке на машине с UPX получался упакованный файл —
    # а упаковку антивирусы считают признаком вредоноса и ругаются охотнее.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
