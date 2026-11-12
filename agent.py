#!/usr/bin/env python3
# AI Agent v6.0 — автономный агент: фоновые задачи, защита от циклов, без входа по паролю
import json, os, re, time, sqlite3, shutil, subprocess, threading, urllib.request, html as htmllib
from contextlib import closing
from flask import Flask, request, jsonify, Response, render_template_string
import paramiko

BASE = "/opt/ai-agent"
WORKSPACE = BASE + "/workspace"
DB = BASE + "/agent.db"
CFG_PATH = BASE + "/config.json"
OLLAMA = "http://127.0.0.1:11434"
MAX_STEPS = 12          # шагов на задачу
MAX_SAME_FAIL = 3       # подряд одинаковых падений -> стоп
STEP_TOKENS = 700       # лимит токенов на шаг

os.makedirs(WORKSPACE, exist_ok=True)
app = Flask(__name__)

# ---------------- КОНФИГ ----------------
def load_cfg():
    cfg = {}
    if os.path.exists(CFG_PATH):
        try: cfg = json.load(open(CFG_PATH))
        except Exception: cfg = {}
    cfg.pop("web_password", None)          # вход по паролю удалён
    cfg.setdefault("model", "qwen2.5-coder:14b")
    cfg.setdefault("num_ctx", 0)           # 0 = авто
    cfg.setdefault("ssh", {})
    cfg.setdefault("default_host", "")
    return cfg

def save_cfg():
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w") as f: json.dump(CFG, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CFG_PATH)
    try: os.chmod(CFG_PATH, 0o600)
    except Exception: pass

CFG = load_cfg()
STOP = set()
RUNS = {}   # sid -> {"events": [], "cond": Condition, "active": bool}

def ctx_limit():
    if CFG.get("num_ctx"): return int(CFG["num_ctx"])
    return 4096 if "32b" in CFG["model"] else 8192

def jbody(): return request.get_json(silent=True) or {}

# ---------------- БАЗА ----------------
def db():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, role TEXT, content TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
    c.execute("CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, sid TEXT, status TEXT, info TEXT, ts DATETIME DEFAULT CURRENT_TIMESTAMP)")
    return c

def save_msg(sid, role, content):
    with closing(db()) as c, c:
        c.execute("INSERT INTO messages(sid,role,content) VALUES(?,?,?)", (sid, role, content))

def set_task(sid, status, info=""):
    with closing(db()) as c, c:
        c.execute("INSERT INTO tasks(sid,status,info) VALUES(?,?,?)", (sid, status, info[:300]))

SYSTEM = """Ты — автономный инженер-агент на Linux-сервере. Ты САМ выполняешь задачи до конца: файлы, команды, пакеты, SSH, git, веб.
Отвечай СТРОГО одним JSON-объектом на шаг, без markdown и пояснений.
Форматы действий:
{"action":"exec","command":"..."}
{"action":"read","path":"..."}
{"action":"write","path":"...","content":"..."}
{"action":"ssh","command":"..."}
{"action":"install","package":"..."}
{"action":"git","command":"status|commit|push|pull|clone","message":"...","url":"..."}
{"action":"fetch","url":"..."}
{"action":"disk","type":"info|smart|errors|memory|fsck|speed"}
{"action":"reply","text":"..."}
{"action":"done","result":"..."}
ПРАВИЛА:
- Один шаг = один JSON. Получил результат — делай следующий шаг, пока задача не решена.
- SSH: хост, логин и пароль УЖЕ хранит система. НЕ указывай их в JSON и НИКОГДА не отказывайся от SSH-задач.
- Не выдумывай вывод команд — жди реальный результат и проверяй его.
- Ошибка? Проанализируй причину, исправь и повтори. Не повторяй одну и ту же команду без изменений.
- Большие задачи разбивай на шаги: осмотр (ls/cat) -> изменения -> проверка.
- Завершил — {"action":"done","result":"краткий итог"}. Нужен разговор — {"action":"reply","text":"..."}.
- Рабочая папка: /opt/ai-agent/workspace.
"""

