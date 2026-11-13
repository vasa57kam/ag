#!/bin/bash
# AI Agent — диагностика. Отчёт: /opt/ai-agent/diag-<дата>.txt
set +e
BASE="/opt/ai-agent"
TS=$(date +%Y%m%d-%H%M%S)
OUT="$BASE/diag-$TS.txt"
mkdir -p "$BASE"
exec > >(tee "$OUT") 2>&1
sec(){ echo; echo "===== $1 ====="; }

sec "ОБЩЕЕ"
date; hostname; uname -a; uptime
echo "--- CPU/RAM ---"; nproc; free -h; swapon --show
echo "--- ДИСК ---"; df -h /; df -i /
echo "--- PYTHON ---"; python3 -V; pip3 --version 2>/dev/null
python3 -c "import flask,paramiko;print('flask',flask.__version__,'paramiko',paramiko.__version__)" 2>&1
python3 -c "import openai;print('openai',openai.__version__)" 2>&1 | head -1

sec "ВЕРСИЯ АГЕНТА"
if [ -f "$BASE/agent.py" ]; then
  ls -l "$BASE/agent.py"; md5sum "$BASE/agent.py"
  head -3 "$BASE/agent.py"
  echo "--- маркеры ---"
  for m in "/task/start" "RUNS" "closing" "make_response" "web_password" "num_predict" "auth disabled"; do
    printf "%-16s : %s\n" "$m" "$(grep -c -- "$m" "$BASE/agent.py")"
  done
  echo "--- ключевые строки ---"
  grep -n "^from flask\|^import\|timeout=\|num_ctx\|MAX_STEPS\|def auth\|before_request" "$BASE/agent.py" | head -25
  echo "--- синтаксис ---"
  python3 -m py_compile "$BASE/agent.py" && echo "compile OK" || echo "compile FAILED"
else
  echo "agent.py ОТСУТСТВУЕТ"
fi

sec "КОНФИГ (пароли скрыты)"
if [ -f "$BASE/config.json" ]; then
  python3 - <<'PY'
import json
try:
    c=json.load(open("/opt/ai-agent/config.json"))
    for h,v in (c.get("ssh") or {}).items():
        v["password"]="***" if v.get("password") else "(пусто)"
    c.pop("web_password",None)
    print(json.dumps(c,ensure_ascii=False,indent=2))
except Exception as e: print("ошибка чтения:",e)
PY
else echo "config.json нет"; fi

sec "СЕРВИСЫ"
systemctl is-enabled ai-agent ollama 2>&1
systemctl status ai-agent --no-pager -l | head -20
systemctl status ollama --no-pager -l | head -12
echo "--- порты ---"; ss -tlnp 2>/dev/null | grep -E ":8000|:11434"

sec "ЛОГИ АГЕНТА (последние 120)"
journalctl -u ai-agent -n 120 --no-pager 2>&1 | tail -120
sec "ЛОГИ OLLAMA (последние 25)"
journalctl -u ollama -n 25 --no-pager 2>&1 | tail -25

sec "OLLAMA"
ollama --version 2>&1
ollama list 2>&1
curl -s --max-time 10 http://127.0.0.1:11434/api/tags | head -c 1200; echo

sec "HTTP ПРОВЕРКИ"
for u in "/" "/models" "/sessions" "/task/status?sid=diag" "/disk?type=info"; do
  printf "%-26s -> %s\n" "$u" "$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:8000$u")"
done
echo "--- /models body ---"; curl -s --max-time 15 http://127.0.0.1:8000/models | head -c 600; echo
echo "--- /sessions body ---"; curl -s --max-time 15 http://127.0.0.1:8000/sessions | head -c 600; echo

sec "БАЗА: последние задачи и сообщения"
python3 - <<'PY'
import sqlite3, os
p="/opt/ai-agent/agent.db"
if not os.path.exists(p): print("agent.db нет"); raise SystemExit
c=sqlite3.connect(p)
try:
    print("-- tasks --")
    for r in c.execute("SELECT sid,status,substr(info,1,80),ts FROM tasks ORDER BY id DESC LIMIT 10"): print(r)
    print("-- messages (обрезано) --")
    for r in c.execute("SELECT sid,role,substr(content,1,200),ts FROM messages ORDER BY id DESC LIMIT 10"): print(r)
    print("всего сообщений:", c.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
except Exception as e: print("ошибка БД:",e)
PY

sec "WORKSPACE"
ls -la "$BASE/workspace" 2>&1 | head -20
echo "--- бэкапы ---"; ls -d "$BASE"/backup-* 2>/dev/null | tail -5

sec "ИТОГ"
echo "Отчёт сохранён: $OUT"
echo "Скопируй этот вывод целиком и пришли в чат для разбора багов."
