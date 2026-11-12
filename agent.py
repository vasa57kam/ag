#!/usr/bin/env python3
# AI Agent v5.0 — автономный агент с веб-интерфейсом
import json, os, re, time, sqlite3, shutil, subprocess, hashlib, secrets
import urllib.request
from flask import Flask, request, jsonify, Response, render_template_string
import paramiko

BASE = "/opt/ai-agent"
WORKSPACE = BASE + "/workspace"
DB = BASE + "/agent.db"
CFG_PATH = BASE + "/config.json"
OLLAMA = "http://127.0.0.1:11434"
MAX_STEPS = 8

os.makedirs(WORKSPACE, exist_ok=True)
app = Flask(__name__)

# ---------- КОНФИГ ----------
def load_cfg():
    cfg = {}
    if os.path.exists(CFG_PATH):
        try: cfg = json.load(open(CFG_PATH))
        except Exception: cfg = {}
    cfg.setdefault("model", "qwen2.5-coder:14b")
    cfg.setdefault("num_ctx", 8192)
    cfg.setdefault("ssh", {})
    cfg.setdefault("default_host", "")
    cfg.setdefault("web_password", "")
    return cfg

def save_cfg():
    with open(CFG_PATH, "w") as f: json.dump(CFG, f, indent=2)
    try: os.chmod(CFG_PATH, 0o600)
    except Exception: pass

CFG = load_cfg()
if not CFG["web_password"]:
    CFG["web_password"] = secrets.token_urlsafe(6)
    save_cfg()
AUTH_TOKEN = hashlib.sha256(CFG["web_password"].encode()).hexdigest()

STOP = set()

# ---------- БАЗА ----------
def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, role TEXT, content TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, status TEXT, info TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
    return c

def save_msg(sid, role, content):
    with db() as c: c.execute("INSERT INTO messages(sid,role,content) VALUES(?,?,?)", (sid, role, content))

def set_task(sid, status, info=""):
    with db() as c: c.execute("INSERT INTO tasks(sid,status,info) VALUES(?,?,?)", (sid, status, info[:300]))

SYSTEM = """Ты — автономный инженер-агент на Linux-сервере. Ты САМ выполняешь задачи: файлы, команды, пакеты, SSH, git.
Отвечай СТРОГО одним JSON-объектом на шаг, без markdown и пояснений.
Форматы действий:
{"action":"exec","command":"..."}
{"action":"read","path":"..."}
{"action":"write","path":"...","content":"..."}
{"action":"ssh","command":"..."}
{"action":"install","package":"..."}
{"action":"git","command":"status|commit|push|pull","message":"..."}
{"action":"disk","type":"info|smart|errors|memory|fsck|speed"}
{"action":"reply","text":"..."}
{"action":"done","result":"..."}
ПРАВИЛА:
- Один шаг = один JSON. Получил результат — делай следующий шаг.
- SSH: хост, логин и пароль УЖЕ хранит система. НЕ указывай их в JSON и НИКОГДА не отказывайся от SSH-задач. Фразы "не могу запомнить пароль" запрещены.
- Не выдумывай вывод команд — жди реальный результат.
- Ошибка? Исправь и повтори (до 3 раз), потом reply с объяснением.
- Рабочая папка: /opt/ai-agent/workspace.
"""

def build_history(sid):
    with db() as c:
        rows = c.execute("SELECT role,content FROM messages WHERE sid=? ORDER BY id DESC LIMIT 14", (sid,)).fetchall()
    rows.reverse()
    msgs = [{"role": "system", "content": SYSTEM}]
    for r, t in rows:
        msgs.append({"role": r, "content": t[:3500]})
    return msgs

# ---------- OLLAMA (нативный API, реальные метрики) ----------
def ollama_stream(path, payload, timeout=600):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            line = line.strip()
            if line:
                yield json.loads(line)

def ollama_get(path):
    with urllib.request.urlopen(OLLAMA + path, timeout=10) as r:
        return json.loads(r.read().decode())

def installed_models():
    try: return [m["name"] for m in ollama_get("/api/tags").get("models", [])]
    except Exception: return []