def build_history(sid):
    with closing(db()) as c:
        rows = c.execute("SELECT role,content FROM messages WHERE sid=? ORDER BY id DESC LIMIT 16", (sid,)).fetchall()
    rows.reverse()
    return [{"role": "system", "content": SYSTEM}] + [{"role": r, "content": t[:3500]} for r, t in rows]

# ---------------- OLLAMA ----------------
def ollama_stream(path, payload, timeout=3600):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            line = line.strip()
            if line: yield json.loads(line)

def ollama_get(path):
    with urllib.request.urlopen(OLLAMA + path, timeout=10) as r:
        return json.loads(r.read().decode())

def installed_models():
    try: return [m["name"] for m in ollama_get("/api/tags").get("models", [])]
    except Exception: return []

def ensure_model():
    models = installed_models()
    if not models: return False
    if CFG["model"] not in models:
        for pref in ("qwen2.5-coder:14b", "qwen2.5-coder:7b", "qwen2.5-coder:32b"):
            if pref in models:
                CFG["model"] = pref; save_cfg(); return True
        CFG["model"] = models[0]; save_cfg()
    return True

# ---------------- ПАРСЕР ДЕЙСТВИЙ ----------------
def parse_actions(text):
    t = re.sub(r"```(?:json)?", "", text or "").strip()
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

# ---------------- ИНСТРУМЕНТЫ ----------------
def resolve(p):
    p = os.path.expanduser(p or "")
    return p if os.path.isabs(p) else os.path.join(WORKSPACE, p)

def sh(cmd, timeout=180):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=WORKSPACE)
        out = r.stdout + (("\n[stderr] " + r.stderr) if r.stderr else "")
        return out, r.returncode
    except subprocess.TimeoutExpired: return f"Таймаут {timeout}с: {cmd}", -1
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
        _, out, err = s.exec_command(cmd, timeout=60)
        res = out.read().decode("utf-8", "replace")
        e = err.read().decode("utf-8", "replace")
        s.close()
        return (res + (("\n[stderr] " + e) if e else "")), 0
    except Exception as ex:
        return f"SSH ошибка ({h}): {ex}", -1

def fetch_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AI-Agent/6.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read(300000).decode("utf-8", "replace")
        if "<html" in raw.lower():
            raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
            raw = re.sub(r"(?s)<[^>]+>", " ", raw)
            raw = htmllib.unescape(raw)
            raw = re.sub(r"\s+", " ", raw)
        return raw[:8000], 0
    except Exception as e:
        return f"fetch ошибка: {e}", -1

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
    except SyntaxError as e: return f"[err] синтаксическая ошибка: {e}"
    bak = f"{BASE}/agent.py.bak.{int(time.time())}"
    shutil.copy2(f"{BASE}/agent.py", bak)
    with open(f"{BASE}/agent.py", "w") as f: f.write(code)
    return f"[ok] код обновлён (бэкап {bak}). Нажми 'Перезапустить агента'."

def handle(act):
    a = act.get("action", "")
    try:
        if a == "exec":
            out, code = sh(act.get("command", ""))
            return f"[{'ok' if code == 0 else 'err'}] {act.get('command')}\n{out}"
        if a == "read":
            with open(resolve(act.get("path")), encoding="utf-8", errors="replace") as f:
                return f.read()[:6000]
        if a == "write":
            p = resolve(act.get("path"))
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "w", encoding="utf-8") as f: f.write(act.get("content", ""))
            return f"[ok] записан {p} ({len(act.get('content',''))} байт)"
        if a == "ssh":
            out, code = ssh_run(act.get("host", ""), act.get("command", ""))
            return f"[{'ok' if code == 0 else 'err'}] ssh {act.get('command')}\n{out}"
        if a == "install":
            out, code = sh(f"apt-get install -y {act.get('package','')}", 300)
            return f"[{'ok' if code == 0 else 'err'}] install\n{out}"
        if a == "git":
            c = act.get("command", "status")
            if c == "commit":
                sh("git add -A")
                out, code = sh(f'git commit -m "{act.get("message","agent")}"')
            elif c == "clone":
                out, code = sh(f'git clone {act.get("url","")} .', 300)
            else:
                out, code = sh(f"git {c}")
            return f"[{'ok' if code == 0 else 'err'}] git {c}\n{out}"
        if a == "fetch":
            out, code = fetch_url(act.get("url", ""))
            return f"[{'ok' if code == 0 else 'err'}] fetch\n{out}"
        if a == "disk": return disk_check(act.get("type", "info"))
        if a == "self_improve": return self_improve(act.get("code", ""))
        return f"[err] неизвестное действие {a}"
    except Exception as e:
        return f"[err] ошибка выполнения: {e}"

