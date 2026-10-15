@echo off
chcp 65001 >nul
title 加权（仓权）合约监控面板
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.10+ 并加入 PATH
  pause
  exit /b 1
)

python -c "import tqsdk" >nul 2>nul
if errorlevel 1 (
  echo [提示] 正在安装 tqsdk（仅首次）...
  python -m pip install tqsdk -i https://pypi.tuna.tsinghua.edu.cn/simple
)

echo 启动加权（仓权）合约监控面板 ...
python 服务端.py
pause
