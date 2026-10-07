#!/bin/bash
# 一键启动：链上体检工具 + Cloudflare 隧道对外
# 用法：bash start.sh
cd "$(dirname "$0")"
# 启动后端（已在跑则跳过）
if ! curl -s -m 3 http://127.0.0.1:18080/api/health >/dev/null 2>&1; then
  echo "启动后端..."
  PORT=18080 .venv/bin/python app.py > /tmp/tracker.log 2>&1 &
  sleep 5
fi
# 启动隧道（已在跑则跳过）
if ! pgrep -f "cloudflared tunnel --url" >/dev/null 2>&1; then
  echo "启动隧道..."
  nohup cloudflared tunnel --url http://localhost:18080 > /tmp/tunnel.log 2>&1 &
  sleep 8
fi
echo "---- 对外地址 ----"
grep -o 'https://[^ ]*trycloudflare.com' /tmp/tunnel.log | head -1
echo "本地健康检查："
curl -s http://127.0.0.1:18080/api/health; echo
