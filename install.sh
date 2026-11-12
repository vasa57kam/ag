#!/bin/bash
set -e
G='\033[0;32m'; Y='\033[1;33m'; B='\033[0;34m'; N='\033[0m'
REPO="https://raw.githubusercontent.com/vasa57kam/ag/main"
echo -e "${B}=== AI Agent v6 install ===${N}"

# 1. Диск
ROOT_DEV=$(df / | tail -1 | awk '{print $1}')
DISK=$(echo $ROOT_DEV | sed 's/[0-9]*$//'); PART=$(echo $ROOT_DEV | grep -oE '[0-9]+$' || echo 1)
DS=$(lsblk -b -d -n -o SIZE $DISK 2>/dev/null || echo 0); PS=$(lsblk -b -n -o SIZE $ROOT_DEV 2>/dev/null || echo 0)
if [ $(( (DS-PS)/1024/1024/1024 )) -gt 5 ]; then
  apt update -qq && apt install -y -qq cloud-guest-utils
  growpart $DISK $PART || true
  if echo "$ROOT_DEV" | grep -q mapper; then
    pvresize ${DISK}${PART} || true; lvextend -l +100%FREE $ROOT_DEV || true
  fi
  resize2fs $ROOT_DEV || true
  echo -e "${G}диск расширен${N}"
fi

# 2. Модель
RAM=$(free -g | awk '/^Mem:/{print $2}')
echo -e "RAM: ${G}${RAM} ГБ${N}"
echo "1) 32B Q4 (~22 ГБ)  2) 32B Q3 (~17 ГБ)  3) 14B (~10 ГБ)  4) 7B (~6 ГБ)"
read -p "Модель [3]: " CH; CH=${CH:-3}
case $CH in
 1) MODEL=qwen2.5-coder:32b;; 2) MODEL=qwen2.5-coder:32b-q3_K_M;;
 4) MODEL=qwen2.5-coder:7b;; *) MODEL=qwen2.5-coder:14b;;
esac

# 3. Ollama + модель
command -v ollama >/dev/null || { curl -fsSL https://ollama.com/install.sh | bash; }
systemctl enable --now ollama
ollama pull $MODEL

# 4. Зависимости (openai больше не нужен!)
apt install -y -qq python3-pip sqlite3 smartmontools e2fsprogs curl git
pip3 install -q flask paramiko 2>/dev/null || pip3 install -q --break-system-packages flask paramiko

# 5. Агент + конфиг
mkdir -p /opt/ai-agent/workspace
curl -fsSL "$REPO/agent.py" -o /opt/ai-agent/agent.py
python3 - "$MODEL" <<'PY'
import json,sys,os
p="/opt/ai-agent/config.json"; cfg={}
if os.path.exists(p): cfg=json.load(open(p))
cfg["model"]=sys.argv[1]; cfg.setdefault("num_ctx",0); cfg.setdefault("ssh",{}); cfg.setdefault("default_host","")
cfg.pop("web_password",None)
json.dump(cfg,open(p,"w"),indent=2)
PY

# 6. Сервис
cat > /etc/systemd/system/ai-agent.service <<'EOF'
[Unit]
Description=AI Agent v6
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
systemctl daemon-reload && systemctl enable --now ai-agent
sleep 2
echo -e "${G}ГОТОВО: http://$(hostname -I | awk '{print $1}'):8000 | модель $MODEL${N}"
