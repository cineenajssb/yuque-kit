#!/usr/bin/env bash
# 语雀便携工具包 - macOS / Linux 入口
# 用法：./yuque.sh              启动并等待登录
#       ./yuque.sh books        其它命令直接透传
set -u
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python yuque.py "$@"
fi

for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then
    exec "$PY" yuque.py "$@"
  fi
done

echo "[X] 没有找到 python3，请先安装 Python 3.8+"
echo "    macOS:  brew install python3"
echo "    Ubuntu: sudo apt install python3 python3-venv"
exit 2
