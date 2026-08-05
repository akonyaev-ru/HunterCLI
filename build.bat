@echo off
chcp 65001 >nul
setlocal

echo.
echo  === Hunter CLI: сборка ===
echo.

echo [1/3] Установка зависимостей...
python -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo [2/3] Очистка прошлой сборки...
if exist build rmdir /s /q build
if exist dist\HunterCLI.exe del /q dist\HunterCLI.exe

echo.
echo [3/3] Сборка HunterCLI.exe...
python -m PyInstaller --noconfirm --clean HunterCLI.spec
if errorlevel 1 goto :failed

echo.
echo  === Готово ===
echo  Файл: dist\HunterCLI.exe
echo.
pause
exit /b 0

:failed
echo.
echo  !!! Сборка не удалась. Смотрите сообщения выше.
echo.
pause
exit /b 1
