@echo off
chcp 936 >nul
title merchant-agent - 商家智能运营助手
cd /d "%~dp0"
set PORT=7860

rem ---- 先检查端口是否被旧进程占着 ----
set OLD=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do set OLD=%%p
if not defined OLD goto ready

echo.
echo 端口 %PORT% 已被进程 %OLD% 占用（很可能是上次的窗口没关干净）。
echo 不结束它的话，你改了代码也看不到效果。
echo.
choice /c YN /t 8 /d Y /n /m "结束旧进程并继续？[Y=结束 / N=取消] 8 秒后默认 Y："
if errorlevel 2 goto cancel
taskkill /PID %OLD% /F >nul 2>&1
echo 已结束旧进程，继续启动。
echo.
timeout /t 1 >nul

:ready
if not exist ".venv\Scripts\python.exe" goto bootstrap
goto run

:bootstrap
echo [首次运行] 正在创建独立运行环境，大约需要 1-3 分钟...
py -3 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
if errorlevel 1 goto fail

:run
if not exist "data\knowledge\products.json" (
  echo [首次运行] 正在生成示例知识库...
  ".venv\Scripts\python.exe" seed.py
)
".venv\Scripts\python.exe" webui.py
echo.
echo 网页已关闭。按任意键退出。
pause
exit /b 0

:cancel
echo 已取消，未做任何改动。
pause
exit /b 0

:fail
echo.
echo 初始化失败：请确认已安装 Python 3.10 以上版本，并检查网络是否可访问 pip 镜像。
pause
exit /b 1