def ensure_model():
    models = installed_models()
    if not models: return
    if CFG["model"] not in models:
        for pref in ("qwen2.5-coder:14b", "qwen2.5-coder:7b", "qwen2.5-coder:32b"):
            if pref in models:
                CFG["model"] = pref; save_cfg(); return
        CFG["model"] = models[0]; save_cfg()

# ---------- ПАРСЕР ДЕЙСТВИЙ (понимает несколько JSON) ----------
def parse_actions(text):
    t = re.sub(r"```(?:json)?", "", text).strip()
    dec = json.JSONDecoder()
    acts, i = [], 0
    while i < len(t):
        j = t.find("{", i)
        if j < 0: break
        try:
            obj, end = dec.raw_decode(t, j)
            if isinstance(obj, dict) and "action" in obj: acts.append(obj)
            i = end
        except ValueError:
            i = j + 1
    return acts

# ---------- ИНСТРУМЕНТЫ ----------
def sh(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=WORKSPACE)
        out = r.stdout + (("\n[stderr] " + r.stderr) if r.stderr else "")
        return out, r.returncode
    except subprocess.TimeoutExpired: return "Таймаут команды", -1
    except Exception as e: return str(e), -1

def ssh_creds(host):
    h = host if host in CFG["ssh"] else CFG.get("default_host", "")
    return h, CFG["ssh"].get(h, {})

def ssh_run(host, cmd):
    h, cr = ssh_creds(host)
    if not h or not cr.get("password"):
        return "SSH не настроен: введи хост/пароль слева и нажми 'Сохранить SSH'", -1
    try:
        s = paramiko.SSHClient()
        s.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        s.connect(h, port=int(cr.get("port", 22)), username=cr.get("user", "root"),
                  password=cr["password"], timeout=10, allow_agent=False, look_for_keys=False)
        _, out, err = s.exec_command(cmd, timeout=30)
        res = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        s.close()
        return (res + (("\n[stderr] " + e) if e else "")), 0
    except Exception as ex:
        return f"SSH ошибка ({h}): {ex}", -1

def root_dev():
    out, _ = sh("findmnt -no SOURCE /")
    return out.strip() or "/dev/sda2"

def disk_check(t):
    dev = root_dev()
    disk = re.sub(r"\d+$", "", dev) or "/dev/sda"
    checks = {
        "info": "df -h && df -i",
        "smart": f"smartctl -H {disk} 2>&1; smartctl -A {disk} 2>&1 | head -30",
        "errors": "dmesg | grep -iE 'error|fail|bad|corrupt' | tail -40",
        "memory": "free -h && swapon --show",
        "fsck": f"fsck -n {dev} 2>&1 | head -60",
        "speed": f"hdparm -t {disk} 2>&1",
    }
    if t not in checks: return "Типы: " + ", ".join(checks)
    out, _ = sh(checks[t], 120)
    return f"=== {t} ===\n{out}"

def self_improve(code):
    try: compile(code, "agent.py", "exec")
    except SyntaxError as e: return f"Отклонено: синтаксическая ошибка: {e}"
    bak = f"{BASE}/agent.py.bak.{int(time.time())}"
    shutil.copy2(f"{BASE}/agent.py", bak)
    with open(f"{BASE}/agent.py", "w") as f: f.write(code)
    return f"Код обновлён (бэкап {bak}). Нажми 'Перезапустить агента'."

def handle(act):
    a = act.get("action", "")
    try:
        if a == "exec":
            out, code = sh(act.get("command", ""))
            return f"[{'ok' if code == 0 else 'err'}] {act.get('command')}\n{out}"
        if a == "read":
            p = act.get("path", "")
            return open(p, encoding="utf-8", errors="replace").read()[:6000]
        if a == "write":
            p = act.get("path", "")
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            open(p, "w", encoding="utf-8").write(act.get("content", ""))
            return f"Записан {p}"
        if a == "ssh":
            out, code = ssh_run(act.get("host", ""), act.get("command", ""))
            return f"[{'ok' if code == 0 else 'err'}] ssh\n{out}"
        if a == "install":
            out, _ = sh(f"apt-get install -y {act.get('package','')}", 300)
            return out
        if a == "git":
            c = act.get("command", "status")
            if c == "commit":
                sh("git add -A")
                out, _ = sh(f'git commit -m "{act.get("message","agent")}"')
                return out
            out, _ = sh(f"git {c}")
            return out
        if a == "disk": return disk_check(act.get("type", "info"))
        if a == "self_improve": return self_improve(act.get("code", ""))
        return f"Неизвестное действие {a}"
    except Exception as e:
        return f"Ошибка выполнения: {e}"