# ---------------- ФОНОВЫЕ ЗАДАЧИ ----------------
def emit(sid, d):
    run = RUNS.get(sid)
    if not run: return
    with run["cond"]:
        run["events"].append(d)
        run["cond"].notify_all()

def worker(sid):
    run = RUNS[sid]
    set_task(sid, "running")
    fails, last_sig, steps = 0, None, 0
    try:
        while steps < MAX_STEPS and sid not in STOP:
            full = ""
            try:
                payload = {"model": CFG["model"], "messages": build_history(sid), "stream": True,
                           "options": {"num_ctx": ctx_limit(), "num_predict": STEP_TOKENS, "temperature": 0.2}}
                for ch in ollama_stream("/api/chat", payload):
                    if sid in STOP: break
                    c = ch.get("message", {}).get("content", "")
                    if c:
                        full += c
                        emit(sid, {"type": "token", "content": c})
                    if ch.get("done"):
                        ec, ed = ch.get("eval_count", 0), ch.get("eval_duration", 1) or 1
                        emit(sid, {"type": "metrics", "speed": round(ec / (ed / 1e9), 1),
                                   "used": ch.get("prompt_eval_count", 0) + ec, "limit": ctx_limit()})
            except Exception as e:
                emit(sid, {"type": "error", "text": f"Ollama: {e}"})
                set_task(sid, "error", str(e)); return
            if sid in STOP:
                save_msg(sid, "assistant", full + " [остановлено]")
                set_task(sid, "stopped"); emit(sid, {"type": "stopped"}); return
            save_msg(sid, "assistant", full)
            acts = parse_actions(full)
            if not acts:
                emit(sid, {"type": "reply", "text": full or "(пустой ответ модели)"})
                set_task(sid, "done", "reply"); return
            terminal = False
            for act in acts:
                a = act.get("action")
                if a in ("reply", "done"):
                    emit(sid, {"type": "reply", "text": act.get("text") or ("✅ " + act.get("result", "Готово"))})
                    terminal = True; break
                sig = json.dumps(act, sort_keys=True, ensure_ascii=False)
                emit(sid, {"type": "action", "action": a, "args": act})
                res = handle(act)
                emit(sid, {"type": "result", "text": res[:4000]})
                save_msg(sid, "user", f"[результат {a}]\n{res[:3000]}")
                if res.startswith("[err]"):
                    fails = fails + 1 if sig == last_sig else 1
                else:
                    fails = 0
                last_sig = sig
                if fails >= MAX_SAME_FAIL:
                    emit(sid, {"type": "reply", "text": f"⚠️ Одно и то же действие падает {fails} раз подряд. Останавливаюсь, чтобы не зациклиться. Последний вывод:\n{res[:800]}"})
                    set_task(sid, "done", "loop-guard"); return
            if terminal:
                set_task(sid, "done"); return
            steps += 1
        if sid in STOP:
            set_task(sid, "stopped"); emit(sid, {"type": "stopped"}); return
        set_task(sid, "done", "limit")
        emit(sid, {"type": "reply", "text": "Лимит шагов исчерпан. Нажми 'Продолжить задачу', чтобы агент доделал."})
    finally:
        with run["cond"]:
            run["active"] = False
            run["cond"].notify_all()
        emit(sid, {"type": "end"})

