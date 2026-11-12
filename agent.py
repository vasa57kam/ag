HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Agent v4.1</title>
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
.model-item .use-btn{background:#238636;color:#fff}.model-item .dl-btn{background:#1f6feb;color:#fff}.model-item .rm-btn{background:#da3633;color:#fff}
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
<button class="btn" onclick="downloadModelByName('qwen2.5-coder:32b-q3_K_M')">Qwen 32B (Q3)</button>
<button class="btn" onclick="downloadModelByName('llama3.1:8b')">Llama 3.1 8B</button>
<button class="btn" onclick="downloadModelByName('mistral:7b')">Mistral 7B</button>
</div>
</div>
<div id="download-status"></div>
<div id="models-list" class="model-list"></div>
<h3>SSH</h3>
<div class="input-group"><label>Хост IP</label><input type="text" id="ssh-host" value="{{ ssh_host }}"></div>
<div class="input-group"><label>Пользователь</label><input type="text" id="ssh-user" value="{{ ssh_user }}"></div>
<div class="input-group"><label>Пароль</label><input type="password" id="ssh-password"></div>
<button class="btn" onclick="saveSSH()"> Сохранить SSH</button>
<h3>Действия</h3>
<button class="btn primary" onclick="newConv()"> Новый диалог</button>
<button class="btn danger" id="stop-btn" onclick="stopTask()" style="display:none">🛑 ОСТАНОВИТЬ</button>
<button class="btn" onclick="quick('ls -la /opt/ai-agent/workspace')">📁 Файлы workspace</button>
<button class="btn" onclick="quick('git status')">📊 Git статус</button>
<button class="btn" onclick="quick('df -h && free -h')">💻 Система</button>
<h3>Проверка диска</h3>
<button class="btn warn" onclick="runDiskCheck('info')">📊 Информация</button>
<button class="btn warn" onclick="runDiskCheck('smart')">❤️ SMART здоровье</button>
<button class="btn warn" onclick="runDiskCheck('errors')">⚠️ Ошибки ядра</button>
<button class="btn warn" onclick="runDiskCheck('memory')">💾 Память</button>
<div id="disk-result" class="disk-panel" style="display:none"></div>
<h3>История сессий</h3>
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
        
        // Список установленных моделей
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
    status.innerHTML='<div style="background:#0d1117;border:1px solid #1f6feb;border-radius:4px;padding:8px;margin-top:6px;color:#58a6ff;font-size:11px"> Скачивание '+name+'...<br><span style="color:#8b949e">Не закрывай страницу</span></div>';
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
        '<button class="btn" onclick="loadSession(\\''+s+'\\')" style="font-size:10px;padding:4px;margin-bottom:2px"> '+s+'</button>'
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