def sse(d): return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

# ---------- ЦИКЛ АГЕНТА ----------
def run_agent(sid):
    set_task(sid, "running")
    steps = 0
    while steps < MAX_STEPS and sid not in STOP:
        full, metrics = "", {}
        try:
            for ch in ollama_stream("/api/chat", {"model": CFG["model"], "messages": build_history(sid),
                                                  "stream": True,
                                                  "options": {"num_ctx": CFG["num_ctx"], "temperature": 0.2}}):
                if sid in STOP: break
                c = ch.get("message", {}).get("content", "")
                if c:
                    full += c
                    yield sse({"type": "token", "content": c})
                if ch.get("done"):
                    ec, ed = ch.get("eval_count", 0), ch.get("eval_duration", 1)
                    metrics = {"speed": round(ec / (ed / 1e9), 1) if ed else 0,
                               "used": ch.get("prompt_eval_count", 0) + ec,
                               "limit": CFG["num_ctx"]}
                    yield sse({"type": "metrics", **metrics})
        except Exception as e:
            yield sse({"type": "error", "text": str(e)}); return
        if sid in STOP:
            save_msg(sid, "assistant", full + " [остановлено]")
            set_task(sid, "stopped"); yield sse({"type": "stopped"}); return
        save_msg(sid, "assistant", full)
        acts = parse_actions(full)
        if not acts:
            yield sse({"type": "reply", "text": full})
            set_task(sid, "done", "reply"); return
        terminal = False
        for act in acts:
            a = act.get("action")
            if a in ("reply", "done"):
                txt = act.get("text") or ("✅ " + act.get("result", "Готово"))
                yield sse({"type": "reply", "text": txt})
                terminal = True; break
            yield sse({"type": "action", "action": a, "args": act})
            res = handle(act)
            yield sse({"type": "result", "text": res[:4000]})
            save_msg(sid, "user", f"[результат {a}]\n{res[:3000]}")
        if terminal:
            set_task(sid, "done"); return
        steps += 1
    set_task(sid, "done", "limit")
    yield sse({"type": "reply", "text": "Лимит шагов исчерпан. Уточни задачу."})

# ---------- АВТОРИЗАЦИЯ ----------
@app.before_request
def auth():
    if not CFG["web_password"]: return
    if request.path in ("/", "/login"): return
    if request.cookies.get("tok") != AUTH_TOKEN:
        return jsonify({"error": "auth"}), 401

@app.route("/login", methods=["POST"])
def login():
    if request.json.get("password") == CFG["web_password"]:
        r = make_response(jsonify({"ok": True}))
        r.set_cookie("tok", AUTH_TOKEN, httponly=True, max_age=86400 * 30)
        return r
    return jsonify({"ok": False}), 403

# ---------- МАРШРУТЫ ----------
@app.route("/")
def home():
    boot = {"model": CFG["model"], "sid": str(int(time.time())), "num_ctx": CFG["num_ctx"],
            "default_host": CFG.get("default_host", ""), "auth": bool(CFG["web_password"]),
            "ssh": {h: {"user": c.get("user", "root"), "port": c.get("port", 22)} for h, c in CFG["ssh"].items()}}
    return render_template_string(HTML, boot=json.dumps(boot, ensure_ascii=False))

@app.route("/chat", methods=["POST"])
def chat():
    d = request.json
    sid = d.get("sid", str(int(time.time())))
    STOP.discard(sid)
    save_msg(sid, "user", d.get("message", ""))
    return Response(run_agent(sid), mimetype="text/event-stream")

@app.route("/stop", methods=["POST"])
def stop():
    STOP.add(request.json.get("sid", "")); return jsonify({"ok": True})

@app.route("/models")
def models():
    ms = []
    try: ms = [{"name": m["name"], "size": round(m.get("size", 0) / 1e9, 1)} for m in ollama_get("/api/tags")["models"]]
    except Exception: pass
    return jsonify({"models": ms, "current": CFG["model"]})