def start_task(sid, message=None):
    run = RUNS.get(sid)
    if run and run["active"]:
        return False, "Задача уже выполняется в этой сессии"
    STOP.discard(sid)
    RUNS[sid] = {"events": [], "cond": threading.Condition(), "active": True}
    if message: save_msg(sid, "user", message)
    threading.Thread(target=worker, args=(sid,), daemon=True).start()
    return True, "started"

def sse(d): return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

# ---------------- МАРШРУТЫ ----------------
@app.route("/")
def home():
    boot = {"model": CFG["model"], "sid": str(int(time.time())), "num_ctx": ctx_limit(),
            "default_host": CFG.get("default_host", ""),
            "ssh": {h: {"user": c.get("user", "root"), "port": c.get("port", 22)} for h, c in CFG["ssh"].items()}}
    return render_template_string(HTML, boot=json.dumps(boot, ensure_ascii=False))

@app.route("/task/start", methods=["POST"])
def task_start():
    d = jbody()
    sid = d.get("sid") or str(int(time.time()))
    if not ensure_model():
        return jsonify({"ok": False, "msg": "Ollama недоступна или нет моделей"})
    ok, msg = start_task(sid, d.get("message", ""))
    return jsonify({"ok": ok, "msg": msg, "sid": sid})

@app.route("/task/resume", methods=["POST"])
def task_resume():
    sid = jbody().get("sid", "")
    ok, msg = start_task(sid, "Продолжи прерванную задачу с того места, где остановился. Проверь текущее состояние и доделай.")
    return jsonify({"ok": ok, "msg": msg})

@app.route("/task/status")
def task_status():
    sid = request.args.get("sid", "")
    run = RUNS.get(sid)
    return jsonify({"active": bool(run and run["active"]), "n": len(run["events"]) if run else 0})

@app.route("/task/stream")
def task_stream():
    sid = request.args.get("sid", "")
    try: start = int(request.args.get("from", 0))
    except ValueError: start = 0
    def gen():
        run = RUNS.get(sid)
        if not run:
            yield sse({"type": "end"}); return
        i = start
        while True:
            with run["cond"]:
                while i >= len(run["events"]) and run["active"]:
                    run["cond"].wait(timeout=15)
                batch = run["events"][i:]
                i = len(run["events"])
                active = run["active"]
            for d in batch: yield sse(d)
            if not active and i >= len(run["events"]):
                yield sse({"type": "end"}); return
            if not batch: yield ": keepalive\n\n"
    return Response(gen(), mimetype="text/event-stream")

@app.route("/stop", methods=["POST"])
def stop():
    STOP.add(jbody().get("sid", "")); return jsonify({"ok": True})

@app.route("/models")
def models():
    ensure_model()
    ms = []
    try: ms = [{"name": m["name"], "size": round(m.get("size", 0) / 1e9, 1)} for m in ollama_get("/api/tags")["models"]]
    except Exception: pass
    return jsonify({"models": ms, "current": CFG["model"], "ctx": ctx_limit()})

