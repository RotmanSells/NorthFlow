"""Лёгкий веб-дашборд памяти: один HTML-файл, без серверов и внешних зависимостей.

Генерирует self-contained HTML с данными memory_log, который открывается
в браузере. Никакого бэкенда: данные встроены как JSON.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .memory import MemoryDB

_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NorthFlow — память проекта</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: #0f1115; color: #e6e8ee; }
  header { padding: 18px 24px; border-bottom: 1px solid #262b36; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 18px; margin: 0; }
  .stats { margin-left: auto; display: flex; gap: 12px; color: #9aa3b2; font-size: 13px; }
  .filters { padding: 12px 24px; display: flex; gap: 10px; border-bottom: 1px solid #1d222c; flex-wrap: wrap; }
  .filters select, .filters button { background: #1a1f2b; color: #e6e8ee; border: 1px solid #2b3342; border-radius: 6px; padding: 7px 12px; font-size: 13px; }
  .filters button { cursor: pointer; }
  .log { padding: 12px 24px 40px; }
  .entry { border: 1px solid #262b36; border-radius: 8px; margin: 8px 0; background: #14181f; overflow: hidden; }
  .entry-head { display: flex; gap: 10px; align-items: center; padding: 10px 14px; cursor: pointer; }
  .entry-head:hover { background: #181d27; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; }
  .badge.store { background: #123524; color: #7ee2a8; }
  .badge.recall { background: #14304a; color: #8ec8ff; }
  .badge.relation { background: #3a2d14; color: #ffd28a; }
  .role { color: #9aa3b2; font-size: 12px; }
  .time { margin-left: auto; color: #5b6472; font-size: 12px; }
  .query { flex: 1; min-width: 200px; }
  .details { display: none; padding: 12px 14px; border-top: 1px solid #1d222c; }
  .entry.open .details { display: block; }
  .details pre { white-space: pre-wrap; font-size: 13px; background: #0d1015; padding: 10px; border-radius: 6px; }
  .detail-label { color: #7f8794; font-size: 11px; text-transform: uppercase; margin: 10px 0 4px; }
</style>
</head>
<body>
<header>
  <h1>🧠 NorthFlow — память проекта</h1>
  <div class="stats">
    <span id="stat-entries"></span>
    <span id="stat-role"></span>
  </div>
</header>
<div class="filters">
  <select id="filter-role"><option value="">Все роли</option></select>
  <select id="filter-action">
    <option value="">Все действия</option>
    <option value="store">store</option>
    <option value="recall">recall</option>
    <option value="relation">relation</option>
  </select>
  <button onclick="applyFilters()">Показать</button>
</div>
<div class="log" id="log"></div>
<script>
const DATA = __DATA__;
let entries = DATA.entries || [];

function render() {
  const roleFilter = document.getElementById('filter-role').value;
  const actionFilter = document.getElementById('filter-action').value;
  const list = document.getElementById('log');
  list.innerHTML = '';
  const visible = entries.filter(e => (!roleFilter || e.role === roleFilter) && (!actionFilter || e.action === actionFilter));
  document.getElementById('stat-entries').textContent = visible.length + ' записей';
  if (!visible.length) {
    list.innerHTML = '<div style="color:#7f8794;padding:20px">Пока нет операций с памятью.</div>';
    return;
  }
  for (const e of visible) {
    const el = document.createElement('div');
    el.className = 'entry';
    el.innerHTML = `
      <div class="entry-head" onclick="this.parentElement.classList.toggle('open')">
        <span class="badge ${e.action}">${e.action}</span>
        <span class="role">${e.role || 'system'}</span>
        <span class="query">${escapeHtml((e.query || '').slice(0, 120))}</span>
        <span class="time">${e.created_at || ''}</span>
      </div>
      <div class="details">
        <div class="detail-label">Запрос</div><pre>${escapeHtml(e.query || '')}</pre>
        <div class="detail-label">Параметры</div><pre>${escapeHtml(e.request_detail || '')}</pre>
        <div class="detail-label">Ответ</div><pre>${escapeHtml(pretty(e.response_detail || ''))}</pre>
        <div class="detail-label">Memory IDs</div><pre>${escapeHtml(e.memory_ids || '[]')}</pre>
      </div>`;
    list.appendChild(el);
  }
}

function applyFilters() { render(); }

function pretty(s) {
  try { return JSON.stringify(JSON.parse(s), null, 2); } catch (_) { return s; }
}
function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

(function init() {
  const roles = [...new Set(entries.map(e => e.role).filter(Boolean))].sort();
  const sel = document.getElementById('filter-role');
  for (const r of roles) {
    const opt = document.createElement('option');
    opt.value = r; opt.textContent = r;
    sel.appendChild(opt);
  }
  document.getElementById('stat-role').textContent = roles.length + ' ролей';
  render();
})();
</script>
</body>
</html>
"""


def render_dashboard(root: Path | str, output: Path | str | None = None, limit: int = 500) -> Path:
    """Собирает HTML-дашборд с последними записями memory_log."""
    root = Path(root)
    db = MemoryDB(root / "memory.db")
    try:
        rows = db.list_memory_log(limit=limit)
        data = {"generated": datetime.now().isoformat(), "entries": rows}
    finally:
        db.close()
    html = _TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    out = Path(output) if output else root / "memory-dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out
