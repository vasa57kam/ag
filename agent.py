import json, time, subprocess, os, re, sqlite3, shutil, threading, urllib.request
from flask import Flask, render_template_string, request, jsonify, Response, stream_with_context
from openai import OpenAI
import paramiko

app = Flask(__name__)

# === КОНФИГУРАЦИЯ ===
CONFIG_PATH = "/opt/ai-agent/config.json"
WORKSPACE = "/opt/ai-agent/workspace"
DB_PATH = "/opt/ai-agent/agent.db"

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    return {"model": "qwen2.5-coder:14b", "ssh_host": "192.168.0.33", "ssh_user": "root"}

def save_config(cfg):
    with open(CONFIG_PATH, 'w') as f:
        json.dump(cfg, f)

CONFIG = load_config()
MODEL = CONFIG.get("model", "qwen2.5-coder:14b")

AVAILABLE_MODELS = ["qwen2.5-coder:7b", "qwen2.5-coder:14b", "qwen2.5-coder:32b", "qwen2.5-coder:32b-q3_K_M"]
client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
ssh_passwords = {}
STOP_FLAG = False

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS tasks 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, status TEXT, action TEXT, progress TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    return conn

def get_history(session_id, limit=30):
    conn = init_db()
    cursor = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?", (session_id, limit))
    msgs = cursor.fetchall()
    msgs.reverse()
    return [{"role": "system", "content": """Ты — автономный ИИ-агент. Ты выполняешь действия через JSON.

ПРАВИЛА:
1. Отвечай ТОЛЬКО одним JSON-объектом, без текста до или после.
2. Никаких markdown, никаких пояснений.
3. После получения результата действия — продолжай работу.
4. Для SSH НЕ указывай password в JSON — сервер сам подставит сохранённый пароль.

ДОСТУПНЫЕ ДЕЙСТВИЯ (выбирай одно):
{"action":"exec","command":"команда"}
{"action":"ssh","command":"команда"}
{"action":"write","path":"/путь","content":"код"}
{"action":"read","path":"/путь"}
{"action":"install","package":"пакет"}
{"action":"git","command":"commit","message":"msg"}
{"action":"disk_check","type":"smart"}
{"action":"self_improve","code":"новый код"}
{"action":"reply","text":"ответ пользователю"}
{"action":"done","result":"итог"}

Пример правильного ответа: {"action":"exec","command":"ls -la"}
"""}] + [{"role": m[0], "content": m[1]} for m in msgs]

def save_msg(session_id, role, content):
    conn = init_db()
    conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, role, content))
    conn.commit()

def save_task(session_id, status, action, progress=""):
    conn = init_db()
    conn.execute("INSERT INTO tasks (session_id, status, action, progress) VALUES (?, ?, ?, ?)", 
                 (session_id, status, action, progress))
    conn.commit()

def get_last_task(session_id):
    conn = init_db()
    cursor = conn.execute("SELECT status, action, progress FROM tasks WHERE session_id=? ORDER BY id DESC LIMIT 1", (session_id,))
    return cursor.fetchone()

def run_cmd(cmd, timeout=60):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=WORKSPACE)
        out = res.stdout
        if res.stderr: out += "\n[STDERR]: " + res.stderr
        return out, res.returncode
    except Exception as e: return str(e), -1

def ssh_cmd(host, cmd, user, pwd, port=22):
    if STOP_FLAG: return "Остановлено", -1
    if not host or len(host) < 7 or "." not in host:
        return f"Неверный хост '{host}'. Укажи IP в настройках.", -1
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, port=port, username=user, password=pwd, timeout=10, allow_agent=False, look_for_keys=False)
        out, err = ssh.exec_command(cmd, timeout=20)
        res = out.read().decode('utf-8', errors='replace')
        e = err.read().decode('utf-8', errors='replace')
        if e: res += "\n[STDERR]: " + e
        ssh.close()
        return res, 0
    except Exception as ex: return f"SSH ошибка: {ex}", -1