@app.route("/model/switch", methods=["POST"])
def switch():
    name = jbody().get("name", "")
    if name not in installed_models(): return jsonify({"ok": False, "msg": "Модель не установлена"})
    old = CFG["model"]
    CFG["model"] = name; save_cfg()
    try:
        urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/generate",
            data=json.dumps({"model": old, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60)
    except Exception: pass
    return jsonify({"ok": True, "msg": f"{old} → {name}", "model": name})

@app.route("/model/pull", methods=["POST"])
def pull():
    name = jbody().get("name", "")
    def gen():
        try:
            for ch in ollama_stream("/api/pull", {"name": name, "stream": True}, timeout=7200):
                if ch.get("error"):
                    yield sse({"status": "error: " + ch["error"], "pct": 0}); return
                done, total = ch.get("completed", 0), ch.get("total", 0)
                yield sse({"status": ch.get("status", ""), "pct": round(done * 100 / total) if total else 0})
            yield sse({"status": "done", "pct": 100})
        except Exception as e:
            yield sse({"status": "error: " + str(e), "pct": 0})
    return Response(gen(), mimetype="text/event-stream")

@app.route("/model/delete", methods=["POST"])
def delete():
    name = jbody().get("name", "")
    try:
        urllib.request.urlopen(urllib.request.Request(OLLAMA + "/api/delete",
            data=json.dumps({"name": name}).encode(),
            headers={"Content-Type": "application/json"}, method="DELETE"), timeout=30)
        ensure_model()
        return jsonify({"ok": True, "msg": f"{name} удалена"})
    except Exception as e: return jsonify({"ok": False, "msg": str(e)})

@app.route("/ssh/save", methods=["POST"])
def ssh_save():
    d = jbody()
    h = (d.get("host") or "").strip()
    if not h: return jsonify({"ok": False, "msg": "Пустой хост"})
    CFG["ssh"][h] = {"user": d.get("user", "root"), "password": d.get("password", ""), "port": int(d.get("port") or 22)}
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
    with closing(db()) as c:
        rows = c.execute("SELECT sid, MAX(ts) t, COUNT(*) n FROM messages GROUP BY sid ORDER BY t DESC LIMIT 25").fetchall()
    return jsonify({"sessions": [{"sid": r[0], "ts": r[1], "n": r[2],
                                  "active": bool(RUNS.get(r[0]) and RUNS[r[0]]["active"])} for r in rows]})

@app.route("/session")
def session():
    sid = request.args.get("sid", "")
    with closing(db()) as c:
        rows = c.execute("SELECT role,content FROM messages WHERE sid=? ORDER BY id", (sid,)).fetchall()
        task = c.execute("SELECT status,info FROM tasks WHERE sid=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    return jsonify({"history": [{"role": r[0], "content": r[1]} for r in rows],
                    "task": {"status": task[0], "info": task[1]} if task else None})

@app.route("/session/delete", methods=["POST"])
def session_delete():
    sid = jbody().get("sid", "")
    with closing(db()) as c, c:
        c.execute("DELETE FROM messages WHERE sid=?", (sid,))
        c.execute("DELETE FROM tasks WHERE sid=?", (sid,))
    RUNS.pop(sid, None)
    return jsonify({"ok": True})

@app.route("/restart", methods=["POST"])
def restart():
    subprocess.Popen("sleep 1; systemctl restart ai-agent", shell=True)
    return jsonify({"ok": True})

