#!/bin/bash
# 致城酒店投资测算系统启动脚本
cd "$(dirname "$0")"

echo "正在安装依赖..."
pip install fastapi uvicorn sqlite3 > /dev/null 2>&1 || {
  echo "依赖安装失败，请手动执行: pip install fastapi uvicorn"
  exit 1
}

echo "启动服务..."
python app.py