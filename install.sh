#!/bin/bash
# install.sh — установка с нуля, всё тянется с GitHub
set -u
REPO="https://raw.githubusercontent.com/vasa57kam/ag/main"
BASE=/opt/ai-agent
[ "$(id -u)" = "0" ] || { echo "нужен root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
echo "[1/5] пакеты"
apt-get update -qq
apt-get install -y -qq python3-pip sqlite3 smartmontools e2fsprogs curl git cloud-guest-utils hdparm >/dev/null
pip3 install -q flask paramiko 2>/dev/null || pip3 install -q --break-system-packages flask paramiko

echo "[2/5] диск"
RD=$(df / | tail -1 | awk '{print $1}'); DK=$(echo $RD | sed 's/[0-9]*$//'); PT=$(echo $RD | grep -oE '[0-9]+$' || echo 1)
DS=$(lsblk -b -d -n -o SIZE $DK 2>/dev/null || echo 0); PS=$(lsblk -b -n -o SIZE $RD 2>/dev/null || echo 0)
if [ $(( (DS-PS)/1024/1024/1024 )) -gt 5 ]; then
  growpart $DK $PT || true
  echo "$RD" | grep -q mapper && { pvresize ${DK}${PT} || true; lvextend -l +100%FREE $RD || true; }
  resize2fs $RD || true
fi

echo "[3/5] ollama + модель"
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | bash
systemctl enable --now ollama >/dev/null 2>&1
for i in $(seq 1 30); do curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
RAM=$(free -g | awk '/^Mem:/{print $2}'); echo "   RAM ${RAM} ГБ"
echo "   1) 32b(~22ГБ) 2) 32b-q3(~17ГБ) 3) 14b(~10ГБ)[рек] 4) 7b(~6ГБ)"
read -r -p "   номер [3]: " CH </dev/tty || CH=3
case "${CH:-3}" in 1) M=qwen2.5-coder:32b;; 2) M=qwen2.5-coder:32b-q3_K_M;; 4) M=qwen2.5-coder:7b;; *) M=qwen2.5-coder:14b;; esac
ollama pull "$M"
mkdir -p "$BASE/workspace"
python3 - "$M" <<'PY'
import json,os,sys
p="/opt/ai-agent/config.json"; cfg={}
if os.path.exists(p):
    try: cfg=json.load(open(p))
    except Exception: cfg={}
cfg["model"]=sys.argv[1]; cfg["num_ctx"]=0
cfg.setdefault("ssh",{}); cfg.setdefault("default_host","")
json.dump(cfg,open(p,"w"),indent=2); os.chmod(p,0o600)
PY

echo "[4/5] сервис"
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
systemctl daemon-reload; systemctl enable ai-agent >/dev/null 2>&1

echo "[5/5] код с GitHub"
curl -fsSL "$REPO/update.sh" -o "$BASE/update.sh" && bash "$BASE/update.sh"
cp "$BASE/update.sh" /usr/local/bin/agent-update 2>/dev/null && chmod +x /usr/local/bin/agent-update
echo "Готово. Обновление в будущем: agent-update"
