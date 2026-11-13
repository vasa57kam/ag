#!/bin/bash
# AI Agent — полная переустановка с нуля
# Запуск: bash reinstall.sh [--wipe] [--model qwen2.5-coder:14b]
set -u
REPO="https://raw.githubusercontent.com/vasa57kam/ag/main"
BASE="/opt/ai-agent"
TS=$(date +%Y%m%d-%H%M%S)
WIPE=0; MODEL_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --wipe) WIPE=1; shift;;
    --model) MODEL_ARG="${2:-}"; shift 2;;
    *) shift;;
  esac
done
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
say(){ echo -e "$1"; }
[ "$(id -u)" = "0" ] || { say "${R}Запусти от root${N}"; exit 1; }

say "${Y}[1/9] Остановка старого агента${N}"
systemctl stop ai-agent 2>/dev/null || true
systemctl disable ai-agent 2>/dev/null || true
pkill -f "$BASE/agent.py" 2>/dev/null || true
sleep 1

say "${Y}[2/9] Бэкап конфигов и истории${N}"
mkdir -p "$BASE/backup-$TS"
for f in config.json agent.db agent.py; do
  [ -f "$BASE/$f" ] && cp -a "$BASE/$f" "$BASE/backup-$TS/" || true
done
if [ "$WIPE" = "1" ]; then rm -f "$BASE/agent.db"; say "   история удалена (--wipe)"; fi
mkdir -p "$BASE/workspace"

say "${Y}[3/9] Системные пакеты${N}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || true
apt-get install -y -qq python3-pip sqlite3 smartmontools e2fsprogs curl git cloud-guest-utils >/dev/null 2>&1 || true
pip3 install -q --upgrade flask paramiko 2>/dev/null || pip3 install -q --break-system-packages --upgrade flask paramiko || {
  say "${R}Не удалось поставить flask/paramiko${N}"; exit 1; }

say "${Y}[4/9] Ollama${N}"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | bash
systemctl enable ollama >/dev/null 2>&1 || true
systemctl start ollama || true
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done

say "${Y}[5/9] Выбор модели${N}"
RAM=$(free -g | awk '/^Mem:/{print $2}')
if [ -n "$MODEL_ARG" ]; then MODEL="$MODEL_ARG"
else
  echo "   RAM: ${RAM} ГБ"
  echo "   1) qwen2.5-coder:32b        (~22 ГБ RAM, медленно на CPU)"
  echo "   2) qwen2.5-coder:32b-q3_K_M (~17 ГБ RAM)"
  echo "   3) qwen2.5-coder:14b        (~10 ГБ RAM)  [рекомендуется]"
  echo "   4) qwen2.5-coder:7b         (~6 ГБ RAM)"
  read -r -p "   Номер [3]: " CH </dev/tty || CH=3
  case "${CH:-3}" in
    1) MODEL=qwen2.5-coder:32b;; 2) MODEL=qwen2.5-coder:32b-q3_K_M;;
    4) MODEL=qwen2.5-coder:7b;; *) MODEL=qwen2.5-coder:14b;;
  esac
fi
say "   модель: ${G}$MODEL${N}"
ollama pull "$MODEL" || say "${Y}pull не удался — проверь интернет/место${N}"

say "${Y}[6/9] Скачивание agent.py из GitHub${N}"
ok=0
for i in 1 2 3; do
  curl -fsSL "$REPO/agent.py" -o "$BASE/agent.py.new" && ok=1 && break
  sleep 2
done
[ "$ok" = "1" ] || { say "${R}Не скачался agent.py${N}"; exit 1; }
if head -c 200 "$BASE/agent.py.new" | grep -qi "<!DOCTYPE\|<html\|404"; then
  say "${R}GitHub вернул HTML вместо кода (проверь имя ветки main/master)${N}"; exit 1
fi
mv "$BASE/agent.py.new" "$BASE/agent.py"