@app.route("/model/switch", methods=["POST"])
def switch():
    name = request.json.get("name", "")
    if name not in installed_models(): return jsonify({"ok": False, "msg": "Модель не установлена"})
    old = CFG["model"]
    CFG["model"] = name; save_cfg()
    try:
        urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/generate",
            data=json.dumps({"model": old, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"}), timeout=30)
    except Exception: pass
    return jsonify({"ok": True, "msg": f"{old} → {name}", "model": name})

@app.route("/model/pull", methods=["POST"])
def pull():
    name = request.json.get("name", "")
    def gen():
        try:
            for ch in ollama_stream("/api/pull", {"name": name, "stream": True}, timeout=3600):
                done, total = ch.get("completed", 0), ch.get("total", 0)
                pct = round(done * 100 / total) if total else 0
                yield sse({"status": ch.get("status", ""), "pct": pct})
            yield sse({"status": "done", "pct": 100})
        except Exception as e:
            yield sse({"status": "error: " + str(e), "pct": 0})
    return Response(gen(), mimetype="text/event-stream")

@app.route("/model/delete", methods=["POST"])
def delete():
    name = request.json.get("name", "")
    try:
        urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/delete",
            data=json.dumps({"name": name}).encode(),
            headers={"Content-Type": "application/json"}, method="DELETE"), timeout=30)
        return jsonify({"ok": True, "msg": f"{name} удалена"})
    except Exception as e: return jsonify({"ok": False, "msg": str(e)})

@app.route("/ssh/save", methods=["POST"])
def ssh_save():
    d = request.json
    h = d.get("host", "").strip()
    if not h: return jsonify({"ok": False, "msg": "Пустой хост"})
    CFG["ssh"][h] = {"user": d.get("user", "root"), "password": d.get("password", ""), "port": d.get("port", 22)}
    CFG["default_host"] = h; save_cfg()
    return jsonify({"ok": True, "msg": f"SSH сохранён: {h}"})

@app.route("/ssh/test", methods=["POST"])
def ssh_test():
    out, code = ssh_run("", "uptime && uname -a")
    return jsonify({"ok": code == 0, "msg": out})

@app.route("/disk")
def disk():
    return jsonify({"result": disk_check(request.args.get("type", "info"))})

@app.route("/sessions")
def sessions():
    with db() as c:
        rows = c.execute("SELECT sid, MAX(ts) t, COUNT(*) n FROM messages GROUP BY sid ORDER BY t DESC LIMIT 20").fetchall()
    return jsonify({"sessions": [{"sid": r[0], "ts": r[1], "n": r[2]} for r in rows]})

@app.route("/session")
def session():
    sid = request.args.get("sid", "")
    with db() as c:
        rows = c.execute("SELECT role,content FROM messages WHERE sid=? ORDER BY id", (sid,)).fetchall()
        task = c.execute("SELECT status,info FROM tasks WHERE sid=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    return jsonify({"history": [{"role": r[0], "content": r[1]} for r in rows],
                    "task": {"status": task[0], "info": task[1]} if task else None})

@app.route("/restart", methods=["POST"])
def restart():
    subprocess.Popen("sleep 1; systemctl restart ai-agent", shell=True)
    return jsonify({"ok": True})

