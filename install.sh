#!/bin/bash
# GitHub заменяет всё. Установка с нуля и обновление — одна команда.
#   curl -fsSL https://raw.githubusercontent.com/vasa57kam/ag/main/install.sh | bash
#   MODEL=qwen2.5-coder:32b bash install.sh     # своя модель
#   WIPE=1 bash install.sh                      # снести историю диалогов
set -u
REPO="${REPO:-https://raw.githubusercontent.com/vasa57kam/ag/main}"
BASE=/opt/ai-agent
TS=$(date +%Y%m%d-%H%M%S)
MODEL="${MODEL:-}"; WIPE="${WIPE:-0}"
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
say(){ echo -e "$1"; }
[ "$(id -u)" = "0" ] || { say "${R}нужен root${N}"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
mkdir -p "$BASE/workspace"

say "${Y}[1/7] пакеты${N}"
apt-get update -qq || true
apt-get install -y -qq python3-pip sqlite3 smartmontools e2fsprogs curl git cloud-guest-utils hdparm >/dev/null 2>&1 || true
pip3 install -q flask paramiko 2>/dev/null || pip3 install -q --break-system-packages flask paramiko || { say "${R}pip failed${N}"; exit 1; }

say "${Y}[2/7] диск${N}"
RD=$(df / | tail -1 | awk '{print $1}')
DK=$(echo "$RD" | sed 's/[0-9]*$//'); PT=$(echo "$RD" | grep -oE '[0-9]+$' || echo 1)
DS=$(lsblk -b -d -n -o SIZE "$DK" 2>/dev/null || echo 0)
PS=$(lsblk -b -n -o SIZE "$RD" 2>/dev/null || echo 0)
if [ $(( (DS-PS)/1024/1024/1024 )) -gt 5 ]; then
  growpart "$DK" "$PT" || true
  if echo "$RD" | grep -q mapper; then pvresize "${DK}${PT}" || true; lvextend -l +100%FREE "$RD" || true; fi
  resize2fs "$RD" || true; say "   расширен"
else say "   ок"; fi

say "${Y}[3/7] ollama${N}"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | bash
systemctl enable ollama >/dev/null 2>&1 || true
systemctl start ollama || true
for i in $(seq 1 40); do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done

say "${Y}[4/7] модель${N}"
RAM=$(free -g | awk '/^Mem:/{print $2}')
if [ -z "$MODEL" ] && [ -f "$BASE/config.json" ]; then
  MODEL=$(python3 -c "import json;print(json.load(open('$BASE/config.json')).get('model',''))" 2>/dev/null || echo "")
fi
if [ -z "$MODEL" ]; then
  if [ "${RAM:-0}" -lt 12 ]; then MODEL=qwen2.5-coder:7b; else MODEL=qwen2.5-coder:14b; fi
fi
say "   RAM ${RAM} ГБ → $MODEL"
ollama pull "$MODEL" || say "${Y}pull не удался${N}"

say "${Y}[5/7] код из GitHub (заменяет всё)${N}"
curl -fsSL "$REPO/agent.py?cb=$TS" -o "$BASE/agent.py.new" || { say "${R}не скачался agent.py${N}"; exit 1; }
if head -c 200 "$BASE/agent.py.new" | grep -qi "<!DOCTYPE\|<html\|404:"; then
  say "${R}GitHub отдал HTML/404 вместо кода${N}"; rm -f "$BASE/agent.py.new"; exit 1
fi
python3 -m py_compile "$BASE/agent.py.new" || { say "${R}код битый — оставляю старую версию${N}"; rm -f "$BASE/agent.py.new"; exit 1; }
[ -f "$BASE/agent.py" ] && cp -a "$BASE/agent.py" "$BASE/agent.py.bak-$TS"
mv "$BASE/agent.py.new" "$BASE/agent.py"
say "   $(grep -m1 '^# AI Agent' "$BASE/agent.py" || echo 'версия не указана')"

say "${Y}[6/7] конфиг + сервис${N}"
MODEL="$MODEL" WIPE="$WIPE" python3 - <<'PY'
import json, os
base="/opt/ai-agent"; p=base+"/config.json"; cfg={}
if os.path.exists(p):
    try: cfg=json.load(open(p))
    except Exception: cfg={}
for k in ("web_password","ssh_host","ssh_user"): cfg.pop(k,None)
cfg["model"]=os.environ["MODEL"]; cfg["num_ctx"]=0
cfg.setdefault("ssh",{}); cfg.setdefault("default_host","")
json.dump(cfg,open(p,"w"),indent=2,ensure_ascii=False); os.chmod(p,0o600)
if os.environ.get("WIPE")=="1" and os.path.exists(base+"/agent.db"):
    os.remove(base+"/agent.db"); print("   история удалена")
print("   модель:",cfg["model"],"| SSH:",", ".join(cfg["ssh"]) or "нет")
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
systemctl enable ai-agent >/dev/null 2>&1 || true
systemctl restart ai-agent

say "${Y}[7/7] проверка${N}"
sleep 5
FAIL=0
for u in "/" "/models" "/sessions" "/task/status?sid=install"; do
  C=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "http://127.0.0.1:8000$u")
  printf "   %-26s -> %s\n" "$u" "$C"; [ "$C" = "200" ] || FAIL=1
done
if [ "$FAIL" = "1" ] && [ -f "$BASE/agent.py.bak-$TS" ]; then
  say "${R}не поднялось — откат на предыдущую версию${N}"
  cp -a "$BASE/agent.py.bak-$TS" "$BASE/agent.py"; systemctl restart ai-agent
  say "   journalctl -u ai-agent -n 40 --no-pager"
fi

cat > /usr/local/bin/agent-sync <<'SYNC'
#!/bin/bash
curl -fsSL "https://raw.githubusercontent.com/vasa57kam/ag/main/install.sh?cb=$(date +%s)" -o /tmp/agent-install.sh && bash /tmp/agent-install.sh
SYNC
chmod +x /usr/local/bin/agent-sync

say "${G}http://$(hostname -I | awk '{print $1}'):8000${N}"
say "   обновление в любой момент: ${G}agent-sync${N}"