def disk_check(check_type):
    checks = {
        "info": "df -h && echo '---INODE---' && df -i && echo '---MOUNT---' && mount | grep -E '^/dev'",
        "smart": "smartctl -H /dev/sda 2>&1 && echo '===ATTRIBUTES===' && smartctl -A /dev/sda 2>&1",
        "smart_full": "smartctl -a /dev/sda 2>&1",
        "errors": "dmesg | grep -iE 'error|fail|bad|corrupt|ext4.*error|xfs.*error' | tail -50",
        "fsck_readonly": "fsck -n /dev/sda2 2>&1 | head -100",
        "speed_read": "hdparm -t /dev/sda 2>&1",
        "logs": "journalctl -p err -b --no-pager | tail -50",
        "memory": "free -h && echo '---SWAP---' && swapon --show && echo '---VMSTAT---' && vmstat 1 3"
    }
    if check_type not in checks:
        return f"Неизвестный тип: {check_type}. Доступны: {', '.join(checks.keys())}"
    cmd = checks[check_type]
    out, code = run_cmd(cmd, timeout=120)
    return f"=== Проверка: {check_type} ===\n{out}"

def self_improve(new_code):
    backup = f"/opt/ai-agent/agent.py.bak.{int(time.time())}"
    shutil.copy2("/opt/ai-agent/agent.py", backup)
    with open("/opt/ai-agent/agent.py", 'w', encoding='utf-8') as f:
        f.write(new_code)
    return f"Код обновлён. Бэкап: {backup}. Выполни: systemctl restart ai-agent"

def handle_action(act):
    global STOP_FLAG
    if STOP_FLAG: return "Остановлено пользователем"
    a = act.get("action", "")
    try:
        if a == "exec":
            out, code = run_cmd(act.get("command", ""))
            return f"{'OK' if code==0 else 'ERR'}: {act['command']}\n{out}"
        elif a == "write":
            p = act.get("path", "")
            os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
            with open(p, 'w', encoding='utf-8') as f: f.write(act.get("content", ""))
            return f"Записан: {p} ({len(act.get('content',''))} байт)"
        elif a == "read":
            p = act.get("path", "")
            try:
                with open(p, 'r', encoding='utf-8') as f: return f.read()
            except Exception as e: return f"Ошибка чтения: {e}"
        elif a == "ssh":
            # Берём хост из настроек если агент написал бред
            host = act.get("host", "")
            user = act.get("user", "root")
            cmd = act.get("command", "")
            port = act.get("port", 22)
            
            if not host or len(host) < 7 or "." not in host:
                if ssh_passwords: 
                    host = list(ssh_passwords.keys())[-1]
                else: 
                    return "⚠️ Укажи реальный IP в SSH настройках слева и нажми 'Сохранить SSH'"
            
            # ВСЕГДА берём пароль из настроек UI, игнорируя то что написал агент
            pwd = ssh_passwords.get(host, "")
            if not pwd: 
                return f"⚠️ Пароль для {host} не сохранён. Введи его в поле 'Пароль' слева и нажми 'Сохранить SSH'."
            
            out, code = ssh_cmd(host, cmd, user, pwd, port)
            return f"SSH {host}: {cmd}\n{out}"
        elif a == "install":
            out, code = run_cmd(f"apt update && apt install -y {act.get('package','')}", 180)
            return f"Установка {act.get('package')}:\n{out}"
        elif a == "git":
            os.chdir(WORKSPACE)
            c = act.get("command", "status")
            if c == "commit":
                run_cmd("git add -A")
                out, _ = run_cmd(f'git commit -m "{act.get("message","ai")}"')
                return out
            out, _ = run_cmd(f"git {c}")
            return out
        elif a == "disk_check":
            return disk_check(act.get("type", "info"))
        elif a == "self_improve":
            return self_improve(act.get("code", ""))
        elif a == "reply":
            return act.get("text", "")
        elif a == "done":
            return f"ГОТОВО: {act.get('result','')}"
        else:
            return f"Неизвестное действие: {a}"
    except Exception as e:
        return f"Ошибка: {e}"

def extract_json(text):
    text = re.sub(r'^```json\s*|\s*```$', '', text).strip()
    start, end = text.find('{'), text.rfind('}') + 1
    if start != -1 and end > start:
        try: return json.loads(text[start:end])
        except: pass
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            try: return json.loads(line)
            except: pass
    return None

HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Agent v4.2</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,sans-serif;background:#0d1117;color:#c9d1d9;height:100vh;display:flex;flex-direction:column}
.header{background:#161b22;border-bottom:1px solid #30363d;padding:12px 20px;display:flex;justify-content:space-between;align-items:center}
.header h1{color:#58a6ff;font-size:18px}.stats{display:flex;gap:15px;font-size:12px}
.stat{background:#21262d;padding:4px 10px;border-radius:5px;border:1px solid #30363d}.stat-label{color:#8b949e}.stat-value{color:#58a6ff;font-weight:bold}.speed{color:#3fb950}
.container{flex:1;display:flex;overflow:hidden}.sidebar{width:280px;background:#161b22;border-right:1px solid #30363d;padding:12px;overflow-y:auto}
.sidebar h3{color:#8b949e;font-size:11px;text-transform:uppercase;margin:12px 0 8px;letter-spacing:1px}.btn{width:100%;padding:8px;margin-bottom:6px;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:5px;cursor:pointer;font-size:12px;text-align:left}
.btn:hover{background:#30363d;border-color:#58a6ff}.btn.primary{background:#238636;border-color:#2ea043;color:#fff;font-weight:bold}.btn.danger{background:#da3633;border-color:#f85149}.btn.warn{background:#f0883e;border-color:#d29922;color:#fff}.btn.info{background:#1f6feb;border-color:#388bfd;color:#fff}
.input-group{margin-bottom:8px}.input-group label{display:block;font-size:10px;color:#8b949e;margin-bottom:3px}
.input-group input,.input-group select{width:100%;padding:6px;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;font-size:11px}
.chat-area{flex:1;display:flex;flex-direction:column;overflow:hidden}.messages{flex:1;overflow-y:auto;padding:15px}
.message{margin-bottom:15px;max-width:85%;animation:fadeIn .3s}@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.message.user{margin-left:auto}.message-header{font-size:11px;color:#8b949e;margin-bottom:4px}
.message-content{padding:10px 14px;border-radius:10px;line-height:1.5;white-space:pre-wrap;word-wrap:break-word;font-size:13px}
.message.user .message-content{background:#1f6feb;color:#fff;border-bottom-right-radius:3px}
.message.agent .message-content{background:#21262d;border:1px solid #30363d;border-bottom-left-radius:3px}
.message.agent.thinking .message-content{background:#1c2128;border-style:dashed;color:#8b949e;font-style:italic}
.message.agent.action .message-content{background:#1a1f2e;border:1px solid #1f6feb;color:#79c0ff;font-family:monospace;font-size:12px}
.input-area{background:#161b22;border-top:1px solid #30363d;padding:12px 15px;display:flex;gap:8px}
.input-area textarea{flex:1;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;padding:10px;border-radius:6px;font-size:13px;font-family:inherit;resize:none;min-height:40px}
.input-area textarea:focus{outline:none;border-color:#58a6ff}.input-area button{padding:10px 18px;background:#238636;border:none;color:#fff;border-radius:6px;cursor:pointer;font-size:13px;font-weight:bold}
.input-area button:disabled{background:#21262d;cursor:not-allowed}
.streaming-indicator{display:inline-block;width:6px;height:6px;background:#3fb950;border-radius:50%;margin-left:6px;animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}.action-badge{display:inline-block;padding:2px 6px;background:#1f6feb;color:#fff;border-radius:3px;font-size:10px;margin-left:6px}
.result-block{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:8px;margin-top:8px;font-family:monospace;font-size:11px;white-space:pre-wrap;max-height:300px;overflow-y:auto;color:#7ee787}
.disk-panel{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:8px;margin-top:8px;max-height:400px;overflow-y:auto;font-family:monospace;font-size:11px;white-space:pre-wrap;color:#7ee787}
.disk-panel.running{color:#f0883e;border-color:#f0883e}.disk-panel.error{color:#f85149;border-color:#f85149}
.model-select-box{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;margin-bottom:12px}
.model-select-box select{width:100%;padding:8px;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;font-size:12px;margin-bottom:8px}
.model-list{max-height:300px;overflow-y:auto}
.model-item{display:flex;justify-content:space-between;align-items:center;padding:8px;background:#0d1117;border:1px solid #30363d;border-radius:4px;margin-bottom:6px}
.model-item .info{flex:1;overflow:hidden}.model-item .name{color:#58a6ff;font-weight:bold;font-size:12px;margin-bottom:2px}.model-item .size{color:#8b949e;font-size:10px}
.model-item button{padding:4px 8px;font-size:10px;border:none;border-radius:3px;cursor:pointer;margin-left:4px}
.model-item .use-btn{background:#238636;color:#fff}.model-item .rm-btn{background:#da3633;color:#fff}
.download-section{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:10px;margin-bottom:12px}
.popular-models{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px}
.popular-models .btn{padding:6px;font-size:10px;margin-bottom:0}
::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:#0d1117}::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
</style></head><body>
<div class="header"><h1>AI Agent (<span id="current-model">{{ model }}</span>)</h1>
<div class="stats"><div class="stat"><span class="stat-label">Скорость: </span><span class="stat-value speed" id="speed">0</span> <span class="stat-label">ток/с</span></div>
<div class="stat"><span class="stat-label">Статус: </span><span class="stat-value" id="status">Готов</span></div></div></div>
<div class="container"><div class="sidebar">
<h3>📦 Управление моделями</h3>
<div class="model-select-box">
<label style="font-size:10px;color:#8b949e;display:block;margin-bottom:4px">Выбрать модель:</label>
<select id="model-select" onchange="switchModel(this.value)"></select>
</div>
<div class="download-section">
<label style="font-size:10px;color:#8b949e;display:block;margin-bottom:4px">Скачать новую:</label>
<input type="text" id="new-model-name" placeholder="qwen2.5-coder:14b" style="width:100%;padding:6px;background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;font-size:11px;margin-bottom:6px">
<button class="btn info" onclick="downloadModel()">⬇️ Скачать</button>
<div style="margin-top:8px;font-size:10px;color:#8b949e">Популярные:</div>
<div class="popular-models">
<button class="btn" onclick="downloadModelByName('qwen2.5-coder:7b')">Qwen 7B</button>
<button class="btn" onclick="downloadModelByName('qwen2.5-coder:14b')">Qwen 14B</button>
<button class="btn" onclick="downloadModelByName('qwen2.5-coder:32b')">Qwen 32B</button>
<button class="btn" onclick="downloadModelByName('qwen2.5-coder:32b-q3_K_M')">Qwen 32B Q3</button>
<button class="btn" onclick="downloadModelByName('llama3.1:8b')">Llama 3.1 8B</button>
<button class="btn" onclick="downloadModelByName('mistral:7b')">Mistral 7B</button>
</div>
</div>
<div id="download-status"></div>
<div id="models-list" class="model-list"></div>
<h3>🔐 SSH</h3>
<div class="input-group"><label>Хост IP</label><input type="text" id="ssh-host" value="{{ ssh_host }}"></div>
<div class="input-group"><label>Пользователь</label><input type="text" id="ssh-user" value="{{ ssh_user }}"></div>
<div class="input-group"><label>Пароль</label><input type="password" id="ssh-password"></div>
<button class="btn" onclick="saveSSH()">💾 Сохранить SSH</button>
<h3>⚡ Действия</h3>
<button class="btn primary" onclick="newConv()">🆕 Новый диалог</button>
<button class="btn danger" id="stop-btn" onclick="stopTask()" style="display:none">🛑 ОСТАНОВИТЬ</button>
<button class="btn" onclick="quick('ls -la /opt/ai-agent/workspace')">📁 Файлы workspace</button>
<button class="btn" onclick="quick('git status')">📊 Git статус</button>
<button class="btn" onclick="quick('df -h && free -h')">💻 Система</button>
<h3>🔍 Проверка диска</h3>
<button class="btn warn" onclick="runDiskCheck('info')">📊 Информация</button>
<button class="btn warn" onclick="runDiskCheck('smart')">❤️ SMART здоровье</button>
<button class="btn warn" onclick="runDiskCheck('errors')">⚠️ Ошибки ядра</button>
<button class="btn warn" onclick="runDiskCheck('memory')">💾 Память</button>
<div id="disk-result" class="disk-panel" style="display:none"></div>
<h3>📂 История сессий</h3>
<button class="btn" onclick="loadLastSession()">📂 Последняя сессия</button>
<div id="sessions-list" style="font-size:11px;color:#8b949e;margin-top:6px"></div>
</div>
<div class="chat-area"><div class="messages" id="messages"></div>
<div class="input-area"><textarea id="input" placeholder="Задача... (Enter: отправить)" onkeydown="handleKey(event)"></textarea>
<button id="send-btn" onclick="sendMessage()">Отправить</button></div></div></div>
<script>
let session=localStorage.getItem('last_session')||'{{ session_id }}';
let isStreaming=false;
let ssh={host:'{{ ssh_host }}',user:'{{ ssh_user }}',password:''};

function handleKey(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}}

async function sendMessage(){
    const input=document.getElementById('input'),btn=document.getElementById('send-btn'),stopBtn=document.getElementById('stop-btn'),text=input.value.trim();
    if(!text||isStreaming)return;
    isStreaming=true;btn.disabled=true;stopBtn.style.display='block';input.value='';
    localStorage.setItem('last_session',session);
    addMsg(text,'user');
    const agentMsg=addMsg('Думает...','agent thinking'),contentDiv=agentMsg.querySelector('.message-content');
    document.getElementById('status').innerHTML='<span class="streaming-indicator"></span> Думает';
    let full='',start=Date.now(),tokens=0,lastAction=null;
    try{
        const res=await fetch('/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,session_id:session,ssh})});
        const reader=res.body.getReader(),dec=new TextDecoder();
        while(true){const{done,value}=await reader.read();if(done)break;
            for(const line of dec.decode(value).split('\\n')){
                if(line.startsWith('data: ')){const d=JSON.parse(line.slice(6));
                    if(d.type==='token'){full+=d.content;contentDiv.textContent=full;tokens++;
                        document.getElementById('speed').textContent=(tokens/((Date.now()-start)/1000)).toFixed(1);
                        document.getElementById('messages').scrollTop=999999;
                    }else if(d.type==='action'){
                        lastAction=d.action;agentMsg.className='message agent action';
                        contentDiv.innerHTML='<b>Действие:</b> '+d.action+'<br><code>'+full+'</code>';
                    }else if(d.type==='result'){
                        contentDiv.innerHTML='<b>Действие:</b> '+lastAction+'<br><code>'+full+'</code><div class="result-block">'+d.result+'</div>';
                    }else if(d.type==='done'){document.getElementById('status').textContent='Готов';stopBtn.style.display='none';loadSessions();}
                    else if(d.type==='error'){contentDiv.textContent='Ошибка: '+d.error;document.getElementById('status').textContent='Ошибка';stopBtn.style.display='none';}
                    else if(d.type==='stopped'){contentDiv.textContent='Остановлено.';document.getElementById('status').textContent='Остановлено';stopBtn.style.display='none';}
                }
            }
        }
    }catch(e){contentDiv.textContent='Ошибка: '+e;document.getElementById('status').textContent='Ошибка';stopBtn.style.display='none';}
    isStreaming=false;btn.disabled=false;
    if(document.getElementById('status').textContent==='Думает')document.getElementById('status').textContent='Готов';
}

function addMsg(text,type){
    const m=document.getElementById('messages'),d=document.createElement('div');d.className='message '+type;
    d.innerHTML='<div class="message-header">'+(type==='user'?'Ты':'Агент')+'</div><div class="message-content">'+text+'</div>';
    m.appendChild(d);m.scrollTop=999999;return d;
}

async function newConv(){
    if(!confirm('Новый диалог?'))return;
    const r=await fetch('/new_conversation',{method:'POST'});const d=await r.json();
    session=d.session_id;localStorage.setItem('last_session',session);
    document.getElementById('messages').innerHTML='';addMsg('Новый диалог.','agent');
}

async function quick(t){document.getElementById('input').value=t;sendMessage();}

function saveSSH(){
    ssh={host:document.getElementById('ssh-host').value,user:document.getElementById('ssh-user').value,password:document.getElementById('ssh-password').value};
    alert('SSH сохранён: '+ssh.host);
}

async function stopTask(){
    if(!confirm('Остановить?'))return;
    try{await fetch('/stop',{method:'POST'});}catch(e){}
    isStreaming=false;document.getElementById('send-btn').disabled=false;
    document.getElementById('stop-btn').style.display='none';
    document.getElementById('status').textContent='Остановлено';addMsg('Остановлено.','agent');
}

async function loadModels(){
    try{
        const r=await fetch('/models');const d=await r.json();
        const sel=document.getElementById('model-select');
        sel.innerHTML=d.models.map(m=>'<option value="'+m+'"'+(m===d.current?' selected':'')+'>'+m+'</option>').join('');
        document.getElementById('current-model').textContent=d.current;
        
        const popular=['qwen2.5-coder:7b','qwen2.5-coder:14b','qwen2.5-coder:32b','qwen2.5-coder:32b-q3_K_M','llama3.1:8b','mistral:7b'];
        document.getElementById('models-list').innerHTML=d.models.map(m=>{
            const isPopular=popular.includes(m);
            return '<div class="model-item"><div class="info"><div class="name">'+m+'</div>'+
                (isPopular?'<div class="size">Популярная</div>':'')+'</div>'+
                (m!==d.current?'<button class="use-btn" onclick="switchModel(\\''+m+'\\')">Использовать</button>':'<span style="color:#3fb950;font-size:10px">✓ Активна</span>')+
                (m!==d.current?'<button class="rm-btn" onclick="deleteModel(\\''+m+'\\')">🗑</button>':'')+'</div>';
        }).join('')||'<div style="color:#8b949e;font-size:11px;text-align:center;padding:20px">Нет установленных моделей</div>';
    }catch(e){}
}

async function switchModel(m){
    if(!confirm('Переключить на '+m+'?'))return;
    await stopTask();
    try{
        const r=await fetch('/switch_model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:m})});
        const d=await r.json();document.getElementById('current-model').textContent=d.model;alert(d.message);
        loadModels();
    }catch(e){alert('Ошибка: '+e);}
}

function downloadModelByName(name){
    document.getElementById('new-model-name').value=name;
    downloadModel();
}

async function downloadModel(){
    const name=document.getElementById('new-model-name').value.trim();
    if(!name){alert('Введи имя модели');return;}
    if(!confirm('Скачать модель '+name+'? Это займёт 5-30 минут.'))return;
    const status=document.getElementById('download-status');
    status.innerHTML='<div style="background:#0d1117;border:1px solid #1f6feb;border-radius:4px;padding:8px;margin-top:6px;color:#58a6ff;font-size:11px">⏳ Скачивание '+name+'...<br><span style="color:#8b949e">Не закрывай страницу</span></div>';
    try{
        const r=await fetch('/download_model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:name})});
        const d=await r.json();
        if(d.status==='ok'){
            status.innerHTML='<div style="background:#0d1117;border:1px solid #3fb950;border-radius:4px;padding:8px;margin-top:6px;color:#3fb950;font-size:11px">✅ '+d.message+'</div>';
            loadModels();
        }else{
            status.innerHTML='<div style="background:#0d1117;border:1px solid #f85149;border-radius:4px;padding:8px;margin-top:6px;color:#f85149;font-size:11px">❌ '+d.message+'</div>';
        }
    }catch(e){status.innerHTML='<div style="background:#0d1117;border:1px solid #f85149;border-radius:4px;padding:8px;margin-top:6px;color:#f85149;font-size:11px">Ошибка: '+e+'</div>';}
}

async function deleteModel(name){
    if(!confirm('Удалить модель '+name+'?'))return;
    try{
        const r=await fetch('/delete_model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:name})});
        const d=await r.json();alert(d.message);loadModels();
    }catch(e){alert('Ошибка: '+e);}
}

async function runDiskCheck(type){
    const panel=document.getElementById('disk-result');
    panel.style.display='block';panel.className='disk-panel running';panel.textContent='⏳ Запуск: '+type+'...';
    try{const r=await fetch('/disk_check?type='+type);const d=await r.json();
        panel.className='disk-panel'+(d.error?' error':'');
        panel.textContent=d.result||d.error||'Нет данных';
    }catch(e){panel.className='disk-panel error';panel.textContent='Ошибка: '+e;}}

async function loadSessions(){
    const r=await fetch('/sessions');const d=await r.json();
    document.getElementById('sessions-list').innerHTML=d.sessions.map(s=>
        '<button class="btn" onclick="loadSession(\\''+s+'\\')" style="font-size:10px;padding:4px;margin-bottom:2px">💬 '+s+'</button>'
    ).join('');
}

async function loadSession(id){
    session=id;localStorage.setItem('last_session',session);
    document.getElementById('messages').innerHTML='';
    const r=await fetch('/load_session?session_id='+id);const d=await r.json();
    d.history.forEach(m=>addMsg(m.content,m.role==='user'?'user':'agent'));
    if(d.last_task){
        addMsg('📂 Последнее действие: '+d.last_task.action+' ['+d.last_task.status+']','agent');
    }
}

async function loadLastSession(){
    const r=await fetch('/last_session');const d=await r.json();
    if(d.session_id){loadSession(d.session_id);}
    else{alert('Нет сохранённых сессий');}
}

async function autoLoadLast(){
    const saved=localStorage.getItem('last_session');
    if(saved){
        try{
            const r=await fetch('/load_session?session_id='+saved);const d=await r.json();
            if(d.history&&d.history.length>0){
                session=saved;
                d.history.forEach(m=>addMsg(m.content,m.role==='user'?'user':'agent'));
                if(d.last_task){
                    addMsg('📂 Восстановлена сессия. Последнее: '+d.last_task.action,'agent');
                }
            }
        }catch(e){}
    }
}

loadModels();loadSessions();autoLoadLast();
</script></body></html>"""

@app.route('/')
def home():
    sid = request.args.get('session', str(int(time.time())))
    cfg = load_config()
    return render_template_string(HTML, model=MODEL, session_id=sid, 
                                  ssh_host=cfg.get("ssh_host", "192.168.0.33"),
                                  ssh_user=cfg.get("ssh_user", "root"))

@app.route('/stop', methods=['POST'])
def stop():
    global STOP_FLAG
    STOP_FLAG = True
    return jsonify({"status": "stopped"})

@app.route('/models')
def get_models():
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
    except: models = AVAILABLE_MODELS
    return jsonify({"models": models, "current": MODEL})

@app.route('/switch_model', methods=['POST'])
def set_model():
    global MODEL
    data = request.json
    new_model = data.get('model', MODEL)
    old_model = MODEL
    try:
        req = urllib.request.Request("http://localhost:11434/api/generate",
            data=json.dumps({"model": old_model, "keep_alive": 0}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except: pass
    MODEL = new_model
    cfg = load_config()
    cfg["model"] = new_model
    save_config(cfg)
    return jsonify({"status": "ok", "message": f"Переключено: {old_model} → {new_model}", "model": MODEL})

@app.route('/download_model', methods=['POST'])
def download_model():
    data = request.json
    model_name = data.get('model', '')
    if not model_name:
        return jsonify({"status": "error", "message": "Не указано имя модели"})
    try:
        proc = subprocess.Popen(f"ollama pull {model_name}", shell=True, 
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.wait(timeout=600)
        if proc.returncode == 0:
            return jsonify({"status": "ok", "message": f"Модель {model_name} скачана"})
        else:
            err = proc.stderr.read().decode()
            return jsonify({"status": "error", "message": f"Ошибка: {err}"})
    except subprocess.TimeoutExpired:
        proc.kill()
        return jsonify({"status": "error", "message": "Таймаут скачивания"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/delete_model', methods=['POST'])
def delete_model():
    data = request.json
    model_name = data.get('model', '')
    if not model_name:
        return jsonify({"status": "error", "message": "Не указано имя"})
    try:
        proc = subprocess.run(f"ollama rm {model_name}", shell=True, 
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            return jsonify({"status": "ok", "message": f"Модель {model_name} удалена"})
        else:
            return jsonify({"status": "error", "message": proc.stderr})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@app.route('/disk_check')
def disk_check_endpoint():
    check_type = request.args.get('type', 'info')
    try:
        result = disk_check(check_type)
        return jsonify({"result": result, "type": check_type})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/chat/stream', methods=['POST'])
def chat_stream():
    global STOP_FLAG
    STOP_FLAG = False
    data = request.json
    msg = data.get('message', '')
    sid = data.get('session_id', str(int(time.time())))
    ssh_data = data.get('ssh', {})
    if ssh_data.get('password') and ssh_data.get('host'):
        ssh_passwords[ssh_data['host']] = ssh_data['password']
        cfg = load_config()
        cfg["ssh_host"] = ssh_data['host']
        cfg["ssh_user"] = ssh_data.get('user', 'root')
        save_config(cfg)
    save_msg(sid, 'user', msg)
    save_task(sid, 'started', 'chat')
    def generate():
        global STOP_FLAG
        try:
            stream = client.chat.completions.create(model=MODEL, messages=get_history(sid), temperature=0.2, stream=True, max_tokens=2048)
            full = ""
            for chunk in stream:
                if STOP_FLAG:
                    save_task(sid, 'stopped', 'chat')
                    save_msg(sid, 'assistant', full + " [ОСТАНОВЛЕНО]")
                    yield f"data: {json.dumps({'type': 'stopped'})}\n\n"
                    return
                if chunk.choices and chunk.choices[0].delta.content:
                    c = chunk.choices[0].delta.content
                    full += c
                    yield f"data: {json.dumps({'type': 'token', 'content': c})}\n\n"
            if STOP_FLAG:
                yield f"data: {json.dumps({'type': 'stopped'})}\n\n"
                return
            act = extract_json(full)
            if act and act.get("action") and act["action"] not in ["reply", "done"]:
                save_task(sid, 'running', act.get('action'))
                yield f"data: {json.dumps({'type': 'action', 'action': act.get('action')})}\n\n"
                res = handle_action(act)
                save_task(sid, 'done', act.get('action'), res[:200])
                yield f"data: {json.dumps({'type': 'result', 'result': res})}\n\n"
                save_msg(sid, 'assistant', full)
                save_msg(sid, 'user', f"Результат {act.get('action')}:\n{res}\nПродолжай.")
                for _ in range(4):
                    if STOP_FLAG: break
                    try:
                        stream2 = client.chat.completions.create(model=MODEL, messages=get_history(sid), temperature=0.2, stream=True, max_tokens=2048)
                        full2 = ""
                        for chunk in stream2:
                            if STOP_FLAG: break
                            if chunk.choices and chunk.choices[0].delta.content:
                                c = chunk.choices[0].delta.content
                                full2 += c
                                yield f"data: {json.dumps({'type': 'token', 'content': c})}\n\n"
                        if STOP_FLAG: break
                        act2 = extract_json(full2)
                        if act2 and act2.get("action") and act2["action"] not in ["reply", "done"]:
                            save_task(sid, 'running', act2.get('action'))
                            yield f"data: {json.dumps({'type': 'action', 'action': act2.get('action')})}\n\n"
                            res2 = handle_action(act2)
                            save_task(sid, 'done', act2.get('action'), res2[:200])
                            yield f"data: {json.dumps({'type': 'result', 'result': res2})}\n\n"
                            save_msg(sid, 'assistant', full2)
                            save_msg(sid, 'user', f"Результат:\n{res2}")
                        else:
                            save_msg(sid, 'assistant', full2)
                            save_task(sid, 'completed', 'done')
                            break
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
                        break
            else:
                save_msg(sid, 'assistant', full)
                save_task(sid, 'completed', 'reply')
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            save_task(sid, 'error', 'exception', str(e))
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/new_conversation', methods=['POST'])
def new_conv():
    return jsonify({"session_id": str(int(time.time()))})

@app.route('/sessions')
def sessions():
    conn = init_db()
    curs = conn.execute("SELECT DISTINCT session_id FROM messages ORDER BY id DESC LIMIT 20")
    return jsonify({"sessions": [r[0] for r in curs.fetchall()]})

@app.route('/last_session')
def last_session():
    conn = init_db()
    curs = conn.execute("SELECT session_id FROM messages ORDER BY id DESC LIMIT 1")
    row = curs.fetchone()
    if row:
        return jsonify({"session_id": row[0]})
    return jsonify({"session_id": None})

@app.route('/load_session')
def load_session():
    sid = request.args.get('session_id')
    conn = init_db()
    curs = conn.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id ASC", (sid,))
    history = [{"role": r[0], "content": r[1]} for r in curs.fetchall()]
    last_task = get_last_task(sid)
    result = {"history": history}
    if last_task:
        result["last_task"] = {"status": last_task[0], "action": last_task[1], "progress": last_task[2]}
    return jsonify(result)

if __name__ == '__main__':
    init_db()
    os.makedirs(WORKSPACE, exist_ok=True)
    print(f"Agent running: http://0.0.0.0:8000 | Model: {MODEL}")
    app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
