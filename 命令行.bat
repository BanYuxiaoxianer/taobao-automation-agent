@echo off
chcp 936 >nul
title merchant-agent - 命令行
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 请先双击 启动.bat 完成一次性初始化。
  pause
  exit /b 1
)

echo 常用命令：
echo   python cli.py doctor                     环境自检
echo   python cli.py seed                       生成示例知识库
echo   python cli.py ask "买家问什么时候发货"    直接提问
echo   python cli.py lister "保温杯"            出上架素材
echo   python cli.py drafts                     看待上架清单
echo.
cmd /k ".venv\Scripts\python.exe"
