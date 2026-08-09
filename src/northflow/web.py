"""Локальный веб-интерфейс NorthFlow.

Один стандартный HTTP-сервер на Python (http.server), без внешних зависимостей.
API:
  GET  /                  — приложение (SPA)
  GET  /api/state         — состояние проекта
  GET  /api/memory/log    — журнал обращений к памяти
  GET  /api/memory/stats  — статистика памяти
  POST /api/run           — следующий шаг конвейера
  POST /api/answers       — ответы на вопросы
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .memory import MemoryDB
from .runner import run_phase_step, submit_answers
from .state import ProjectState, write_roadmap

PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NorthFlow</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background: #0b0e14; color: #e8eaf0; }
  .topbar { display: flex; align-items: center; gap: 14px; padding: 14px 22px; background: #10141c; border-bottom: 1px solid #1e2430; position: sticky; top: 0; z-index: 5; }
  .logo { font-size: 20px; font-weight: 700; letter-spacing: 0; }
  .logo span { color: #6ea8ff; }
  .phase-pill { background: #16202f; border: 1px solid #253347; color: #9fc4ff; border-radius: 999px; padding: 4px 12px; font-size: 12px; }
  .actions { margin-left: auto; display: flex; gap: 8px; }
  button { background: #18202c; color: #e8eaf0; border: 1px solid #2a3446; border-radius: 7px; padding: 8px 14px; font-size: 13px; cursor: pointer; }
  button:hover { background: #1f2938; }
  button.primary { background: #2456d6; border-color: #2f66ea; color: #fff; }
  button.primary:disabled { opacity: .55; cursor: wait; }
  .layout { display: grid; grid-template-columns: 320px 1fr; gap: 0; min-height: calc(100vh - 60px); }
  .side { border-right: 1px solid #1e2430; padding: 18px; background: #0d1119; }
  .main { padding: 18px 24px; overflow: auto; }
  .card { background: #121722; border: 1px solid #1e2634; border-radius: 10px; padding: 16px; margin-bottom: 14px; }
  .card h3 { margin: 0 0 10px; font-size: 14px; text-transform: uppercase; letter-spacing: .04em; color: #93a0b4; }
  .muted { color: #6d7686; font-size: 13px; }
  .task { display: flex; gap: 10px; align-items: center; padding: 8px 10px; border-radius: 7px; border: 1px solid transparent; }
  .task:hover { background: #171d29; }
  .task .dot { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; }
  .dot.todo { background: #5b6472; } .dot.in_progress { background: #e0a83c; } .dot.done { background: #3ecf8e; } .dot.blocked { background: #e05252; }
  .progress { height: 8px; background: #1b2230; border-radius: 99px; overflow: hidden; }
  .progress > div { height: 100%; background: #3ecf8e; transition: width .3s; }
  .log-line { border-bottom: 1px solid #171d29; padding: 9px 4px; display: flex; gap: 10px; align-items: baseline; cursor: pointer; }
  .log-line:hover { background: #141a25; }
  .log-line .badge { font-size: 11px; padding: 1px 7px; border-radius: 99px; }
  .badge.store { background: #123524; color: #7ee2a8; } .badge.recall { background: #14304a; color: #8ec8ff; } .badge.relation { background: #3a2d14; color: #ffd28a; }
  .detail { display: none; padding: 10px 6px 14px; }
  .detail.open { display: block; }
  .detail pre { white-space: pre-wrap; background: #0a0d13; border: 1px solid #1b2230; padding: 10px; border-radius: 7px; font-size: 12.5px; max-height: 300px; overflow: auto; }
  .questions .q { background: #16202f; border: 1px solid #253347; border-radius: 9px; padding: 14px; margin-bottom: 10px; }
  .questions label { display: block; margin: 7px 0; padding: 9px 12px; border: 1px solid #2a3446; border-radius: 7px; cursor: pointer; font-size: 14px; }
  .questions label.selected { background: #1d2f4d; border-color: #3f6fd8; }
  .questions input[type=radio] { margin-right: 8px; }
  .toast { position: fixed; bottom: 18px; right: 18px; background: #1b2230; border: 1px solid #2c3748; padding: 12px 16px; border-radius: 9px; max-width: 420px; font-size: 13px; z-index: 10; display: none; white-space: pre-wrap; }
  .empty { color: #5d6675; padding: 20px 4px; font-size: 14px; }
  .loading { display: none; align-items: center; gap: 10px; color: #9fc4ff; font-size: 14px; }
  .loading.show { display: flex; }
  .spinner { width: 16px; height: 16px; border: 2px solid #2a3446; border-top-color: #6ea8ff; border-radius: 50%; animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (max-width: 860px) { .layout { grid-template-columns: 1fr; } .side { border-right: none; border-bottom: 1px solid #1e2430; } }
</style>
</head>
<body>
<div class="topbar">
  <div class="logo">North<span>Flow</span></div>
  <span class="phase-pill" id="phasePill">—</span>
  <div class="actions">
    <div class="loading" id="loading"><span class="spinner"></span><span>Агент работает…</span></div>
    <button id="btnRefresh">Обновить</button>
    <button id="btnRun" class="primary">Следующий шаг</button>
  </div>
</div>
<div class="layout">
  <aside class="side">
    <div class="card"><h3>Проект</h3><div id="projectInfo" class="muted">—</div></div>
    <div class="card"><h3>Этапы</h3><div id="stagesList"></div></div>
    <div class="card"><h3>Память</h3>
      <div id="memoryStats" class="muted">—</div>
      <div style="margin-top:10px"><button id="btnMemory">Журнал памяти</button></div>
    </div>
  </aside>
  <main class="main">
    <div class="card" id="questionsCard" style="display:none"><h3>Вопросы агента</h3><div id="questions" class="questions"></div><button id="btnAnswer" class="primary" style="margin-top:10px">Отправить ответы</button></div>
    <div class="card"><h3>Последние действия</h3><div id="logList"></div></div>
  </main>
</div>
<div class="toast" id="toast"></div>
<script>
let state = {phase:'', stages:[], answers:{}, pending_questions:[]};
let memoryLog = [];

async function api(path, opts={}) {
  const r = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts});
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
function esc(s){ return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function toast(msg, err=false){ const t=document.getElementById('toast'); t.textContent=msg; t.style.display='block'; t.style.background=err?'#3a1d1d':'#1b2230'; setTimeout(()=>t.style.display='none', 6000); }

async function loadState(){
  try {
    state = await api('/api/state');
    document.getElementById('phasePill').textContent = 'Фаза: ' + state.phase;
    document.getElementById('projectInfo').textContent = (state.name||'—') + '\nФаза: ' + state.phase + '\nОбновлено: ' + (state.updated_at||'—');
    renderStages();
    renderQuestions();
  } catch(e){ toast('Не удалось загрузить состояние: '+e, true); }
}
function renderStages(){
  const el = document.getElementById('stagesList');
  el.innerHTML = '';
  if (!state.stages || !state.stages.length) { el.innerHTML='<div class="empty">Этапов пока нет</div>'; return; }
  for (const s of state.stages){
    const done = (s.tasks||[]).filter(t=>t.status==='done').length;
    const total = (s.tasks||[]).length;
    const pct = total? Math.round(done/total*100):0;
    const div = document.createElement('div');
    div.className='task';
    div.innerHTML = `<span class="dot ${s.status}"></span><div style="flex:1"><div style="font-size:14px">${esc(s.title)}</div><div class="muted">${done}/${total} задач</div><div class="progress" style="margin-top:6px"><div style="width:${pct}%"></div></div></div>`;
    el.appendChild(div);
  }
}
function renderQuestions(){
  const card = document.getElementById('questionsCard');
  const qs = state.pending_questions || [];
  card.style.display = qs.length ? 'block' : 'none';
  if (!qs.length) return;
  const el = document.getElementById('questions');
  el.innerHTML='';
  qs.forEach((q,i)=>{
    const opts = q.options && q.options.length ? q.options : [{value:'да',label:'Да'},{value:'нет',label:'Нет'},{value:'не знаю',label:'Не знаю / пусть решит агент'}];
    const key = q.key || ('q'+(i+1));
    const box = document.createElement('div');
    box.className='q';
    box.innerHTML = `<div style="font-weight:600;margin-bottom:8px">${i+1}. ${esc(q.question||q.text||'Вопрос')}</div>` + opts.map((o,j)=>`<label data-key="${esc(key)}" data-val="${esc(o.value||o.label)}"><input type="radio" name="q_${esc(key)}" value="${j}"> ${esc(o.label||o.value)}</label>`).join('');
    box.addEventListener('click', (e)=>{
      const label = e.target.closest('label');
      if (label) { box.querySelectorAll('label').forEach(l=>l.classList.remove('selected')); label.classList.add('selected'); }
    });
    el.appendChild(box);
  });
}
async function sendAnswers(){
  const answers = {};
  document.querySelectorAll('#questions .q').forEach(q=>{
    const key = q.querySelector('label[data-key]')?.dataset.key;
    const selected = q.querySelector('input:checked');
    if (key && selected) {
      const label = q.querySelectorAll('label')[parseInt(selected.value)];
      answers[key] = label?.dataset.val || 'да';
    }
  });
  if (!Object.keys(answers).length) { toast('Выбери ответы'); return; }
  try {
    const res = await api('/api/answers', {method:'POST', body: JSON.stringify({answers})});
    toast(res.message || 'Ответы отправлены');
    await loadState();
  } catch(e){ toast('Ошибка: '+e, true); }
}
async function runStep(){
  const btn = document.getElementById('btnRun');
  btn.disabled = true;
  document.getElementById('loading').classList.add('show');
  try {
    const res = await api('/api/run', {method:'POST'});
    toast(res.message || 'Готово');
  } catch(e){ toast('Ошибка: '+e, true); }
  finally { btn.disabled=false; document.getElementById('loading').classList.remove('show'); await loadState(); await loadMemory(); }
}
async function loadMemory(){
  try {
    const stats = await api('/api/memory/stats');
    document.getElementById('memoryStats').textContent = 'Факты: ' + (stats.memories||0) + ' · Связи: ' + (stats.relations||0) + ' · Эпизоды: ' + (stats.episodes||0);
    memoryLog = await api('/api/memory/log');
    renderMemoryLog();
  } catch(e){ /* память может быть пустой */ }
}
function renderMemoryLog(){
  const el = document.getElementById('logList');
  el.innerHTML='';
  if (!memoryLog.length) { el.innerHTML='<div class="empty">Пока нет обращений к памяти.</div>'; return; }
  for (const r of memoryLog.slice(0, 30)){
    const d = document.createElement('div');
    d.className='log-line';
    d.innerHTML = `<span class="badge ${r.action}">${esc(r.action)}</span><span class="muted" style="min-width:90px">${esc(r.role||'system')}</span><span style="flex:1">${esc((r.query||'').slice(0,90))}</span><span class="muted">${esc(r.created_at||'')}</span><div class="detail"><div class="muted">Запрос:</div><pre>${esc(r.query||'')}</pre><div class="muted" style="margin-top:8px">Ответ:</div><pre>${esc(pretty(r.response_detail||''))}</pre></div>`;
    d.addEventListener('click', ()=> d.querySelector('.detail').classList.toggle('open'));
    el.appendChild(d);
  }
}
function pretty(s){ try{return JSON.stringify(JSON.parse(s),null,2);}catch(_){return s;} }

document.getElementById('btnRun').onclick = runStep;
document.getElementById('btnRefresh').onclick = async ()=>{ await loadState(); await loadMemory(); };
document.getElementById('btnAnswer').onclick = sendAnswers;
document.getElementById('btnMemory').onclick = async ()=>{
  const res = await api('/api/state'); // просто проверка
  document.getElementById('logList').scrollIntoView({behavior:'smooth'});
};
loadState(); loadMemory();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server: "WebServer"  # type: ignore

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index.html"):
            self._send_html(PAGE)
            return
        if self.path == "/api/state":
            state = ProjectState.load(self.server.root)
            self._send_json({
                "name": state.name,
                "phase": state.phase,
                "stages": [
                    {
                        "id": s.id,
                        "title": s.title,
                        "status": s.status,
                        "tasks": [
                            {"id": t.id, "title": t.title, "status": t.status}
                            for t in s.tasks
                        ],
                    }
                    for s in state.stages
                ],
                "answers": state.answers,
                "pending_questions": state.pending_questions,
                "pending_next_phase": state.pending_next_phase,
                "updated_at": state.updated_at,
            })
            return
        if self.path == "/api/memory/log":
            db = MemoryDB(self.server.root / "memory.db")
            try:
                rows = db.list_memory_log(limit=100)
                self._send_json({"entries": rows})
            finally:
                db.close()
            return
        if self.path == "/api/memory/stats":
            db = MemoryDB(self.server.root / "memory.db")
            try:
                self._send_json(db.stats())
            finally:
                db.close()
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/run":
            res = run_phase_step(self.server.root, config_path=self.server.config)
            self._send_json(res, status=200 if res.get("ok") else 422)
            return
        if self.path == "/api/answers":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8"))
                res = submit_answers(self.server.root, data.get("answers", {}), self.server.config)
                self._send_json(res, status=200 if res.get("ok") else 422)
            except Exception as e:
                self._send_json({"ok": False, "message": str(e)}, 422)
            return
        self._send_json({"error": "not found"}, 404)


class WebServer:
    def __init__(self, root: Path, config: str | None = None, host: str = "127.0.0.1", port: int = 8756):
        self.root = Path(root).resolve()
        self.config = config
        self.host = host
        self.port = port
        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.root = self.root
        self.httpd.config = config
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def run_web(root: Path, config: str | None = None, host: str = "127.0.0.1",
            port: int = 8756, open_browser: bool = True) -> WebServer:
    server = WebServer(root, config, host, port)
    server.start()
    if open_browser:
        webbrowser.open(server.url())
    return server
