#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# URL твоего репозитория — ЗАМЕНИ НА СВОЙ!
REPO_URL="https://raw.githubusercontent.com/vasa57kam/ag/main/agent.py"

echo -e "${BLUE}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     🧠 Ultimate AI Agent v4.0 — GitHub Install               ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""

# === 1. ДИСК ===
echo -e "${YELLOW}[1/5] 🔍 Диск...${NC}"
ROOT_DEV=$(df / | tail -1 | awk '{print $1}')
DISK_NAME=$(echo $ROOT_DEV | sed 's/[0-9]*$//')
PART_NUM=$(echo $ROOT_DEV | grep -oE '[0-9]+$' || echo "1")
DISK_SIZE=$(lsblk -b -d -n -o SIZE $DISK_NAME 2>/dev/null || echo 0)
PART_SIZE=$(lsblk -b -n -o SIZE $ROOT_DEV 2>/dev/null || echo 0)
FREE_GB=$((($DISK_SIZE - $PART_SIZE) / 1024 / 1024 / 1024))

if [ "$FREE_GB" -gt 5 ]; then
    apt update > /dev/null 2>&1 && apt install -y cloud-guest-utils > /dev/null 2>&1
    if echo "$ROOT_DEV" | grep -q "/dev/mapper/"; then
        growpart $DISK_NAME $PART_NUM 2>/dev/null || true
        pvresize $DISK_NAME$PART_NUM 2>/dev/null || true
        lvextend -l +100%FREE $(df / | tail -1 | awk '{print $1}') 2>/dev/null || true
        resize2fs $(df / | tail -1 | awk '{print $1}') > /dev/null 2>&1 || true
    else
        growpart $DISK_NAME $PART_NUM 2>/dev/null || true
        resize2fs $ROOT_DEV > /dev/null 2>&1 || true
    fi
    echo -e "   ✅ Расширен."
else
    echo -e "   ⚠️ Расширение не требуется."
fi
AVAILABLE_DISK=$(df -BG / | tail -1 | awk '{print $4}' | sed 's/G//')
echo ""

# === 2. RAM И МОДЕЛЬ ===
echo -e "${YELLOW}[2/5] 🔍 RAM и модель...${NC}"
TOTAL_RAM=$(free -g | awk '/^Mem:/{print $2}')
echo -e "   💾 RAM: ${GREEN}${TOTAL_RAM} ГБ${NC} | 💿 Диск: ${GREEN}${AVAILABLE_DISK} ГБ${NC}"
echo ""
echo -e "${BLUE}Выбор модели:${NC}"
echo -e "   ${GREEN}1)${NC} Qwen 2.5 Coder 32B (Q4) — Макс. ум (~22 ГБ RAM)"
echo -e "   ${GREEN}2)${NC} Qwen 2.5 Coder 32B (Q3_K_M) — Ум + сжатие (~17 ГБ) ⭐"
echo -e "   ${GREEN}3)${NC} Qwen 2.5 Coder 14B — Баланс (~10 ГБ)"
echo -e "   ${GREEN}4)${NC} Qwen 2.5 Coder 7B — Скорость (~6 ГБ)"

while true; do
    read -p "👉 Номер (1-4): " CHOICE
    case $CHOICE in
        1) MODEL="qwen2.5-coder:32b"; REQ=22; break ;;
        2) MODEL="qwen2.5-coder:32b-q3_K_M"; REQ=17; break ;;
        3) MODEL="qwen2.5-coder:14b"; REQ=10; break ;;
        4) MODEL="qwen2.5-coder:7b"; REQ=6; break ;;
        *) echo -e "   ${RED}Неверно.${NC}" ;;
    esac
done

if [ "$TOTAL_RAM" -lt "$REQ" ]; then
    echo -e "   ${YELLOW}⚠️ RAM меньше рекомендуемой ($REQ ГБ). Модель может тормозить.${NC}"
    read -p "   Продолжить? (да/нет): " C
    if [[ "$C" != "да" && "$C" != "y" ]]; then exit 1; fi
fi
echo ""

# === 3. OLLAMA ===
echo -e "${YELLOW}[3/5] 🦙 Ollama...${NC}"
if ! command -v ollama &> /dev/null; then
    curl -fsSL https://ollama.com/install.sh | bash
    systemctl enable ollama && systemctl start ollama
fi
echo -e "   ⏳ Скачивание $MODEL (не закрывай терминал)..."
ollama pull $MODEL
echo -e "   ✅ Готово."
echo ""

# === 4. ЗАВИСИМОСТИ ===
echo -e "${YELLOW}[4/5] 📦 Зависимости...${NC}"
apt install -y python3-pip sqlite3 smartmontools e2fsprogs curl > /dev/null 2>&1
pip3 install flask openai paramiko flask-cors > /dev/null 2>&1
echo -e "   ✅ Готово."
echo ""

# === 5. СКАЧИВАЕМ АГЕНТА С GITHUB ===
echo -e "${YELLOW}[5/5] 🧠 Скачивание агента с GitHub...${NC}"
mkdir -p /opt/ai-agent/workspace

# Скачиваем agent.py с GitHub
curl -fsSL "$REPO_URL" -o /opt/ai-agent/agent.py

# Заменяем модель в коде на выбранную
sed -i "s|MODEL = \"qwen2.5-coder:14b\"|MODEL = \"$MODEL\"|g" /opt/ai-agent/agent.py

echo -e "   ✅ Скачан и настроен."
echo ""

# === 6. SYSTEMD ===
echo -e "${YELLOW}[6/6] ⚙️ Автозапуск...${NC}"
cat > /etc/systemd/system/ai-agent.service << 'SEOF'
[Unit]
Description=Ultimate AI Agent v4
After=network.target ollama.service
[Service]
User=root
WorkingDirectory=/opt/ai-agent
ExecStart=/usr/bin/python3 /opt/ai-agent/agent.py
Restart=always
RestartSec=10
[Install]
WantedBy=multi-user.target
SEOF
systemctl daemon-reload
systemctl enable ai-agent
systemctl restart ai-agent

IP=$(hostname -I | awk '{print $1}')
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                   ✅ УСТАНОВКА ЗАВЕРШЕНА!                    ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║${NC}  🌐 Открой: ${BLUE}http://$IP:8000${NC}                                ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  🧠 Модель: ${BLUE}$MODEL${NC}                                  ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  💾 История в SQLite | 🔄 Смена моделей | 💿 Проверка диска  ${GREEN}║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}📝 Для обновления агента в будущем:${NC}"
echo -e "   curl -fsSL $REPO_URL -o /opt/ai-agent/agent.py"
echo -e "   systemctl restart ai-agent"
echo ""