say "${Y}[7/9] Проверка версии и хотфиксы${N}"
python3 - "$BASE/agent.py" <<'PY'
import sys, py_compile
p = sys.argv[1]; src = open(p, encoding="utf-8").read()
is_v6 = ("/task/start" in src) and ("RUNS" in src)
print("   версия в репо:", "v6 (фоновые задачи)" if is_v6 else "v5 (старая)")
if not is_v6:
    patches = [
      ("from flask import Flask, request, jsonify, Response, render_template_string",
       "from flask import Flask, request, jsonify, Response, render_template_string, make_response"),
      ("import urllib.request", "import urllib.request\nfrom contextlib import closing"),
      ('    if not CFG["web_password"]: return', '    return  # auth disabled by reinstall'),
      ("def ollama_stream(path, payload, timeout=600):", "def ollama_stream(path, payload, timeout=3600):"),
      ('"options": {"num_ctx": CFG["num_ctx"], "temperature": 0.2}',
       '"options": {"num_ctx": (4096 if "32b" in CFG["model"] else 8192), "num_predict": 700, "temperature": 0.2}'),
      ('"limit": CFG["num_ctx"]', '"limit": (4096 if "32b" in CFG["model"] else 8192)'),
      ("with db() as c:", "with closing(db()) as c:"),
      ('        if a == "write":\n            p = act.get("path", "")',
       '        if a == "write":\n            p = act.get("path", "")\n            p = p if os.path.isabs(p) else os.path.join(WORKSPACE, p)'),
      ('        if a == "read":\n            p = act.get("path", "")',
       '        if a == "read":\n            p = act.get("path", "")\n            p = p if os.path.isabs(p) else os.path.join(WORKSPACE, p)'),
      ("def models():\n    ms = []", "def models():\n    ensure_model()\n    ms = []"),
    ]
    n = 0
    for old, new in patches:
        if old in src: src = src.replace(old, new); n += 1
    open(p, "w", encoding="utf-8").write(src)
    print(f"   применено хотфиксов: {n}/10 (вход отключён, таймауты, closing, пути, num_ctx)")
    print("   ВНИМАНИЕ: фоновых задач и loop-guard в v5 нет — залей v6 в репозиторий для полной автономности")
try:
    py_compile.compile(p, doraise=True)
    print("   синтаксис: OK")
except Exception as e:
    print("   ОШИБКА синтаксиса:", e); sys.exit(1)
PY
[ $? -eq 0 ] || { say "${R}Код битый, откат${N}"; cp -a "$BASE/backup-$TS/agent.py" "$BASE/agent.py" 2>/dev/null || true; exit 1; }

say "${Y}[8/9] Конфиг и systemd${N}"
python3 - "$BASE" "$MODEL" "$TS" <<'PY'
import json, os, sys
base, model, ts = sys.argv[1:4]
cfg = {}
old = os.path.join(base, "backup-"+ts, "config.json")
if os.path.exists(old):
    try: cfg = json.load(open(old))
    except Exception: cfg = {}
cfg.pop("web_password", None)
cfg["model"] = model
cfg.setdefault("num_ctx", 0)
cfg.setdefault("ssh", {}); cfg.setdefault("default_host", "")
p = os.path.join(base, "config.json")
json.dump(cfg, open(p, "w"), indent=2, ensure_ascii=False)
os.chmod(p, 0o600)
print("   конфиг записан, SSH-настройки сохранены:", ", ".join(cfg["ssh"].keys()) or "нет")
PY
cat > /etc/systemd/system/ai-agent.service <<'EOF'
[Unit]
Description=AI Agent
After=network-online.target ollama.service
Wants=ollama.service
[Service]
User=root
WorkingDirectory=/opt/ai-agent
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/ai-agent/agent.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable ai-agent >/dev/null 2>&1
systemctl restart ai-agent

say "${Y}[9/9] Проверка запуска${N}"
sleep 4
CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ || echo 000)
MODELS=$(curl -s http://127.0.0.1:8000/models | head -c 300 || echo "нет ответа")
IP=$(hostname -I | awk '{print $1}')
echo "   HTTP /: $CODE"
echo "   /models: $MODELS"
if [ "$CODE" = "200" ]; then
  say "${G}ГОТОВО: http://$IP:8000  (модель $MODEL)${N}"
else
  say "${R}Агент не ответил. Смотри: journalctl -u ai-agent -n 50 --no-pager${N}"
fi
say "   Бэкап: $BASE/backup-$TS"
say "   Диагностика: bash diag.sh"
