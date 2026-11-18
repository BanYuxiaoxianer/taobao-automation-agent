@echo off
chcp 936 >nul
title merchant-agent - 重启
cd /d "%~dp0"
set PORT=7860

echo 正在结束占用 %PORT% 端口的旧进程...
set FOUND=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  set FOUND=1
  echo   结束 PID %%p
  taskkill /PID %%p /F >nul 2>&1
)
if not defined FOUND echo   没有旧进程，直接启动。
timeout /t 1 >nul
echo.
call "%~dp0启动.bat"