# ---------- ВЕБ-ИНТЕРФЕЙС ----------
HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Agent v5</title><style>
*{box-sizing:border-box;margin:0}body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;height:100vh;display:flex;flex-direction:column}
header{background:#161b22;border-bottom:1px solid #30363d;padding:10px 16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;color:#58a6ff}.st{background:#21262d;border:1px solid #30363d;border-radius:6px;padding:3px 10px;font-size:12px}
.st b{color:#3fb950}.ctx{width:120px;height:6px;background:#21262d;border-radius:3px;overflow:hidden}.ctx i{display:block;height:100%;background:#d2a8ff;width:0}
main{flex:1;display:flex;overflow:hidden}aside{width:280px;background:#161b22;border-right:1px solid #30363d;padding:12px;overflow-y:auto}
aside h3{font-size:11px;color:#8b949e;text-transform:uppercase;margin:12px 0 6px}
button{width:100%;padding:7px;margin-bottom:5px;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;cursor:pointer;font-size:12px;text-align:left}
button:hover{border-color:#58a6ff}button.g{background:#238636;color:#fff}button.r{background:#da3633;color:#fff}button.b{background:#1f6feb;color:#fff}button.o{background:#9e6a03;color:#fff}
input,select{width:100%;padding:6px;margin-bottom:5px;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:5px;font-size:12px}
#chat{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px}
.msg{max-width:85%;padding:10px 14px;border-radius:10px;font-size:13px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word}
.u{align-self:flex-end;background:#1f6feb;color:#fff}.a{align-self:flex-start;background:#21262d;border:1px solid #30363d}
.turn{align-self:flex-start;max-width:90%;width:100%}.think{color:#8b949e;font-size:12px;white-space:pre-wrap;margin:4px 0}
.card{background:#161b22;border:1px solid #1f6feb;border-radius:8px;margin:6px 0;overflow:hidden}
.card .h{padding:6px 10px;font-size:11px;color:#79c0ff;background:#1a1f2e}.card pre{padding:8px 10px;font-size:11px;overflow-x:auto;max-height:260px;overflow-y:auto;color:#7ee787}
footer{background:#161b22;border-top:1px solid #30363d;padding:10px;display:flex;gap:8px}
footer textarea{flex:1;resize:none;min-height:44px;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:8px;padding:10px;font-size:13px}
footer button{width:auto;padding:10px 20px}
#login{position:fixed;inset:0;background:#0d1117;display:none;align-items:center;justify-content:center;z-index:9}
#login div{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:24px;width:300px}
.mi{display:flex;align-items:center;gap:6px;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:6px;margin-bottom:5px;font-size:11px}
.mi span{flex:1;overflow:hidden;text-overflow:ellipsis}.mi button{width:auto;margin:0;padding:3px 8px;font-size:10px}
#pull{font-size:11px;color:#58a6ff;margin:4px 0}
</style></head><body>
<div id="login"><div><h3 style="margin-bottom:10px">Вход</h3><input type="password" id="lp" placeholder="Пароль агента"><button class="g" onclick="doLogin()">Войти</button><div id="lerr" style="color:#f85149;font-size:11px;margin-top:6px"></div></div></div>
<header><h1>🤖 AI Agent v5</h1><span class="st">Модель: <b id="hm">...</b></span><span class="st">Скорость: <b id="hs">0</b> ток/с</span><span class="st">Контекст: <span id="hc">0</span></span><div class="ctx"><i id="hb"></i></div><span class="st" id="hst">Готов</span></header>
<main><aside>
<h3>Модель</h3><select id="msel" onchange="switchModel(this.value)"></select>
<div id="mlist"></div>
<h3>Скачать модель</h3><input id="mname" placeholder="qwen2.5-coder:14b">
<button class="b" onclick="pullModel()">⬇️ Скачать</button>
<div style="display:flex;gap:4px;flex-wrap:wrap">
<button style="width:auto" onclick="pullName('qwen2.5-coder:7b')">7B</button>
<button style="width:auto" onclick="pullName('qwen2.5-coder:14b')">14B</button>
<button style="width:auto" onclick="pullName('qwen2.5-coder:32b')">32B</button>
<button style="width:auto" onclick="pullName('qwen2.5-coder:32b-q3_K_M')">32B-Q3</button>
<button style="width:auto" onclick="pullName('llama3.1:8b')">Llama8B</button>
<button style="width:auto" onclick="pullName('mistral-nemo:12b')">Nemo12B</button></div>
<div id="pull"></div>
<h3>SSH</h3><input id="sh" placeholder="Хост IP"><input id="su" placeholder="Пользователь" value="root"><input id="sp" type="number" placeholder="Порт" value="22"><input id="sw" type="password" placeholder="Пароль">
<button onclick="sshSave()">💾 Сохранить SSH</button><button onclick="sshTest()">🔌 Проверить SSH</button>
<h3>Действия</h3><button class="g" onclick="newChat()">🆕 Новый диалог</button>
<button class="r" id="stopb" style="display:none" onclick="stopTask()">🛑 Остановить</button>
<button onclick="quick('Покажи файлы в workspace')">📁 Файлы</button>
<button onclick="quick('Проверь диск и память, скажи есть ли проблемы')">🩺 Диагностика</button>
<button class="o" onclick="restartAgent()">♻️ Перезапустить агента</button>
<h3>Проверка диска</h3>
<button class="o" onclick="disk('info')">📊 Информация</button><button class="o" onclick="disk('smart')">❤️ SMART</button>
<button class="o" onclick="disk('errors')">⚠️ Ошибки ядра</button><button class="o" onclick="disk('memory')">💾 Память</button>
<button class="o" onclick="disk('fsck')">🔧 fsck (read-only)</button><button class="o" onclick="disk('speed')">⚡ Скорость</button>
<div id="dres" class="card" style="display:none"><pre></pre></div>
<h3>История</h3><div id="sess"></div>
</aside>
<div id="chat"></div></main>
<footer><textarea id="inp" placeholder="Задача агенту... (Enter — отправить)" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();send()}"></textarea><button class="g" id="sendb" onclick="send()">Отправить</button></footer>
<script>
const BOOT = {{ boot|safe }};
let sid = localStorage.getItem('sid') || BOOT.sid, busy = false;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
async function api(url, opt) {
  const r = await fetch(url, opt);
  if (r.status === 401) { $('login').style.display = 'flex'; throw new Error('auth'); }
  return r;
}
async function doLogin() {
  const r = await fetch('/login', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({password: $('lp').value})});
  if (r.ok) { $('login').style.display = 'none'; init(); } else $('lerr').textContent = 'Неверный пароль';
}
function bubble(text, cls) { const d = document.createElement('div'); d.className = 'msg ' + cls; d.textContent = text; $('chat').appendChild(d); scroll(); return d; }
function scroll() { $('chat').scrollTop = 1e9; }
function newTurn() { const t = document.createElement('div'); t.className = 'turn'; $('chat').appendChild(t); return t; }
function thinkBlock(turn) { let t = turn.querySelector('.think:last-child'); if (!t || t.dataset.closed) { t = document.createElement('div'); t.className = 'think'; turn.appendChild(t); } return t; }
async function send() {
  const text = $('inp').value.trim(); if (!text || busy) return;
  busy = true; $('sendb').disabled = true; $('stopb').style.display = 'block'; $('inp').value = '';
  localStorage.setItem('sid', sid);
  bubble(text, 'u');
  const turn = newTurn(); let think = thinkBlock(turn);
  $('hst').textContent = 'Думает...';
  try {
    const r = await api('/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sid, message: text})});
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    while (true) {
      const {done, value} = await rd.read(); if (done) break;
      buf += dec.decode(value, {stream:true});
      const lines = buf.split('\\n'); buf = lines.pop();
      for (const ln of lines) {
        if (!ln.startsWith('data: ')) continue;
        const d = JSON.parse(ln.slice(6));
        if (d.type === 'token') { think.dataset.closed = ''; think.textContent += d.content; scroll(); }
        else if (d.type === 'action') {
          think.dataset.closed = '1';
          const c = document.createElement('div'); c.className = 'card';
          c.innerHTML = '<div class="h">⚙️ ' + esc(d.action) + '</div><pre>' + esc(JSON.stringify(d.args, null, 1)) + '</pre>';
          turn.appendChild(c); think = thinkBlock(turn); scroll();
        }
        else if (d.type === 'result') {
          const cards = turn.querySelectorAll('.card'); const pre = cards[cards.length-1].querySelector('pre:last-child');
          const rp = document.createElement('pre'); rp.textContent = d.text; cards[cards.length-1].appendChild(rp);
          think.dataset.closed = '1'; think = thinkBlock(turn); scroll();
        }
        else if (d.type === 'reply') { think.dataset.closed = '1'; bubble(d.text, 'a'); }
        else if (d.type === 'metrics') { $('hs').textContent = d.speed; $('hc').textContent = d.used + '/' + d.limit; $('hb').style.width = Math.min(100, d.used*100/d.limit) + '%'; }
        else if (d.type === 'error') { bubble('❌ ' + d.text, 'a'); }
        else if (d.type === 'stopped') { bubble('🛑 Остановлено', 'a'); }
      }
    }
  } catch(e) { if (e.message !== 'auth') bubble('❌ ' + e, 'a'); }
  busy = false; $('sendb').disabled = false; $('stopb').style.display = 'none'; $('hst').textContent = 'Готов'; loadSessions();
}
async function stopTask() { await api('/stop', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sid})}); }
function quick(t) { $('inp').value = t; send(); }
async function newChat() { sid = String(Date.now()); localStorage.setItem('sid', sid); $('chat').innerHTML = ''; bubble('Новый диалог. Давай задачу.', 'a'); }
async function loadModels() {
  const d = await (await api('/models')).json();
  $('hm').textContent = d.current;
  $('msel').innerHTML = d.models.map(m => '<option ' + (m.name===d.current?'selected':'') + '>' + esc(m.name) + '</option>').join('');
  $('mlist').innerHTML = d.models.map(m => '<div class="mi"><span>' + esc(m.name) + ' (' + m.size + ' ГБ)</span>' +
    (m.name!==d.current ? '<button class="g" onclick="switchModel(\\''+m.name+'\\')">▶</button><button class="r" onclick="delModel(\\''+m.name+'\\')">🗑</button>' : '<b style="color:#3fb950">✓</b>') + '</div>').join('');
}
async function switchModel(n) { const d = await (await api('/model/switch', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:n})})).json(); alert(d.msg); loadModels(); }
async function delModel(n) { if (!confirm('Удалить ' + n + '?')) return; const d = await (await api('/model/delete', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:n})})).json(); alert(d.msg); loadModels(); }
function pullName(n) { $('mname').value = n; pullModel(); }
async function pullModel() {
  const n = $('mname').value.trim(); if (!n) return alert('Введи имя модели');
  $('pull').textContent = '⏳ ' + n + '...';
  const r = await api('/model/pull', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name:n})});
  const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
  while (true) { const {done, value} = await rd.read(); if (done) break;
    buf += dec.decode(value, {stream:true}); const ls = buf.split('\\n'); buf = ls.pop();
    for (const ln of ls) if (ln.startsWith('data: ')) { const d = JSON.parse(ln.slice(6)); $('pull').textContent = (d.pct>=100?'✅ ':'⏳ ') + n + ' ' + d.pct + '% ' + d.status; } }
  loadModels();
}
async function sshSave() { const d = await (await api('/ssh/save', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({host:$('sh').value, user:$('su').value, port:$('sp').value, password:$('sw').value})})).json(); alert(d.msg); }
async function sshTest() { const d = await (await api('/ssh/test', {method:'POST'})).json(); alert(d.msg); }
async function disk(t) { $('dres').style.display = 'block'; $('dres').querySelector('pre').textContent = '⏳ ' + t + '...'; const d = await (await api('/disk?type=' + t)).json(); $('dres').querySelector('pre').textContent = d.result; }
async function loadSessions() { const d = await (await api('/sessions')).json(); $('sess').innerHTML = d.sessions.map(s => '<button onclick="loadSession(\\'' + s.sid + '\\')">💬 ' + s.sid + ' (' + s.n + ')</button>').join(''); }
async function loadSession(id) { sid = id; localStorage.setItem('sid', sid); $('chat').innerHTML = ''; const d = await (await api('/session?sid=' + id)).json(); d.history.forEach(m => bubble(m.content, m.role==='user'?'u':'a')); if (d.task) bubble('Статус задачи: ' + d.task.status, 'a'); }
async function restartAgent() { if (!confirm('Перезапустить агента?')) return; await api('/restart', {method:'POST'}); $('hst').textContent = 'Перезапуск...'; setTimeout(() => location.reload(), 4000); }
async function init() {
  if (BOOT.default_host) { $('sh').value = BOOT.default_host; const c = BOOT.ssh[BOOT.default_host]; if (c) { $('su').value = c.user; $('sp').value = c.port; } }
  loadModels(); loadSessions();
  const saved = localStorage.getItem('sid');
  if (saved) { try { await loadSession(saved); } catch(e) {} }
}
init();
</script></body></html>"""

if __name__ == "__main__":
    ensure_model()
    print("=" * 60)
    print("AI Agent v5 | модель:", CFG["model"])
    print("ПАРОЛЬ ВЕБ-ИНТЕРФЕЙСА:", CFG["web_password"])
    print("=" * 60)
    app.run(host="0.0.0.0", port=8000, threaded=True)
