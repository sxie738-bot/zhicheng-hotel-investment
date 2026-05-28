@echo off
REM 致城酒店投资测算系统 — Windows 启动脚本
echo 正在安装依赖...
pip install fastapi uvicorn > nul 2>&1
if errorlevel 1 (
  echo 依赖安装失败，请手动执行: pip install fastapi uvicorn
  pause
  exit /b 1
)

echo 启动服务...
python app.py
pause