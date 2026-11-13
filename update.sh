#!/bin/bash
# update.sh — подтянуть агента с GitHub, пропатчить до v6.2, перезапустить
# Запуск: bash update.sh    (или curl ... | bash)
set -u
REPO="https://raw.githubusercontent.com/vasa57kam/ag/main"
BASE=/opt/ai-agent
TS=$(date +%Y%m%d-%H%M%S)
G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
[ "$(id -u)" = "0" ] || { echo -e "${R}нужен root${N}"; exit 1; }
mkdir -p "$BASE/workspace"

echo -e "${Y}[1/5] Скачивание с GitHub${N}"
curl -fsSL "$REPO/agent.py?cb=$TS" -o "$BASE/agent.py.new" || { echo -e "${R}не скачалось${N}"; exit 1; }
head -c 200 "$BASE/agent.py.new" | grep -qi "<!DOCTYPE\|<html" && { echo -e "${R}GitHub отдал HTML вместо кода${N}"; exit 1; }
cp -a "$BASE/agent.py" "$BASE/agent.py.bak-$TS" 2>/dev/null || true

echo -e "${Y}[2/5] Патчи до v6.2 (идемпотентно)${N}"
python3 - "$BASE/agent.py.new" <<'PY'
import sys, py_compile
p = sys.argv[1]; src = open(p, encoding="utf-8").read()
P = [
 ("STEP_TOKENS = 700", "STEP_TOKENS = 1600"),
 ("MAX_SAME_FAIL = 3", "MAX_EVENTS = 4000; MAX_RUNS = 20\nMAX_SAME_FAIL = 3"),
 ("def build_history(sid):", "def est(t): return max(1, len(t)//3)\n\ndef build_history(sid):"),
 ('        rows = c.execute("SELECT role,content FROM messages WHERE sid=? ORDER BY id DESC LIMIT 16", (sid,)).fetchall()\n    rows.reverse()\n    return [{"role": "system", "content": SYSTEM}] + [{"role": r, "content": t[:3500]} for r, t in rows]',
  '        rows = c.execute("SELECT role,content FROM messages WHERE sid=? ORDER BY id DESC LIMIT 40", (sid,)).fetchall()\n'
  '    budget = max(512, ctx_limit() - STEP_TOKENS - est(SYSTEM) - 200)\n'
  '    out, used = [], 0\n'
  '    for r, t in rows:\n'
  '        t = t[:4000]; n = est(t)\n'
  '        if used + n > budget: break\n'
  '        out.append({"role": r, "content": t}); used += n\n'
  '    out.reverse()\n'
  '    return [{"role": "system", "content": SYSTEM}] + out'),
 ("def parse_actions(text):",
  'def looks_truncated(text):\n'
  '    t = (text or "").strip()\n'
  '    if not t.startswith("{"): return False\n'
  '    try: json.loads(t); return False\n'
  '    except Exception: return t.count("{") > t.count("}") or \'"content"\' in t\n\n'
  'def parse_actions(text):'),
 ('            if not acts:\n                emit(sid, {"type": "reply", "text": full or "(пустой ответ модели)"})\n                set_task(sid, "done", "reply"); return',
  '            if not acts:\n'
  '                if looks_truncated(full):\n'
  '                    save_msg(sid, "user", "[система] JSON оборван на середине. Сократи шаг: пиши файл частями. Повтори коротким JSON.")\n'
  '                    steps += 1; continue\n'
  '                emit(sid, {"type": "reply", "text": full or "(пустой ответ модели)"})\n'
  '                set_task(sid, "done", "reply"); return'),
 ("def disk_check(t):",
  'def parent_disk(dev):\n'
  '    pk, _ = sh(f"lsblk -no PKNAME {dev} 2>/dev/null")\n'
  '    if pk.strip(): return "/dev/" + pk.strip().splitlines()[0]\n'
  '    m = re.match(r"(/dev/nvme\\d+n\\d+)p\\d+$", dev)\n'
  '    if m: return m.group(1)\n'
  '    m = re.match(r"(/dev/[a-z]+)\\d+$", dev)\n'
  '    return m.group(1) if m else dev\n\n'
  'def disk_check(t):'),
 ('    disk = re.sub(r"\\d+$", "", dev) or "/dev/sda"', "    disk = parent_disk(dev)"),
 ('        run["events"].append(d)', '        if len(run["events"]) < MAX_EVENTS: run["events"].append(d)'),
 ("    STOP.discard(sid)\n", "    STOP.discard(sid)\n    for k in [k for k, v in RUNS.items() if not v['active']][:max(0, len(RUNS)-MAX_RUNS)]: RUNS.pop(k, None)\n"),
]
n = 0
for old, new in P:
    if old in src and new not in src:
        src = src.replace(old, new, 1); n += 1
open(p, "w", encoding="utf-8").write(src)
print(f"   применено патчей: {n}/10")
print("   версия:", "v6.2" if "MAX_EVENTS" in src else "v6.1/старая")
try:
    py_compile.compile(p, doraise=True); print("   синтаксис: OK")
except Exception as e:
    print("   ОШИБКА:", e); sys.exit(1)
PY
[ $? -eq 0 ] || { echo -e "${R}патч не прошёл, оставляю старую версию${N}"; rm -f "$BASE/agent.py.new"; exit 1; }
mv "$BASE/agent.py.new" "$BASE/agent.py"

echo -e "${Y}[3/5] Конфиг${N}"
python3 - <<'PY'
import json, os
p="/opt/ai-agent/config.json"; cfg={}
if os.path.exists(p):
    try: cfg=json.load(open(p))
    except Exception: cfg={}
for k in ("web_password","ssh_host","ssh_user"): cfg.pop(k,None)
cfg.setdefault("model","qwen2.5-coder:14b"); cfg["num_ctx"]=0
cfg.setdefault("ssh",{}); cfg.setdefault("default_host","")
json.dump(cfg,open(p,"w"),indent=2,ensure_ascii=False); os.chmod(p,0o600)
print("   модель:",cfg["model"],"| контекст: авто")
PY

echo -e "${Y}[4/5] Перезапуск${N}"
systemctl restart ai-agent; sleep 5

echo -e "${Y}[5/5] Проверка${N}"
FAIL=0
for u in "/" "/models" "/sessions" "/task/status?sid=update"; do
  C=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "http://127.0.0.1:8000$u")
  printf "   %-26s -> %s\n" "$u" "$C"; [ "$C" = "200" ] || FAIL=1
done
if [ "$FAIL" = "1" ]; then
  echo -e "${R}Что-то не отвечает — откат на предыдущую версию${N}"
  cp -a "$BASE/agent.py.bak-$TS" "$BASE/agent.py" 2>/dev/null && systemctl restart ai-agent
  echo "   смотри: journalctl -u ai-agent -n 40 --no-pager"
else
  echo -e "${G}Обновлено: http://$(hostname -I | awk '{print $1}'):8000${N}"
fi