# ---------------- ВЕБ-ИНТЕРФЕЙС ----------------
HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Agent v6</title><style>
*{box-sizing:border-box;margin:0}body{font-family:system-ui,sans-serif;background:#0d1117;color:#c9d1d9;height:100vh;display:flex;flex-direction:column}
header{background:#161b22;border-bottom:1px solid #30363d;padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
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
.turn{align-self:flex-start;max-width:92%;width:100%}.think{color:#8b949e;font-size:12px;white-space:pre-wrap;margin:4px 0}
.card{background:#161b22;border:1px solid #1f6feb;border-radius:8px;margin:6px 0;overflow:hidden}
.card .h{padding:6px 10px;font-size:11px;color:#79c0ff;background:#1a1f2e}.card pre{padding:8px 10px;font-size:11px;overflow-x:auto;max-height:260px;overflow-y:auto;color:#7ee787;white-space:pre-wrap}
footer{background:#161b22;border-top:1px solid #30363d;padding:10px;display:flex;gap:8px}
footer textarea{flex:1;resize:none;min-height:44px;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:8px;padding:10px;font-size:13px}
footer button{width:auto;padding:10px 20px}
.mi{display:flex;align-items:center;gap:6px;background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:6px;margin-bottom:5px;font-size:11px}
.mi span{flex:1;overflow:hidden;text-overflow:ellipsis}.mi button{width:auto;margin:0;padding:3px 8px;font-size:10px}
#pull{font-size:11px;color:#58a6ff;margin:4px 0;white-space:pre-wrap}
.row{display:flex;gap:4px;flex-wrap:wrap}.row button{width:auto}
</style></head><body>
<header><h1>🤖 AI Agent v6</h1><span class="st">Модель: <b id="hm">...</b></span><span class="st">Скорость: <b id="hs">0</b> ток/с</span><span class="st">Контекст: <span id="hc">0</span></span><div class="ctx"><i id="hb"></i></div><span class="st" id="hst">Готов</span></header>
<main><aside>
<h3>Модель</h3><select id="msel"></select>
<div id="mlist"></div>
<h3>Скачать модель</h3><input id="mname" placeholder="qwen2.5-coder:14b">
<button class="b" data-act="pull">⬇️ Скачать</button>
<div class="row">
<button data-act="pullname" data-arg="qwen2.5-coder:7b">7B</button>
<button data-act="pullname" data-arg="qwen2.5-coder:14b">14B</button>
<button data-act="pullname" data-arg="qwen2.5-coder:32b">32B</button>
<button data-act="pullname" data-arg="qwen2.5-coder:32b-q3_K_M">32B-Q3</button>
<button data-act="pullname" data-arg="llama3.1:8b">Llama8B</button>
<button data-act="pullname" data-arg="mistral-nemo:12b">Nemo12B</button></div>
<div id="pull"></div>
<h3>SSH</h3><input id="sh" placeholder="Хост IP"><input id="su" placeholder="Пользователь" value="root"><input id="sp" type="number" placeholder="Порт" value="22"><input id="sw" type="password" placeholder="Пароль">
<button data-act="sshsave">💾 Сохранить SSH</button><button data-act="sshtest">🔌 Проверить SSH</button>
<h3>Задачи</h3><button class="g" data-act="new">🆕 Новый диалог</button>
<button class="r" id="stopb" style="display:none" data-act="stop">🛑 Остановить</button>
<button class="b" data-act="resume">▶️ Продолжить задачу</button>
<button data-act="quick" data-arg="Покажи файлы в workspace и скажи что там">📁 Файлы</button>
<button data-act="quick" data-arg="Проверь диск, память и логи, скажи есть ли проблемы и исправь что можно">🩺 Диагностика</button>
<button class="o" data-act="restart">♻️ Перезапустить агента</button>
<h3>Проверка диска</h3>
<button class="o" data-act="disk" data-arg="info">📊 Информация</button><button class="o" data-act="disk" data-arg="smart">❤️ SMART</button>
<button class="o" data-act="disk" data-arg="errors">⚠️ Ошибки ядра</button><button class="o" data-act="disk" data-arg="memory">💾 Память</button>
<button class="o" data-act="disk" data-arg="fsck">🔧 fsck (read-only)</button><button class="o" data-act="disk" data-arg="speed">⚡ Скорость</button>
<div id="dres" class="card" style="display:none"><pre></pre></div>
<h3>История</h3><div id="sess"></div>
</aside>
<div id="chat"></div></main>
<footer><textarea id="inp" placeholder="Задача агенту... (Enter — отправить, Shift+Enter — новая строка)"></textarea><button class="g" id="sendb" data-act="send">Отправить</button></footer>
<script>
const BOOT = {{ boot|safe }};
let sid = localStorage.getItem('sid') || BOOT.sid, busy = false, turn = null, think = null;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const api = (u,o) => fetch(u,o);
const post = (u,b) => fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(r=>r.json());
function scroll(){ $('chat').scrollTop = 1e9; }
function bubble(t,c){ const d=document.createElement('div'); d.className='msg '+c; d.textContent=t; $('chat').appendChild(d); scroll(); return d; }
function newTurn(){ turn=document.createElement('div'); turn.className='turn'; $('chat').appendChild(turn); think=null; return turn; }
function thinkBlock(){ if(!turn) newTurn(); if(!think || think.dataset.closed){ think=document.createElement('div'); think.className='think'; turn.appendChild(think); } return think; }
function card(title, body){ const c=document.createElement('div'); c.className='card'; c.innerHTML='<div class="h">'+esc(title)+'</div>'; const p=document.createElement('pre'); p.textContent=body; c.appendChild(p); (turn||newTurn()).appendChild(c); think=null; scroll(); return c; }
function renderHistory(m){
  if(m.role==='user'){
    if(m.content.startsWith('[результат')) card('результат', m.content);
    else bubble(m.content,'u');
  } else {
    let a=null; try{ a=JSON.parse(m.content); }catch(e){}
    if(a && a.action) card('⚙️ '+a.action, JSON.stringify(a,null,1));
    else bubble(m.content,'a');
  }
}
async function attach(from){
  busy=true; $('sendb').disabled=true; $('stopb').style.display='block'; $('hst').textContent='Работает...';
  if(!turn) newTurn();
  try{
    const r = await api('/task/stream?sid='+encodeURIComponent(sid)+'&from='+(from||0));
    const rd=r.body.getReader(), dec=new TextDecoder(); let buf='';
    while(true){
      const {done,value}=await rd.read(); if(done) break;
      buf+=dec.decode(value,{stream:true});
      const lines=buf.split("\n"); buf=lines.pop();
      for(const ln of lines){
        if(!ln.startsWith('data: ')) continue;
        const d=JSON.parse(ln.slice(6));
        if(d.type==='token'){ thinkBlock().textContent+=d.content; scroll(); }
        else if(d.type==='action'){ if(think) think.dataset.closed='1'; card('⚙️ '+d.action, JSON.stringify(d.args,null,1)); }
        else if(d.type==='result'){ card('результат', d.text); }
        else if(d.type==='reply'){ if(think) think.dataset.closed='1'; bubble(d.text,'a'); }
        else if(d.type==='metrics'){ $('hs').textContent=d.speed; $('hc').textContent=d.used+'/'+d.limit; $('hb').style.width=Math.min(100,d.used*100/d.limit)+'%'; }
        else if(d.type==='error'){ bubble('❌ '+d.text,'a'); }
        else if(d.type==='stopped'){ bubble('🛑 Остановлено','a'); }
      }
    }
  }catch(e){ bubble('❌ связь: '+e,'a'); }
  busy=false; $('sendb').disabled=false; $('stopb').style.display='none'; $('hst').textContent='Готов'; turn=null; think=null; loadSessions();
}
async function send(){
  const text=$('inp').value.trim(); if(!text||busy) return;
  $('inp').value=''; localStorage.setItem('sid',sid);
  bubble(text,'u'); newTurn();
  const d=await post('/task/start',{sid,message:text});
  if(!d.ok){ bubble('⚠️ '+d.msg,'a'); turn=null; return; }
  attach(0);
}
async function loadModels(){
  const d=await (await api('/models')).json();
  $('hm').textContent=d.current;
  $('msel').innerHTML=d.models.map(m=>'<option value="'+esc(m.name)+'"'+(m.name===d.current?' selected':'')+'>'+esc(m.name)+'</option>').join('');
  $('mlist').innerHTML=d.models.map(m=>'<div class="mi"><span>'+esc(m.name)+' ('+m.size+' ГБ)</span>'+
    (m.name!==d.current?'<button class="g" data-act="switch" data-arg="'+esc(m.name)+'">▶</button><button class="r" data-act="delmodel" data-arg="'+esc(m.name)+'">🗑</button>':'<b style="color:#3fb950">✓</b>')+'</div>').join('');
}
async function pullModel(name){
  const n=name||$('mname').value.trim(); if(!n) return alert('Введи имя модели');
  $('pull').textContent='⏳ '+n+' ...';
  const r=await api('/model/pull',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})});
  const rd=r.body.getReader(), dec=new TextDecoder(); let buf='';
  while(true){ const {done,value}=await rd.read(); if(done) break;
    buf+=dec.decode(value,{stream:true}); const ls=buf.split("\n"); buf=ls.pop();
    for(const ln of ls) if(ln.startsWith('data: ')){ const d=JSON.parse(ln.slice(6)); $('pull').textContent=(d.pct>=100?'✅ ':'⏳ ')+n+' '+d.pct+'% '+d.status; } }
  loadModels();
}
async function loadSessions(){
  const d=await (await api('/sessions')).json();
  $('sess').innerHTML=d.sessions.map(s=>'<div class="mi"><span>'+esc(s.sid)+(s.active?' ⚡':'')+' ('+s.n+')</span><button data-act="session" data-arg="'+esc(s.sid)+'">открыть</button><button class="r" data-act="delsession" data-arg="'+esc(s.sid)+'">🗑</button></div>').join('');
}
async function loadSession(id){
  sid=id; localStorage.setItem('sid',sid); $('chat').innerHTML=''; turn=null; think=null;
  const d=await (await api('/session?sid='+encodeURIComponent(id))).json();
  d.history.forEach(renderHistory);
  const st=await (await api('/task/status?sid='+encodeURIComponent(id))).json();
  if(st.active){ newTurn(); attach(0); }
}
document.addEventListener('click', async e=>{
  const b=e.target.closest('[data-act]'); if(!b) return;
  const act=b.dataset.act, arg=b.dataset.arg;
  if(act==='send') send();
  else if(act==='stop') post('/stop',{sid});
  else if(act==='resume'){ const d=await post('/task/resume',{sid}); if(d.ok){ newTurn(); attach(0); } else alert(d.msg); }
  else if(act==='new'){ sid=String(Date.now()); localStorage.setItem('sid',sid); $('chat').innerHTML=''; turn=null; bubble('Новый диалог. Давай задачу.','a'); }
  else if(act==='quick'){ $('inp').value=arg; send(); }
  else if(act==='switch'){ const d=await post('/model/switch',{name:arg}); alert(d.msg); loadModels(); }
  else if(act==='delmodel'){ if(confirm('Удалить '+arg+'?')){ const d=await post('/model/delete',{name:arg}); alert(d.msg); loadModels(); } }
  else if(act==='pull') pullModel();
  else if(act==='pullname') pullModel(arg);
  else if(act==='sshsave'){ const d=await post('/ssh/save',{host:$('sh').value,user:$('su').value,port:$('sp').value,password:$('sw').value}); alert(d.msg); }
  else if(act==='sshtest'){ const d=await post('/ssh/test',{}); alert(d.msg); }
  else if(act==='disk'){ $('dres').style.display='block'; $('dres').querySelector('pre').textContent='⏳ '+arg+'...'; const d=await (await api('/disk?type='+arg)).json(); $('dres').querySelector('pre').textContent=d.result; }
  else if(act==='session') loadSession(arg);
  else if(act==='delsession'){ if(confirm('Удалить сессию '+arg+'?')){ await post('/session/delete',{sid:arg}); loadSessions(); } }
  else if(act==='restart'){ if(confirm('Перезапустить агента?')){ await post('/restart',{}); $('hst').textContent='Перезапуск...'; setTimeout(()=>location.reload(),5000); } }
});
$('msel').addEventListener('change', async e=>{ const d=await post('/model/switch',{name:e.target.value}); alert(d.msg); loadModels(); });
$('inp').addEventListener('keydown', e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); send(); } });
(async function init(){
  if(BOOT.default_host){ $('sh').value=BOOT.default_host; const c=BOOT.ssh[BOOT.default_host]; if(c){ $('su').value=c.user; $('sp').value=c.port; } }
  loadModels(); loadSessions();
  const saved=localStorage.getItem('sid');
  if(saved){ sid=saved; await loadSession(saved); }
})();
</script></body></html>"""

if __name__ == "__main__":
    ensure_model()
    print("=" * 60)
    print("AI Agent v6 | модель:", CFG["model"], "| контекст:", ctx_limit())
    print("Веб: http://0.0.0.0:8000 (без пароля)")
    print("=" * 60)
    app.run(host="0.0.0.0", port=8000, threaded=True)
