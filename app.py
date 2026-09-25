#!/usr/bin/env python3
"""
My UniStation — Dashboard académico local para a Universidade.

Servidor HTTP leve que:
  • Serve a aplicação single-page (HTML/CSS/JS embutidos);
  • Persiste o estado do utilizador em `data.json` (caminho configurável);
  • Usa `config.json` como bootstrap para o caminho do data.json;
  • Expõe /api/data (GET/POST) e /api/config (GET/POST/preview);
  • Escrita atómica + backups automáticos antes de substituições.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Configuração base
# ---------------------------------------------------------------------------
PORT         = int(os.environ.get("MYUNISTATION_PORT", "8080"))
BIND_ADDRESS = os.environ.get("MYUNISTATION_HOST", "0.0.0.0")
CONFIG_FILE  = os.environ.get("MYUNISTATION_CONFIG", "config.json")
DEFAULT_DATA_FILE = os.environ.get("MYUNISTATION_DATA", "data.json")

SCHEMA_VERSION    = 1
BACKUP_RETENTION  = 5
APP_DIR           = os.path.dirname(os.path.abspath(__file__))

DEFAULT_CONFIG: dict[str, Any] = {
    "data_file": "data.json",
}

DEFAULT_STATE: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "profile": {
        "name": "Aluno",
        "degree": "Engenharia Informática",
    },
    "theme": "dark",
    "courses": [],
    "notificationsActive": True,
    "notifyAdvanceDays": 3,
    "mutedNotifications": [],
    "expandedWeeks": {},
    "completedDeliveries": {},
}


# ---------------------------------------------------------------------------
# Utilitários de ficheiro
# ---------------------------------------------------------------------------
def _resolve_path(p: str) -> str:
    """Expande ~ e resolve caminhos relativos contra a pasta da aplicação."""
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(APP_DIR, p)
    return os.path.abspath(p)


def _write_json_atomic(path: str, data: Any) -> None:
    """Escrita atómica: tmp no mesmo FS + fsync + os.replace."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".data_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cleanup_old_backups(path: str, keep: int = BACKUP_RETENTION) -> None:
    """Mantém apenas os `keep` backups mais recentes do ficheiro indicado."""
    parent = os.path.dirname(os.path.abspath(path)) or "."
    base = os.path.basename(path)
    try:
        entries = [
            e for e in os.listdir(parent)
            if e.startswith(base + ".backup-")
        ]
    except OSError:
        return
    entries.sort(reverse=True)
    for old in entries[keep:]:
        try:
            os.unlink(os.path.join(parent, old))
        except OSError:
            pass


def _backup_before_replace(path: str) -> str | None:
    """Renomeia path → path.backup-<timestamp>[-N]. Devolve o caminho do backup."""
    if not os.path.exists(path):
        return None

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{path}.backup-{ts}"
    backup = base
    n = 1
    while os.path.exists(backup):
        backup = f"{base}-{n}"
        n += 1

    try:
        os.rename(path, backup)
    except OSError:
        try:
            shutil.copy2(path, backup)
            os.unlink(path)
        except OSError:
            return None

    _cleanup_old_backups(path)
    return backup


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Validação e migração do estado
# ---------------------------------------------------------------------------
def _validate_state(data: Any) -> tuple[bool, str]:
    """Validação estrutural mínima do estado antes de gravar."""
    if not isinstance(data, dict):
        return False, "Estado não é um objeto JSON."
    if "profile" in data and not isinstance(data["profile"], dict):
        return False, "Campo 'profile' deve ser um objeto."
    if "courses" in data and not isinstance(data["courses"], list):
        return False, "Campo 'courses' deve ser uma lista."
    if "mutedNotifications" in data and not isinstance(data["mutedNotifications"], list):
        return False, "Campo 'mutedNotifications' deve ser uma lista."
    if "completedDeliveries" in data and not isinstance(data["completedDeliveries"], dict):
        return False, "Campo 'completedDeliveries' deve ser um objeto."
    if "expandedWeeks" in data and not isinstance(data["expandedWeeks"], dict):
        return False, "Campo 'expandedWeeks' deve ser um objeto."
    return True, ""


def _migrate_state(data: dict) -> dict:
    """Migração incremental do esquema do data.json (não muta o input)."""
    migrated = dict(data)
    v = migrated.get("schema_version", 0)
    if v < 1:
        migrated["schema_version"] = 1
    return migrated


# ---------------------------------------------------------------------------
# Config bootstrap
# ---------------------------------------------------------------------------
_config_cache: dict[str, Any] | None = None
_config_lock = threading.Lock()


def load_config() -> dict[str, Any]:
    """Carrega config.json (ou cria-o por defeito). Cacheado em memória."""
    global _config_cache
    with _config_lock:
        if _config_cache is not None:
            return _config_cache

        cfg: dict[str, Any] = {}
        if os.path.exists(CONFIG_FILE):
            try:
                raw = _read_json(CONFIG_FILE)
                if isinstance(raw, dict):
                    cfg = raw
            except (OSError, json.JSONDecodeError):
                cfg = {}

        if not cfg.get("data_file"):
            cfg["data_file"] = DEFAULT_DATA_FILE

        _config_cache = cfg

        if not os.path.exists(CONFIG_FILE):
            try:
                _write_json_atomic(CONFIG_FILE, cfg)
            except OSError:
                pass

        return cfg


def _save_config_file(cfg: dict[str, Any]) -> None:
    global _config_cache
    _write_json_atomic(CONFIG_FILE, cfg)
    with _config_lock:
        _config_cache = cfg


def get_data_file() -> str:
    """Devolve o caminho absoluto do data.json atualmente configurado."""
    cfg = load_config()
    return _resolve_path(cfg.get("data_file") or DEFAULT_DATA_FILE)


def _file_metadata(path: str) -> dict[str, Any]:
    """Inspeciona um ficheiro sem o alterar. Nunca lança exceções."""
    if not os.path.exists(path):
        return {"exists": False}

    try:
        st = os.stat(path)
    except OSError as exc:
        return {"exists": True, "valid_json": False, "error": str(exc)}

    try:
        data = _read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "exists": True,
            "valid_json": False,
            "error": str(exc),
            "size_bytes": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        }

    courses = data.get("courses", []) if isinstance(data, dict) else []
    active = sum(1 for c in courses if isinstance(c, dict) and not c.get("isCompleted"))
    completed = sum(1 for c in courses if isinstance(c, dict) and c.get("isCompleted"))

    last_ts: str | None = None
    last_course: str | None = None
    for c in courses:
        if isinstance(c, dict) and c.get("updatedAt"):
            ts = c["updatedAt"]
            if last_ts is None or ts > last_ts:
                last_ts = ts
                last_course = c.get("name")

    return {
        "exists": True,
        "valid_json": True,
        "size_bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        "courses_total": len(courses),
        "courses_active": active,
        "courses_completed": completed,
        "last_updated_course": last_course,
    }


# ---------------------------------------------------------------------------
# Aplicação single-page (HTML + CSS + JS embutidos)
# ---------------------------------------------------------------------------
APP_HTML = """<!DOCTYPE html>
<html lang="pt-PT" data-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>My UniStation</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' x2='1'%3E%3Cstop offset='0' stop-color='%230b3d3d'/%3E%3Cstop offset='1' stop-color='%2310b981'/%3E%3C/linearGradient%3E%3C/defs%3E%3Cg stroke='url(%23g)' stroke-width='8' fill='none' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M15 20 L15 55 L50 85 L85 55 L85 20'/%3E%3Cpath d='M50 20 L50 50'/%3E%3C/g%3E%3Cg fill='url(%23g)'%3E%3Ccircle cx='15' cy='20' r='7'/%3E%3Ccircle cx='50' cy='20' r='7'/%3E%3Ccircle cx='85' cy='20' r='7'/%3E%3Ccircle cx='50' cy='85' r='8'/%3E%3C/g%3E%3C/svg%3E">
  <style>
    :root {
      --font-main: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;

      --radius-sm: 6px;
      --radius-md: 10px;
      --radius-lg: 14px;
      --transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);

      --sidebar-width: 290px;

      --emerald-accent: #10b981;
      --emerald-glow: rgba(16, 185, 129, 0.15);
      --amber-accent: #f59e0b;
      --rose-accent: #f43f5e;
      --cyan-accent: #38bdf8;
    }

    html[data-theme="dark"] {
      --bg-base: #080b11;
      --bg-surface: #0f1523;
      --bg-card: #131d31;
      --bg-card-subtle: #18243b;
      --border-color: #24344d;
      --border-focus: #38bdf8;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --primary-subtle: rgba(56, 189, 248, 0.12);
      --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.3);
      --shadow-md: 0 4px 10px rgba(0, 0, 0, 0.4);
      --shadow-card-elevated: 0 8px 20px rgba(0, 0, 0, 0.55), 0 0 0 1px rgba(255, 255, 255, 0.05);
      --shadow-toast: 0 14px 32px rgba(0, 0, 0, 0.75), 0 0 0 1px rgba(255, 255, 255, 0.1);
    }

    html[data-theme="light"] {
      --bg-base: #f1f5f9;
      --bg-surface: #ffffff;
      --bg-card: #ffffff;
      --bg-card-subtle: #f8fafc;
      --border-color: #cbd5e1;
      --border-focus: #0284c7;
      --text-main: #0f172a;
      --text-muted: #475569;
      --text-dim: #64748b;
      --primary-subtle: rgba(2, 132, 199, 0.1);
      --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.08);
      --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.08);
      --shadow-card-elevated: 0 6px 16px rgba(0, 0, 0, 0.1), 0 0 0 1px rgba(0, 0, 0, 0.05);
      --shadow-toast: 0 14px 32px rgba(0, 0, 0, 0.18), 0 0 0 1px rgba(0, 0, 0, 0.05);
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      width: 100%;
      min-height: 100vh;
      overflow-x: hidden;
      background-color: var(--bg-base);
    }
    body {
      font-family: var(--font-main);
      color: var(--text-main);
      display: flex;
      line-height: 1.5;
      font-size: 14px;
      position: relative;
      transition: background-color 0.25s ease, color 0.25s ease;
    }

    .mobile-only { display: none !important; }
    .desktop-only { display: inline-flex !important; }

    aside.sidebar {
      width: var(--sidebar-width);
      background: var(--bg-surface);
      border-right: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      position: fixed;
      top: 0; bottom: 0; left: 0;
      z-index: 105;
    }

    /* ★ LOGO — linha fina de gradiente no topo da sidebar */
    aside.sidebar::before {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 2px;
      background: linear-gradient(90deg, #10b981 0%, #38bdf8 100%);
      opacity: 0.75;
      z-index: 1;
    }

    .sidebar-header {
      padding: 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-color);
      height: 64px;
      flex-shrink: 0;
    }
    .brand-content { display: flex; align-items: center; gap: 10px; overflow: hidden; white-space: nowrap; }

    /* ★ LOGO — substitui o antigo .brand-badge */
    .brand-logo {
      width: 38px;
      height: 32px;
      flex-shrink: 0;
      filter: drop-shadow(0 0 10px var(--emerald-glow));
      transition: filter 0.35s ease, transform 0.35s ease;
    }
    .brand-content:hover .brand-logo {
      filter: drop-shadow(0 0 16px rgba(16, 185, 129, 0.55));
      transform: scale(1.05);
    }

    .brand-text h1 { font-size: 0.95rem; font-weight: 800; color: var(--text-main); }
    .brand-text small { font-size: 0.7rem; color: var(--text-muted); display: block; }

    .header-actions { display: flex; align-items: center; gap: 6px; }
    .icon-action-btn {
      background: transparent;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      width: 32px;
      height: 32px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 1rem;
      transition: var(--transition);
    }
    .icon-action-btn:hover {
      color: var(--text-main);
      border-color: var(--border-focus);
      background: var(--bg-card-subtle);
    }

    .nav-scrollable {
      display: flex;
      flex-direction: column;
      flex: 1;
      overflow-y: auto;
      padding: 10px 8px;
      scrollbar-width: thin;
      scrollbar-color: var(--border-color) transparent;
    }

    .nav-list { display: flex; flex-direction: column; gap: 4px; list-style: none; }
    .nav-btn {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 9px 12px;
      border-radius: 8px;
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-muted);
      font-size: 0.85rem;
      font-weight: 500;
      cursor: pointer;
      width: 100%;
      text-align: left;
      white-space: nowrap;
      transition: all 0.15s ease;
    }
    .nav-btn .icon { font-size: 1.05rem; min-width: 24px; text-align: center; }
    .nav-btn:hover { background: var(--bg-card); color: var(--text-main); }

    /* ★ LOGO — nav ativa com gradiente subtil */
    .nav-btn.active {
      background: linear-gradient(90deg, rgba(16, 185, 129, 0.20) 0%, rgba(56, 189, 248, 0.08) 100%);
      color: var(--emerald-accent);
      border-color: rgba(16, 185, 129, 0.35);
      font-weight: 600;
    }

    .sidebar-divider {
      border: none;
      border-top: 1px solid var(--border-color);
      margin: 12px 6px;
    }

    .nav-btn.highlight {
      background: rgba(56, 189, 248, 0.08);
      color: var(--cyan-accent);
      border-color: rgba(56, 189, 248, 0.2);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .nav-btn.highlight.active {
      background: rgba(56, 189, 248, 0.2);
      border-color: var(--cyan-accent);
      color: #fff;
    }

    .subnav-tree {
      display: flex;
      flex-direction: column;
      gap: 2px;
      list-style: none;
      padding-left: 20px;
      margin: 4px 0 6px 14px;
      border-left: 2px solid var(--border-color);
    }

    .subnav-btn-side {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 7px 10px;
      border-radius: 6px;
      background: transparent;
      border: 1px solid transparent;
      color: var(--text-dim);
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      width: 100%;
      text-align: left;
      white-space: nowrap;
      transition: all 0.15s ease;
    }
    .subnav-btn-side:hover { color: var(--text-main); background: var(--bg-card); }

    /* ★ LOGO — subnav ativa com gradiente subtil */
    .subnav-btn-side.active {
      color: var(--cyan-accent);
      background: linear-gradient(90deg, rgba(16, 185, 129, 0.10) 0%, rgba(56, 189, 248, 0.15) 100%);
      border-color: rgba(56, 189, 248, 0.35);
      font-weight: 700;
    }

    .sidebar-widgets {
      padding: 12px 10px;
      border-top: 1px solid var(--border-color);
      background: rgba(8, 11, 17, 0.25);
      flex-shrink: 0;
    }

    .widget-box {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 12px;
      margin-bottom: 10px;
    }
    .widget-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
    .widget-title { font-size: 0.68rem; font-weight: 800; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; }
    .widget-pct { font-size: 0.82rem; font-weight: 800; color: var(--text-main); font-family: var(--font-mono); }
    .widget-sub { font-size: 0.68rem; color: var(--text-dim); display: flex; justify-content: space-between; margin-top: 6px; }

    .bar-bg { width: 100%; height: 5px; background: var(--border-color); border-radius: 99px; overflow: hidden; }
    .bar-fill { height: 100%; width: 0%; border-radius: 99px; transition: width 0.3s ease; }

    .course-prog-list { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
    .course-prog-item { display: flex; flex-direction: column; gap: 3px; }
    .course-prog-meta { display: flex; justify-content: space-between; font-size: 0.72rem; }
    .course-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }

    .app-viewport {
      flex: 1;
      margin-left: var(--sidebar-width);
      display: flex;
      flex-direction: column;
      min-width: 0;
      width: calc(100% - var(--sidebar-width));
      background-color: var(--bg-base);
    }

    header.topbar {
      height: 64px;
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 28px;
      position: sticky;
      top: 0;
      z-index: 90;
      width: 100%;
    }
    .user-tag { font-size: 0.82rem; font-weight: 600; color: var(--text-muted); display: flex; align-items: center; gap: 8px; min-width: 0; }
    .user-tag-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--emerald-accent); flex-shrink: 0; }
    .topbar-right { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }

    .sync-chip { font-size: 0.75rem; padding: 4px 10px; border-radius: 99px; border: 1px solid var(--border-color); color: var(--text-muted); background: var(--bg-base); }

    main.content-area { padding: 26px 32px; width: 100%; flex: 1; }
    .tab-content { display: none; }
    .tab-content.active { display: block; animation: fadeIn 0.15s ease-out; }

    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(4px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .grid-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .metric-card { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 10px; padding: 18px; transition: var(--transition); box-shadow: var(--shadow-sm); }
    .metric-card:hover { border-color: var(--border-focus); transform: translateY(-2px); box-shadow: var(--shadow-md); }
    .metric-title { font-size: 0.72rem; font-weight: 700; text-transform: uppercase; color: var(--text-dim); letter-spacing: 0.05em; }
    .metric-val { font-size: 1.85rem; font-weight: 800; margin: 6px 0; color: var(--text-main); font-family: var(--font-mono); }

    .panel { background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 12px; padding: 22px; margin-bottom: 24px; box-shadow: var(--shadow-sm); }
    .panel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 18px; border-bottom: 1px solid var(--border-color); padding-bottom: 12px; flex-wrap: wrap; gap: 8px; }
    .panel-title { font-size: 1.05rem; font-weight: 700; color: var(--text-main); }

    .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
    .field { display: flex; flex-direction: column; gap: 4px; }
    .field label { font-size: 0.75rem; font-weight: 600; color: var(--text-muted); }

    .active-course-card {
      background: linear-gradient(180deg, var(--bg-surface) 0%, var(--bg-card-subtle) 100%);
      border: 1px solid var(--border-color);
      border-left: 5px solid var(--border-focus);
      border-radius: var(--radius-md);
      padding: 18px 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      box-shadow: var(--shadow-card-elevated);
      transition: var(--transition);
      position: relative;
    }
    .active-course-card:hover {
      transform: translateY(-3px);
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
      border-color: var(--border-focus);
    }
    .active-course-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 8px;
    }
    .active-course-title {
      font-size: 1.05rem;
      font-weight: 800;
      color: var(--text-main);
      margin: 4px 0 12px 0;
      line-height: 1.35;
    }

    input, select, textarea {
      background: var(--bg-base);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 0.88rem;
      outline: none;
      width: 100%;
    }
    input:focus, select:focus, textarea:focus { border-color: var(--emerald-accent); }

    button { cursor: pointer; border-radius: 6px; font-weight: 600; font-size: 0.85rem; padding: 8px 14px; transition: all 0.15s ease; border: 1px solid transparent; display: inline-flex; align-items: center; gap: 6px; }
    .btn-emerald { background: var(--emerald-accent); color: #000; }
    .btn-emerald:hover { background: #059669; color: #fff; }
    .btn-subtle { background: var(--bg-surface); border-color: var(--border-color); color: var(--text-main); }
    .btn-subtle:hover { border-color: var(--border-focus); background: var(--bg-card-subtle); }
    .btn-puc { background: rgba(56, 189, 248, 0.15); border-color: rgba(56, 189, 248, 0.4); color: var(--cyan-accent); }
    .btn-puc:hover { background: var(--cyan-accent); color: #000; }
    .btn-danger { background: transparent; border-color: rgba(244, 63, 94, 0.3); color: var(--rose-accent); padding: 4px 8px; font-size: 0.78rem; }
    .btn-danger:hover { background: var(--rose-accent); color: #fff; }
    .btn-edit { background: rgba(56, 189, 248, 0.1); border-color: rgba(56, 189, 248, 0.3); color: var(--cyan-accent); padding: 4px 8px; font-size: 0.78rem; }
    .btn-edit:hover { background: var(--cyan-accent); color: #000; }

    .btn-clean-puc {
      background: rgba(245, 158, 11, 0.1);
      border-color: rgba(245, 158, 11, 0.35);
      color: var(--amber-accent);
      padding: 4px 8px;
      font-size: 0.78rem;
    }
    .btn-clean-puc:hover { background: var(--amber-accent); color: #000; }

    .table-container { overflow-x: auto; width: 100%; }
    table { width: 100%; border-collapse: collapse; font-size: 0.88rem; text-align: left; }
    th { background: var(--bg-card-subtle); padding: 12px 14px; color: var(--text-dim); font-size: 0.72rem; text-transform: uppercase; font-weight: 700; border-bottom: 1px solid var(--border-color); }
    td { padding: 12px 14px; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
    tr:last-child td { border-bottom: none; }
    tr:hover td { background: var(--bg-card-subtle); }

    .clickable-uc-title {
      color: var(--text-main); cursor: pointer; font-weight: 700; transition: color 0.15s ease; display: inline-flex; align-items: center; gap: 6px;
    }
    .clickable-uc-title:hover { color: var(--cyan-accent); text-decoration: underline; }

    .tag { font-size: 0.75rem; padding: 3px 8px; border-radius: 4px; font-weight: 700; display: inline-block; }
    .tag-passed { background: var(--emerald-glow); color: var(--emerald-accent); border: 1px solid rgba(16, 185, 129, 0.4); }
    .tag-active { background: rgba(56, 189, 248, 0.15); color: var(--cyan-accent); border: 1px solid rgba(56, 189, 248, 0.4); }

    .uc-filter-controls {
      display: flex; gap: 12px; margin-bottom: 16px; align-items: center; flex-wrap: wrap; background: var(--bg-surface); padding: 10px 14px; border-radius: 8px; border: 1px solid var(--border-color);
    }
    .filter-group { display: flex; align-items: center; gap: 6px; }
    .filter-group label { font-size: 0.75rem; color: var(--text-dim); font-weight: 600; text-transform: uppercase; }
    .filter-group select { width: auto; min-width: 130px; padding: 6px 10px; font-size: 0.8rem; }

    .uc-filter-bar { display: flex; gap: 8px; margin-bottom: 16px; overflow-x: auto; padding-bottom: 4px; }
    .uc-pill-btn {
      background: var(--bg-surface); border: 1px solid var(--border-color); color: var(--text-muted); padding: 6px 12px; border-radius: 20px; font-size: 0.8rem; cursor: pointer; display: flex; align-items: center; gap: 6px; white-space: nowrap;
    }
    .uc-pill-btn.active { background: var(--bg-card-subtle); border-color: var(--cyan-accent); color: var(--text-main); font-weight: 700; }

    .subview-section { display: none; }
    .subview-section.active { display: block; }

    .calendar-controls {
      background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-lg); padding: 14px 20px; margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; box-shadow: var(--shadow-sm);
    }
    .cal-toggles { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .cal-toggle-pill {
      display: flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 20px; border: 1px solid var(--border-color); background: var(--bg-surface); font-size: 12px; font-weight: 600; cursor: pointer; user-select: none;
    }
    .cal-toggle-pill.active { border-color: currentColor; box-shadow: var(--shadow-sm); }
    .cal-toggle-pill:not(.active) { opacity: 0.4; filter: grayscale(0.8); }
    .cal-nav { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .cal-month-title { font-size: 16px; font-weight: 700; font-family: var(--font-mono); min-width: 190px; text-align: center; }

    .calendar-grid-container {
      background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-lg); overflow: hidden; box-shadow: var(--shadow-md); display: flex; flex-direction: column; width: 100%;
    }
    .cal-weekdays {
      display: grid; grid-template-columns: repeat(7, 1fr); background: var(--bg-card-subtle); border-bottom: 1px solid var(--border-color); text-align: center; font-weight: 700; font-size: 11.5px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em;
    }
    .cal-weekdays div { padding: 10px 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .cal-days-grid { display: grid; grid-template-columns: repeat(7, 1fr); grid-auto-rows: minmax(98px, 1fr); width: 100%; }
    .cal-day-cell {
      border-right: 1px solid var(--border-color); border-bottom: 1px solid var(--border-color); padding: 6px 5px; display: flex; flex-direction: column; gap: 3px; background: var(--bg-card); min-width: 0; overflow: hidden;
    }
    .cal-day-cell:nth-child(7n) { border-right: none; }
    .cal-day-cell.other-month { background: var(--bg-base); opacity: 0.3; }
    .cal-day-cell.today { background: var(--primary-subtle); }
    .day-number { font-size: 11px; font-weight: 700; color: var(--text-dim); margin-bottom: 2px; font-family: var(--font-mono); }
    .cal-day-cell.today .day-number { color: var(--cyan-accent); }

    .cal-event-bar {
      font-size: 10px; font-weight: 700; padding: 2.5px 5px; border-radius: 4px; color: #fff; display: flex; justify-content: space-between; align-items: center; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,0.3); margin-bottom: 2px;
    }
    .cal-event-bar .event-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin-right: 4px; }
    .cal-event-bar .event-val {
      background: rgba(0,0,0,0.35); border-radius: 3px; padding: 0 4px; font-size: 9px; font-family: var(--font-mono); font-weight: 800; flex-shrink: 0;
    }

    .deliveries-toolbar {
      padding: 14px 18px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px 8px 0 0; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;
    }
    .deliveries-table-card {
      background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-md); overflow: hidden; box-shadow: var(--shadow-md); margin-bottom: 24px;
    }
    .deliveries-table { width: 100%; border-collapse: collapse; font-size: 13.5px; text-align: left; }
    .deliveries-table th {
      background: var(--bg-card-subtle); padding: 12px 14px; font-weight: 700; color: var(--text-dim); text-transform: uppercase; font-size: 11px; letter-spacing: 0.04em; border-bottom: 1px solid var(--border-color); cursor: pointer; user-select: none; white-space: nowrap;
    }
    .deliveries-table th:hover { color: var(--cyan-accent); }
    .deliveries-table td { padding: 12px 14px; border-bottom: 1px solid var(--border-color); vertical-align: middle; }
    .deliveries-table tr:hover td { background: var(--bg-card-subtle); }
    .deliveries-table tr.is-done td { opacity: 0.45; }
    .deliveries-table tr.is-done .deliv-task-name { text-decoration: line-through; }
    .deliveries-table tr.highlight-target td {
      background: rgba(56, 189, 248, 0.15) !important;
      border-color: var(--cyan-accent);
    }

    .uc-badge-pill { font-size: 11px; font-weight: 800; padding: 3px 8px; border-radius: 12px; display: inline-block; text-align: center; min-width: 38px; }
    .type-badge-pill { font-size: 10.5px; font-weight: 800; padding: 3px 8px; border-radius: 4px; text-transform: uppercase; }
    .type-async { background: #064e3b; color: #34d399; }
    .type-sync { background: #450a0a; color: #f87171; }
    .type-part { background: #3b2a00; color: #fbbf24; }

    .cota-badge {
      font-family: var(--font-mono); font-size: 12px; font-weight: 700; background: var(--bg-surface); padding: 3px 8px; border-radius: 4px; border: 1px solid var(--border-color); display: inline-block;
    }

    .weeks-toolbar {
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: 12px 18px; margin-bottom: 16px;
    }
    .week-block {
      background: var(--bg-card); border: 1px solid var(--border-color); border-radius: var(--radius-lg); margin-bottom: 14px; overflow: hidden; box-shadow: var(--shadow-sm);
    }
    .week-block.current-week { border: 2px solid var(--cyan-accent); box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.15); }
    .week-header {
      display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; cursor: pointer; user-select: none; background: var(--bg-card); flex-wrap: wrap; gap: 10px;
    }
    .week-header:hover { background: var(--bg-card-subtle); }
    .week-header-left { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
    .week-chevron { font-size: 12px; color: var(--text-dim); transition: transform 0.2s ease; }
    .week-block.expanded .week-chevron { transform: rotate(90deg); color: var(--cyan-accent); }
    .week-number { font-size: 15px; font-weight: 800; }

    .week-header-right { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

    .critical-due-badge {
      background: rgba(244, 63, 94, 0.15); border: 1px solid rgba(244, 63, 94, 0.45); color: #fca5a5; font-size: 11px; font-weight: 800; padding: 3px 10px; border-radius: 20px; display: inline-flex; align-items: center; gap: 5px;
    }
    .critical-open-badge {
      background: rgba(16, 185, 129, 0.15); border: 1px solid rgba(16, 185, 129, 0.45); color: #6ee7b7; font-size: 11px; font-weight: 800; padding: 3px 10px; border-radius: 20px; display: inline-flex; align-items: center; gap: 5px;
    }

    .week-dates { font-family: var(--font-mono); font-size: 11.5px; color: var(--text-muted); background: var(--bg-surface); padding: 3px 8px; border-radius: 20px; border: 1px solid var(--border-color); }
    .current-week-tag { background: var(--cyan-accent); color: #000; font-size: 10.5px; font-weight: 800; text-transform: uppercase; padding: 2px 8px; border-radius: 20px; }

    .week-content-body { display: none; padding: 0 20px 20px 20px; border-top: 1px solid var(--border-color); }
    .week-block.expanded .week-content-body { display: block; }
    .week-cards-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; padding-top: 16px; }

    .smart-checkbox {
      appearance: none; width: 19px; height: 19px; border: 2px solid var(--border-color); border-radius: 5px; outline: none; cursor: pointer; display: grid; place-content: center; background: var(--bg-base); flex-shrink: 0;
    }
    .smart-checkbox:hover { border-color: var(--cyan-accent); }
    .smart-checkbox:checked { background: var(--emerald-accent); border-color: var(--emerald-accent); }
    .smart-checkbox:checked::before { content: "✓"; color: #000; font-size: 12px; font-weight: 800; }

    .uc-activity-card {
      background: var(--bg-surface); border: 1px solid var(--border-color); border-radius: var(--radius-md); padding: 18px; display: flex; flex-direction: column; gap: 10px; transition: var(--transition); box-shadow: var(--shadow-sm); min-width: 0;
    }
    .uc-activity-card:hover { border-color: var(--border-focus); }

    .uc-activity-card .card-study-body {
      display: flex; flex-direction: column; gap: 8px; transition: var(--transition);
    }
    .uc-activity-card.is-done .card-study-body { opacity: 0.45; filter: grayscale(0.85); }
    .uc-activity-card.is-done .card-task-title { text-decoration: line-through; }

    .card-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; }
    .card-task-title { font-size: 14.5px; font-weight: 700; color: var(--text-main); line-height: 1.35; }
    .card-topics { font-size: 12.5px; color: var(--text-muted); line-height: 1.45; }

    .card-tags-group { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; margin-bottom: 4px; }
    .topic-tag {
      font-size: 11px; font-weight: 600; padding: 3px 8px; border-radius: 4px; background: var(--bg-card-subtle); color: var(--text-muted); border: 1px solid var(--border-color); white-space: nowrap;
    }

    .card-highlight-bar {
      margin-top: auto; display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; font-weight: 700; padding: 8px 12px; border-radius: var(--radius-sm); line-height: 1.3;
    }
    .card-highlight-bar.is-done { opacity: 0.50 !important; filter: grayscale(0.8); text-decoration: line-through; }
    .card-highlight-bar.alert-danger { background: rgba(220, 38, 38, 0.14); border: 1px solid rgba(220, 38, 38, 0.4); color: #fca5a5; }
    .card-highlight-bar.alert-open { background: rgba(16, 185, 129, 0.14); border: 1px solid rgba(16, 185, 129, 0.4); color: #6ee7b7; }
    .card-highlight-bar.alert-fullcycle {
      background: linear-gradient(90deg, rgba(16, 185, 129, 0.16) 0%, rgba(220, 38, 38, 0.16) 100%);
      border: 1px solid rgba(245, 158, 11, 0.45); color: #fed7aa;
    }
    .card-highlight-bar.alert-info { background: var(--bg-card-subtle); border: 1px solid var(--border-color); color: var(--text-muted); }
    .highlight-date-span { font-family: var(--font-mono); font-size: 11px; font-weight: 800; opacity: 0.95; flex-shrink: 0; }
    .highlight-clickable-title { cursor: pointer; display: inline-flex; align-items: center; gap: 4px; transition: opacity 0.15s ease; }
    .highlight-clickable-title:hover { text-decoration: underline; opacity: 0.9; }

    .toast-container {
      position: fixed; top: 20px; right: 20px; z-index: 99999; display: flex; flex-direction: column; gap: 10px; max-width: 420px; pointer-events: none;
    }
    .toast-card {
      background: #111726; border: 1px solid #24344d; border-left: 5px solid var(--cyan-accent); padding: 16px; border-radius: var(--radius-md); box-shadow: var(--shadow-toast); pointer-events: auto; display: flex; flex-direction: column; gap: 6px;
    }
    html[data-theme="light"] .toast-card {
      background: #ffffff; border-color: #cbd5e1;
    }
    .toast-card.urgent { border-left-color: var(--rose-accent); background: #1c111a; }
    html[data-theme="light"] .toast-card.urgent { background: #fff1f2; }
    .toast-header { display: flex; justify-content: space-between; align-items: center; }
    .toast-title { font-size: 13.5px; font-weight: 800; color: var(--text-main); }
    .toast-desc { font-size: 12px; color: var(--text-muted); }
    .toast-footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; border-top: 1px solid var(--border-color); padding-top: 6px; }
    .toast-btn { font-size: 11px; padding: 4px 10px; border-radius: var(--radius-sm); cursor: pointer; font-weight: 600; border: none; }
    .toast-btn-dismiss { background: var(--bg-card-subtle); color: var(--text-muted); }
    .toast-btn-mute { background: var(--cyan-accent); color: #000; }

    .roadmap-course-card {
      background: var(--bg-surface); border: 1px solid var(--border-color); border-radius: 10px; padding: 20px; margin-bottom: 22px; box-shadow: var(--shadow-sm);
    }
    .roadmap-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px; flex-wrap: wrap; gap: 12px; }
    .puc-block { background: var(--bg-base); border: 1px solid var(--border-color); border-radius: 8px; padding: 14px 16px; margin-top: 14px; }
    .puc-block-title { font-size: 0.82rem; font-weight: 800; color: var(--cyan-accent); text-transform: uppercase; margin-bottom: 8px; }

    .modal-overlay {
      display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(4, 6, 10, 0.78); backdrop-filter: blur(4px); -webkit-backdrop-filter: blur(4px); z-index: 1000; align-items: center; justify-content: center; padding: 16px;
    }
    .modal-overlay.active { display: flex; animation: fadeIn 0.15s ease-out; }
    .modal-container {
      background: var(--bg-surface); border: 1px solid var(--border-color); border-radius: 12px; max-width: 680px; width: 100%; padding: 24px; max-height: 90vh; overflow-y: auto; box-shadow: var(--shadow-md);
    }
    .modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-color); padding-bottom: 14px; margin-bottom: 18px; }
    .modal-title { font-size: 1.15rem; font-weight: 700; color: var(--text-main); display: flex; align-items: center; gap: 8px; }
    .modal-close-btn { background: transparent; border: none; color: var(--text-dim); font-size: 1.3rem; cursor: pointer; line-height: 1; }

    .entry-mode-selector { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 18px; }
    .mode-card {
      background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; cursor: pointer; display: flex; flex-direction: column; gap: 4px;
    }
    .mode-card:hover { border-color: var(--cyan-accent); }
    .mode-card.selected { border-color: var(--cyan-accent); background: rgba(56, 189, 248, 0.08); }
    .mode-card-title { font-weight: 700; font-size: 0.9rem; color: var(--text-main); }
    .mode-card-desc { font-size: 0.75rem; color: var(--text-dim); }

    .puc-flow-step {
      background: var(--bg-card-subtle); border: 1px solid var(--border-color); border-radius: 8px; padding: 14px; margin-bottom: 14px;
    }
    .puc-step-title {
      font-size: 0.88rem; font-weight: 700; color: var(--text-main); margin-bottom: 8px; display: flex; align-items: center; justify-content: space-between;
    }
    .prompt-box-mini {
      background: var(--bg-base); border: 1px solid var(--border-color); border-radius: 6px; padding: 12px; font-family: var(--font-mono); font-size: 0.75rem; color: var(--text-muted); white-space: pre-wrap; max-height: 160px; overflow-y: auto; user-select: all;
    }

    .settings-section-card {
      background: var(--bg-card-subtle); border: 1px solid var(--border-color); border-radius: 10px; padding: 16px; margin-bottom: 16px;
    }
    .settings-section-title {
      font-size: 0.85rem; font-weight: 800; color: var(--cyan-accent); text-transform: uppercase; margin-bottom: 12px; display: flex; align-items: center; gap: 6px;
    }

    .notif-setting-row {
      display: flex; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--border-color);
    }
    .notif-setting-row:last-child { border-bottom: none; }
    .notif-label-group { display: flex; flex-direction: column; gap: 3px; max-width: 72%; }
    .notif-label-title { font-size: 0.9rem; font-weight: 700; color: var(--text-main); }
    .notif-label-sub { font-size: 0.76rem; color: var(--text-muted); }

    .ios-switch { position: relative; display: inline-block; width: 48px; height: 26px; flex-shrink: 0; }
    .ios-switch input { opacity: 0; width: 0; height: 0; }
    .slider-toggle { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: var(--border-color); transition: .25s; border-radius: 34px; }
    .slider-toggle:before { position: absolute; content: ""; height: 20px; width: 20px; left: 3px; bottom: 3px; background-color: white; transition: .25s; border-radius: 50%; }
    input:checked + .slider-toggle { background-color: #3b82f6; }
    input:checked + .slider-toggle:before { transform: translateX(22px); }

    .btn-notif-action {
      background: var(--bg-card); border: 1px solid var(--border-color); color: var(--text-main); font-weight: 700; font-size: 0.82rem; padding: 6px 12px; border-radius: 6px;
    }
    .btn-notif-action:hover { background: var(--border-color); }
    .btn-notif-blue {
      background: #3b82f6; border: none; color: #fff; font-weight: 700; font-size: 0.82rem; padding: 7px 14px; border-radius: 6px;
    }
    .btn-notif-blue:hover { background: #2563eb; }

    .milestone-editor-card {
      background: var(--bg-card); border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; margin-bottom: 10px; display: flex; flex-direction: column; gap: 8px;
    }
    .milestone-editor-row { display: grid; grid-template-columns: 2fr 1fr auto; gap: 8px; align-items: center; }
    .milestone-editor-dates { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .milestone-editor-dates label { font-size: 0.7rem; color: var(--text-dim); font-weight: 700; text-transform: uppercase; margin-bottom: 2px; display: block; }

    .storage-compare-grid {
      display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 16px 0;
    }
    .storage-compare-col {
      background: var(--bg-base); border: 1px solid var(--border-color); border-radius: 8px; padding: 14px;
    }
    .storage-compare-title {
      font-size: 0.7rem; font-weight: 800; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;
    }
    .storage-compare-row {
      display: flex; justify-content: space-between; font-size: 0.8rem; padding: 4px 0; border-bottom: 1px dashed var(--border-color);
    }
    .storage-compare-row:last-child { border-bottom: none; }
    .storage-compare-row b { font-family: var(--font-mono); color: var(--text-main); word-break: break-all; }
    .storage-action-stack { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }

    nav.bottom-nav { display: none; }

    @media (max-width: 900px) {
      .mobile-only { display: inline-flex !important; }
      .desktop-only { display: none !important; }

      aside.sidebar { display: none !important; }
      .app-viewport, .app-viewport.expanded { margin-left: 0 !important; width: 100% !important; padding-bottom: 64px; }

      header.topbar { padding: 0 12px; gap: 8px; }
      .user-tag { max-width: 45%; overflow: hidden; }
      .user-tag span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .topbar-right { gap: 6px; }
      .sync-chip { font-size: 0; padding: 6px 9px; min-width: 16px; display: inline-flex; justify-content: center; align-items: center; }
      .sync-chip::before { content: '●'; font-size: 13px; line-height: 1; }

      main.content-area { padding: 16px; }

      .calendar-controls { padding: 12px 14px; }
      .cal-nav { width: 100%; justify-content: space-between; }
      .cal-month-title { order: -1; flex-basis: 100%; min-width: 0; text-align: center; font-size: 15px; padding: 2px 0; }
      .cal-nav .btn-subtle { padding: 6px 10px; font-size: 0.75rem; }
      .cal-toggles { width: 100%; justify-content: center; }

      .week-cards-grid { grid-template-columns: 1fr; padding-top: 12px; }
      .week-header { flex-direction: column; align-items: stretch; padding: 12px 14px; gap: 8px; }
      .week-header-left { justify-content: flex-start; }
      .week-header-right { width: 100%; justify-content: space-between; }
      .week-content-body { padding: 0 14px 14px 14px; }
      .uc-activity-card { padding: 14px; }

      .deliveries-toolbar { flex-direction: column; align-items: stretch; padding: 12px 14px; gap: 10px; }
      .deliveries-toolbar > div { flex-direction: column !important; align-items: stretch !important; gap: 6px; width: 100%; }
      .deliveries-toolbar label { margin-bottom: 2px; }
      .deliveries-toolbar select { width: 100% !important; min-width: 0; }
      #delivCountLabel { text-align: center; }

      .uc-filter-bar { padding-bottom: 6px; }

      .modal-container { padding: 18px; }
      .entry-mode-selector { grid-template-columns: 1fr; }
      .storage-compare-grid { grid-template-columns: 1fr; }

      nav.bottom-nav {
        display: flex !important;
        position: fixed;
        bottom: 0; left: 0; right: 0;
        background: var(--bg-surface);
        border-top: 1px solid var(--border-color);
        height: 60px;
        z-index: 1000;
        justify-content: space-around;
        align-items: center;
      }
      .bottom-btn {
        background: transparent; border: none; color: var(--text-dim);
        display: flex; flex-direction: column; align-items: center; gap: 3px;
        font-size: 0.68rem; font-weight: 500; width: 100%; height: 100%; justify-content: center;
      }
      .bottom-btn.active { color: var(--emerald-accent); font-weight: 700; }
      .bottom-btn span:first-child { font-size: 1.1rem; }
    }
  </style>
</head>
<body>

<!-- ★ LOGO — SVG sprite reutilizável (sidebar, futuro favicon em mobile, etc.) -->
<svg width="0" height="0" style="position:absolute" aria-hidden="true" focusable="false">
  <defs>
    <linearGradient id="musLogoGrad" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%"   stop-color="#0b3d3d"/>
      <stop offset="45%"  stop-color="#0d9488"/>
      <stop offset="100%" stop-color="#10b981"/>
    </linearGradient>

    <symbol id="musLogo" viewBox="0 0 240 200">
      <g stroke="url(#musLogoGrad)" stroke-width="8" fill="none"
         stroke-linecap="round" stroke-linejoin="round">
        <path d="M 55 32 L 55 122 L 120 176 L 185 122 L 185 32"/>
        <path d="M 88 56 L 88 112 L 120 145 L 152 112 L 152 56"/>
        <path d="M 112 78 L 112 100 L 120 108 L 128 100 L 128 78"/>
        <path d="M 55 32 L 100 32"/>
        <path d="M 140 32 L 185 32"/>
        <path d="M 120 32 L 120 88"/>
      </g>
      <g fill="url(#musLogoGrad)">
        <circle cx="55"  cy="32"  r="11"/>
        <circle cx="120" cy="32"  r="11"/>
        <circle cx="185" cy="32"  r="11"/>
        <circle cx="88"  cy="56"  r="9"/>
        <circle cx="152" cy="56"  r="9"/>
        <circle cx="55"  cy="122" r="9"/>
        <circle cx="185" cy="122" r="9"/>
        <circle cx="120" cy="176" r="12"/>
        <circle cx="120" cy="88"  r="8"/>
      </g>
    </symbol>
  </defs>
</svg>

<aside class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <div class="brand-content">
      <svg class="brand-logo" aria-label="My UniStation" role="img">
        <use href="#musLogo"/>
      </svg>
      <div class="brand-text">
        <h1>My UniStation</h1>
      </div>
    </div>
    <div class="header-actions">
      <button class="icon-action-btn" onclick="toggleTheme()" id="themeToggleBtn" title="Alternar Light / Dark Mode">☀️</button>
      <button class="icon-action-btn" onclick="openCentralSettingsModal()" title="Definições Gerais do Sistema">⚙️</button>
    </div>
  </div>

  <div class="nav-scrollable">
    <ul class="nav-list">
      <li><button class="nav-btn active" onclick="navigate('tab-inicio')"><span class="icon">📊</span><span>Resumo Académico</span></button></li>
    </ul>

    <hr class="sidebar-divider">

    <ul class="nav-list">
      <li>
        <button class="nav-btn highlight" id="btnSemestreParent" onclick="toggleSemesterTree(event)">
          <span style="display:flex; align-items:center; gap:10px;">
            <span class="icon">🚀</span>
            <span>Semestre em Curso</span>
          </span>
          <span id="semestreChevron" style="font-size:0.7rem; color:var(--cyan-accent);">▼</span>
        </button>
        <ul class="subnav-tree" id="semesterSubNavTree" style="display: flex;">
          <li>
            <button class="subnav-btn-side active" id="sideSubBtn-calendario" onclick="openSemesterSubView('sub-calendario')">
              <span>📅</span><span>Calendário Geral</span>
            </button>
          </li>
          <li>
            <button class="subnav-btn-side" id="sideSubBtn-roteiro" onclick="openSemesterSubView('sub-roteiro')">
              <span>🗺️</span><span>Roteiro Semanal</span>
            </button>
          </li>
          <li>
            <button class="subnav-btn-side" id="sideSubBtn-entregas" onclick="openSemesterSubView('sub-entregas')">
              <span>📋</span><span>Atividades Sumativas</span>
            </button>
          </li>
          <li>
            <button class="subnav-btn-side" id="sideSubBtn-dossie" onclick="openSemesterSubView('sub-dossie')">
              <span>📘</span><span>PUC das UC</span>
            </button>
          </li>
        </ul>
      </li>
    </ul>
  </div>

  <div class="sidebar-widgets">
    <div class="widget-box">
      <div class="widget-header">
        <span class="widget-title">Progresso Global</span>
        <span class="widget-pct" id="sideGlobalPct">0%</span>
      </div>
      <div class="bar-bg">
        <!-- ★ LOGO — gradiente esmeralda→ciano no progresso global -->
        <div class="bar-fill" id="sideGlobalBar" style="background:linear-gradient(90deg, #10b981 0%, #38bdf8 100%);"></div>
      </div>
      <div class="widget-sub">
        <span id="sideGlobalCounts">0/0 concluídas</span>
        <span>Estudo & Entregas</span>
      </div>
    </div>

    <div class="widget-box" style="margin-bottom:0;">
      <span class="widget-title">Progresso por UC</span>
      <div class="course-prog-list" id="sideCourseList"></div>
    </div>
  </div>
</aside>

<div class="app-viewport" id="viewport">
  <header class="topbar">
    <div class="user-tag">
      <div class="user-tag-dot"></div>
      <span id="userDegreeDisplay">Aluno • Engenharia Informática</span>
    </div>
    <div class="topbar-right">
      <button class="icon-action-btn mobile-only" onclick="toggleTheme()" id="themeToggleBtnMobile" title="Alternar Light / Dark Mode">☀️</button>
      <button class="icon-action-btn mobile-only" onclick="openCentralSettingsModal()" title="Definições do Sistema">⚙️</button>
      <div id="syncBadge" class="sync-chip">A sincronizar...</div>
      <button class="btn-subtle desktop-only" onclick="exportData()">📥 Backup</button>
    </div>
  </header>

  <div class="toast-container" id="toastContainer"></div>

  <main class="content-area">
    <section id="tab-inicio" class="tab-content active">
      <div class="grid-metrics">
        <div class="metric-card">
          <div class="metric-title">Progresso da Licenciatura</div>
          <div class="metric-val" id="statsEcts">0 / 180 ECTS</div>
          <!-- ★ LOGO — gradiente esmeralda→ciano na barra ECTS -->
          <div class="bar-bg"><div class="bar-fill" id="statsProgressBar" style="background:linear-gradient(90deg, #10b981 0%, #38bdf8 100%);"></div></div>
        </div>
        <div class="metric-card">
          <div class="metric-title">Média Ponderada</div>
          <div class="metric-val" id="statsGpa">—</div>
          <small style="color:var(--text-dim); font-size:0.75rem;">Créditos ECTS concluídos</small>
        </div>
        <div class="metric-card">
          <div class="metric-title">UCs em Frequência</div>
          <div class="metric-val" id="statsActive">0</div>
          <small style="color:var(--text-dim); font-size:0.75rem;">Semestre atual</small>
        </div>
        <div class="metric-card">
          <div class="metric-title">Concluídas</div>
          <div class="metric-val" id="statsDone">0</div>
          <small style="color:var(--text-dim); font-size:0.75rem;">Aprovadas / Equivalências</small>
        </div>
      </div>

      <div class="panel">
        <div class="panel-header">
          <div>
            <span class="panel-title">⚡ Unidades Curriculares em Curso (Semestre Atual)</span>
            <p style="color:var(--text-dim); font-size:0.8rem; margin-top:2px;">Acompanhamento e gestão ativa das disciplinas em frequência com acesso direto ao planeamento.</p>
          </div>
          <div style="display:flex; gap:8px;">
            <button class="btn-puc" onclick="openUcModal('puc')">🤖 Importar PUC (JSON)</button>
            <button class="btn-emerald" onclick="openSemesterSubView('sub-calendario')">Abrir Calendário Geral 📅</button>
          </div>
        </div>
        <div id="activeCardsContainer" class="form-grid"></div>
      </div>

      <div class="panel" style="padding-bottom:14px;">
        <div class="panel-header">
          <div>
            <span class="panel-title">🎓 Histórico de Unidades Curriculares Concluídas</span>
            <p style="color:var(--text-dim); font-size:0.8rem; margin-top:2px;">Clica no nome da UC para consultar o detalhe dos momentos e trabalhos realizados.</p>
          </div>
          <button class="btn-emerald" onclick="openUcModal('manual')">➕ Registar UC Passada / Concluída</button>
        </div>

        <div class="uc-filter-controls">
          <div class="filter-group">
            <label>Ano:</label>
            <select id="filterYearSelect" onchange="renderDoneUcTable()">
              <option value="all">Todos os Anos</option>
              <option value="1">1º Ano</option>
              <option value="2">2º Ano</option>
              <option value="3">3º Ano</option>
            </select>
          </div>
          <div class="filter-group">
            <label>Semestre:</label>
            <select id="filterSemSelect" onchange="renderDoneUcTable()">
              <option value="all">Todos os Semestres</option>
              <option value="1">1º Semestre</option>
              <option value="2">2º Semestre</option>
              <option value="Anual">Anual</option>
            </select>
          </div>
        </div>

        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Unidade Curricular (Detalhes ↗)</th>
                <th>Ano Letivo</th>
                <th>Ano / Sem</th>
                <th>ECTS</th>
                <th>Regime</th>
                <th>Classificação Oficial</th>
                <th>Estado</th>
                <th style="text-align:right;">Ações</th>
              </tr>
            </thead>
            <tbody id="doneCoursesTableBody"></tbody>
          </table>
        </div>
      </div>
    </section>

    <section id="tab-semestre" class="tab-content">
      <div class="panel-header">
        <div>
          <h2 style="font-size:1.25rem; font-weight:800; color:var(--text-main);" id="semesterViewHeaderTitle">
            🚀 Semestre em Curso • Calendário Geral
          </h2>
          <p style="color:var(--text-muted); font-size:0.85rem; margin-top:2px;">
            Acompanhamento operacional das atividades, entregas e fichas oficiais das UCs em frequência.
          </p>
        </div>
        <button class="btn-puc" onclick="openUcModal('puc')">+ Importar UC via PUC (JSON)</button>
      </div>

      <div id="sub-calendario" class="subview-section active">
        <div class="calendar-controls">
          <div class="cal-toggles" id="calUcToggles"></div>
          <div class="cal-nav">
            <button class="btn-subtle" id="prevMonthBtn">◀ Anterior</button>
            <button class="btn-subtle" onclick="goToCurrentCalendarMonth()" title="Centrar no Mês e Ano Atual">🎯 Hoje</button>
            <span class="cal-month-title" id="calMonthDisplay">Setembro 2026</span>
            <button class="btn-subtle" id="nextMonthBtn">Seguinte ▶</button>
          </div>
        </div>

        <div class="calendar-grid-container">
          <div class="cal-weekdays">
            <div>Segunda</div><div>Terça</div><div>Quarta</div><div>Quinta</div><div>Sexta</div><div>Sábado</div><div>Domingo</div>
          </div>
          <div class="cal-days-grid" id="calDaysGrid"></div>
        </div>
      </div>

      <div id="sub-roteiro" class="subview-section">
        <div class="weeks-toolbar">
          <button class="btn-subtle" onclick="focusCurrentWeek(true)">🎯 Centrar na Semana Atual</button>
          <div style="display:flex; gap:8px;">
            <button class="btn-subtle" onclick="expandAllWeeks(true)">Expandir Todas</button>
            <button class="btn-subtle" onclick="expandAllWeeks(false)">Minimizar Todas</button>
          </div>
        </div>
        <div id="unifiedWeeklyRoadmapContainer"></div>
      </div>

      <div id="sub-entregas" class="subview-section">
        <div class="deliveries-table-card">
          <div class="deliveries-toolbar">
            <div style="display: flex; align-items: center; gap: 8px;">
              <label style="font-size: 11.5px; font-weight: 700; color: var(--text-dim); text-transform: uppercase;">Filtrar UC:</label>
              <select id="delivFilterUcSelect" onchange="renderConsolidatedDeliveriesTable()" style="padding: 6px 10px; font-size: 0.85rem;">
                <option value="ALL">Todas as Disciplinas</option>
              </select>
            </div>
            <div style="font-size: 12px; color: var(--text-muted); font-family: var(--font-mono);" id="delivCountLabel">
              A mostrar 0 momentos de avaliação
            </div>
          </div>

          <div class="table-container">
            <table class="deliveries-table">
              <thead>
                <tr>
                  <th style="width: 40px; text-align: center;">Status</th>
                  <th onclick="sortDeliveries('ucCode')">UC ↕</th>
                  <th onclick="sortDeliveries('name')">Atividade / Momento Oficial ↕</th>
                  <th onclick="sortDeliveries('type')">Tipo ↕</th>
                  <th onclick="sortDeliveries('date_start')">Data Início ↕</th>
                  <th onclick="sortDeliveries('date_due')">Data Limite (Hora) ↕</th>
                  <th onclick="sortDeliveries('weight')">Cotação ↕</th>
                  <th style="text-align:right;">Ações</th>
                </tr>
              </thead>
              <tbody id="consolidatedDeliveriesTbody"></tbody>
            </table>
          </div>
        </div>
      </div>

      <div id="sub-dossie" class="subview-section">
        <div class="uc-filter-bar" id="dossierUcFilterBar"></div>
        <div id="dossierContent"></div>
      </div>
    </section>
  </main>
</div>

<nav class="bottom-nav" id="bottomNav">
  <button class="bottom-btn active" data-tab="tab-inicio" onclick="navigate('tab-inicio')">
    <span>🏠</span><span>Início</span>
  </button>
  <button class="bottom-btn" data-tab="sub-calendario" onclick="openSemesterSubView('sub-calendario')">
    <span>📅</span><span>Calendário</span>
  </button>
  <button class="bottom-btn" data-tab="sub-roteiro" onclick="openSemesterSubView('sub-roteiro')">
    <span>🗺️</span><span>Roteiro</span>
  </button>
  <button class="bottom-btn" data-tab="sub-entregas" onclick="openSemesterSubView('sub-entregas')">
    <span>📋</span><span>Entregas</span>
  </button>
  <button class="bottom-btn" data-tab="sub-dossie" onclick="openSemesterSubView('sub-dossie')">
    <span>📘</span><span>PUC</span>
  </button>
</nav>

<div class="modal-overlay" id="ucModalOverlay" onclick="closeUcModal(event)">
  <div class="modal-container" onclick="event.stopPropagation()">
    <div class="modal-header">
      <h3 class="modal-title" id="ucModalTitle">Adicionar / Configurar Unidade Curricular</h3>
      <button class="modal-close-btn" onclick="closeUcModal()">✕</button>
    </div>

    <div class="entry-mode-selector" id="entryModeSelector">
      <div class="mode-card selected" id="cardModeManual" onclick="selectEntryMode('manual')">
        <div class="mode-card-title">✍️ Introdução Manual</div>
        <div class="mode-card-desc">Regista uma UC atual ou passada para alimentar as estatísticas e histórico.</div>
      </div>
      <div class="mode-card" id="cardModePuc" onclick="selectEntryMode('puc')">
        <div class="mode-card-title">📄 Pelo PUC (JSON)</div>
        <div class="mode-card-desc">Gera a prompt ou carrega o ficheiro JSON estruturado pelo assistente de IA.</div>
      </div>
    </div>

    <div id="pucUploadSection" style="display:none; margin-bottom:18px;">
      <div class="puc-flow-step">
        <div class="puc-step-title">
          <span>1. Como gerar o JSON do PUC</span>
          <button type="button" class="btn-emerald" style="font-size:0.75rem; padding:4px 10px;" onclick="copyMasterPrompt()">📋 Copiar Prompt PUC</button>
        </div>
        <p style="font-size:0.8rem; color:var(--text-muted); margin-bottom:8px;">
          Copia a Master Prompt abaixo e cola-a no teu modelo de IA (ChatGPT, Claude ou Ollama) juntamente com o PDF do PUC para obteres o JSON exato:
        </p>
        <div class="prompt-box-mini" id="masterPromptText">Atua como um Engenheiro de Dados Curriculares e Auditor Pedagógico da Universidade.
O teu objetivo é processar o documento do Plano da Unidade Curricular (PUC) fornecido e extrair a TOTALIDADE INTEGRAL e FIDEDIGNA do seu conteúdo para uma estrutura JSON exaustiva.

O PUC é a FONTE ÚNICA DA VERDADE. O JSON gerado deve cumprir um duplo propósito:
1. Conter os dados operacionais estruturados para alimentar os motores de Calendário, Roteiro Semanal e Cálculo de Médias da aplicação.
2. Preservar no "Dossiê do PUC" a TRANSCRIÇÃO INTEGRAL e LITERAL das tabelas originais (Plano de Atividades e Calendário de Avaliação), permitindo ao estudante consultar a letra exata do regulamento em caso de dúvida.

A resposta deve ser ESTRITAMENTE um único bloco JSON válido, sem texto introdutório nem conclusões.

### REGRAS CRÍTICAS DE EXTRAÇÃO:
1. IDENTIFICAÇÃO E REGIME:
   - "name": Nome exato da UC.
   - "code": Código numérico oficial (ex: "21048", "21053", "21078", "21093", "21106").
   - "academicYear": Ano letivo indicado (ex: "2026/2027").
   - "ects": Número de créditos (normalmente 6).
   - "model": "uab_standard" (se Tipologia 1 com 8v assíncronos + 12v síncronos) ou "puc_flexible" (se Tipologia 3 ou regime misto).

2. MOMENTOS DE AVALIAÇÃO ("milestones"):
   - Decompoõe cada momento sumativo: "weight" (cotação decimal), "type" ("efolio" ou "exam"), "date_start", "date_due" (formato ISO "AAAA-MM-DDTHH:MM" ou "AAAA-MM-DD") e "description".

3. ROTEIRO SEMANAL COMPLETO ("weekly_roadmap"):
   - Decompoõe sempre as 16 semanas letivas do semestre (semanas 0 a 16).

4. TRANSCRIÇÃO LITERAL DA FONTE DA VERDADE ("puc_raw_sections"):
   - "raw_activity_plan", "raw_evaluation_schedule", "raw_approval_rules".

Schema obrigatório:
{
  "name": "Nome da Unidade Curricular",
  "code": "Código numérico",
  "academicYear": "AAAA/AAAA",
  "ects": 6,
  "year": 1,
  "sem": "1",
  "faculty": { "regent": "Nome do Professor Regente", "team": ["Docente 2"] },
  "presentation": "Texto integral da apresentação...",
  "learning_outcomes": ["RA1: Descrição..."],
  "competences": ["[C1]: Descrição..."],
  "methodology_and_tools": "Metodologia completa...",
  "approval_conditions": { "model": "uab_standard ou puc_flexible", "regime": "...", "weights_summary": "...", "chain_rules": "...", "sync_min_grade": "...", "exam_rules": "...", "summary": "..." },
  "milestones": [ { "id": "deliv_1", "name": "...", "type": "efolio | exam", "weight": 3.0, "date_start": "AAAA-MM-DD", "date_due": "AAAA-MM-DDTHH:MM", "grade": null, "description": "..." } ],
  "weekly_roadmap": [ { "week": 1, "topic": "...", "activities": "...", "completed": false } ],
  "bibliography": { "mandatory": ["..."], "complementary": ["..."], "tools": "..." },
  "puc_raw_sections": {
    "raw_activity_plan": [ { "activity_or_theme": "...", "period": "...", "contents_or_topic": "...", "summary_description": "...", "resources": "..." } ],
    "raw_evaluation_schedule": [ { "activity_name": "...", "weight_text": "...", "modality": "...", "release_date": "...", "due_date": "...", "feedback_date": "..." } ],
    "raw_approval_rules": "Transcrição integral dos critérios cumulativos de avaliação e recurso."
  }
}</div>
      </div>

      <div class="puc-flow-step" style="border-style:dashed; text-align:center; padding:18px;">
        <div class="puc-step-title" style="justify-content:center; margin-bottom:6px;">2. Submeter o JSON do PUC</div>
        <p style="font-size:0.8rem; color:var(--text-muted); margin-bottom:12px;">Carrega o ficheiro gerado para integrar automaticamente na aplicação:</p>
        <input type="file" id="pucModalFileInput" accept=".json" style="display:none;" onchange="importPucJSON(event)">
        <button type="button" class="btn-puc" onclick="document.getElementById('pucModalFileInput').click()">📁 Escolher Ficheiro JSON do PUC</button>
      </div>
    </div>

    <form id="courseForm" onsubmit="handleSaveCourse(event)" novalidate>
      <input type="hidden" id="editingCourseIndex" value="-1">

      <div class="form-grid">
        <div class="field" style="grid-column: span 2;">
          <label>Nome da Unidade Curricular</label>
          <input type="text" id="cName" placeholder="Ex: Sistemas em Rede">
        </div>

        <div class="field">
          <label>Código Oficial da UC</label>
          <input type="text" id="cCode" placeholder="Ex: 21106">
        </div>

        <div class="field">
          <label>Ano Letivo de Frequência</label>
          <input type="text" id="cAcademicYear" placeholder="Ex: 2026/2027">
        </div>

        <div class="field">
          <label>Créditos ECTS</label>
          <input type="number" id="cEcts" value="6" min="1" max="30">
        </div>

        <div class="field">
          <label>Ano Curricular</label>
          <select id="cYear">
            <option value="1">1º Ano</option>
            <option value="2">2º Ano</option>
            <option value="3">3º Ano</option>
          </select>
        </div>

        <div class="field">
          <label>Semestre</label>
          <select id="cSem">
            <option value="1">1º Semestre</option>
            <option value="2">2º Semestre</option>
            <option value="Anual">Anual</option>
          </select>
        </div>

        <div class="field">
          <label>Regime Pedagógico</label>
          <select id="cModel">
            <option value="uab_standard">Regime Geral (e-Fólios + Global)</option>
            <option value="puc_flexible">Flexível / PUC / Recurso (0-20)</option>
          </select>
        </div>

        <div class="field">
          <label>Estado da UC</label>
          <select id="cStatus" onchange="toggleGradeFieldVisibility()">
            <option value="ongoing">⚡ Em Frequência (A Decorrer)</option>
            <option value="completed">🎓 Concluída / Aprovada (Passada)</option>
          </select>
        </div>

        <div class="field" id="finalGradeFieldContainer" style="display:none;">
          <label>Nota Final Oficial (0.0 a 20.0)</label>
          <input type="number" id="cFinalGrade" min="0" max="20" step="0.1" placeholder="Ex: 14.5">
        </div>
      </div>

      <div id="manualBreakdownContainer" style="display:block; margin-top:16px; background:var(--bg-base); border:1px solid var(--border-color); border-radius:8px; padding:16px;">
        <div style="font-size:0.85rem; font-weight:700; color:var(--cyan-accent); margin-bottom:10px; display:flex; justify-content:space-between; align-items:center;">
          <span>📋 Atividades Sumativas & Datas (Prazos Oficiais do PUC)</span>
          <button type="button" class="btn-subtle" style="font-size:0.75rem; padding:4px 8px;" onclick="addMilestoneEditorRow()">+ Adicionar Atividade Sumativa</button>
        </div>
        <p style="font-size:0.75rem; color:var(--text-dim); margin-bottom:12px;">
          Podes ajustar aqui os prazos de abertura e fecho caso o PUC não traga datas ou as mesmas tenham sido alteradas:
        </p>
        <div id="milestoneEditorRowsList"></div>
      </div>

      <div style="margin-top:20px; display:flex; justify-content:flex-end; gap:10px;">
        <button type="button" class="btn-subtle" onclick="closeUcModal()">Cancelar</button>
        <button type="submit" class="btn-emerald" id="saveCourseBtn">Guardar Unidade Curricular</button>
      </div>
    </form>
  </div>
</div>

<div class="modal-overlay" id="ucHistoryModalOverlay" onclick="closeUcHistoryModal(event)">
  <div class="modal-container" onclick="event.stopPropagation()">
    <div class="modal-header">
      <div>
        <span style="font-size:0.72rem; color:var(--cyan-accent); font-weight:800; text-transform:uppercase;" id="histModalTag">UC Concluída</span>
        <h3 class="modal-title" id="histModalTitle" style="margin-top:2px;">Detalhes da Unidade Curricular</h3>
      </div>
      <button class="modal-close-btn" onclick="closeUcHistoryModal()">✕</button>
    </div>
    <div id="histModalBody" style="display:flex; flex-direction:column; gap:16px;"></div>
    <div style="margin-top:20px; display:flex; justify-content:space-between; align-items:center; border-top:1px solid var(--border-color); padding-top:14px;">
      <button class="btn-edit" id="histModalEditBtn">✏️ Editar Detalhes / Parcelas</button>
      <button class="btn-subtle" onclick="closeUcHistoryModal()">Fechar</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="centralSettingsModalOverlay" onclick="closeCentralSettingsModal(event)">
  <div class="modal-container" style="max-width: 620px;" onclick="event.stopPropagation()">
    <div class="modal-header">
      <h3 class="modal-title">⚙️ Definições do Sistema</h3>
      <button class="modal-close-btn" onclick="closeCentralSettingsModal()">✕</button>
    </div>

    <div style="display:flex; flex-direction:column; gap:4px;">
      <div class="settings-section-card">
        <div class="settings-section-title">👤 Perfil do Estudante & Curso</div>
        <div class="form-grid" style="grid-template-columns: 1fr 1fr; gap: 10px;">
          <div class="field">
            <label>Nome do Aluno</label>
            <input type="text" id="modalSettingName" placeholder="Ex: João Marques" onchange="saveProfileSettings()">
          </div>
          <div class="field">
            <label>Licenciatura / Curso</label>
            <input type="text" id="modalSettingDegree" placeholder="Ex: Engenharia Informática" onchange="saveProfileSettings()">
          </div>
        </div>
      </div>

      <div class="settings-section-card">
        <div class="settings-section-title">🔔 Alertas Visuais & Notificações de Prazos</div>

        <div class="notif-setting-row">
          <div class="notif-label-group">
            <span class="notif-label-title">Motor de Notificações Ativo</span>
            <span class="notif-label-sub">Apresentar avisos no canto superior direito para eventos próximos</span>
          </div>
          <label class="ios-switch">
            <input type="checkbox" id="notifActiveToggle" onchange="toggleNotifActive(this.checked)">
            <span class="slider-toggle"></span>
          </label>
        </div>

        <div class="notif-setting-row">
          <div class="notif-label-group">
            <span class="notif-label-title">Janela de Antecedência</span>
            <span class="notif-label-sub">Com que antecedência pretende ser alertado sobre prazos?</span>
          </div>
          <select id="notifAdvanceSelect" onchange="changeNotifAdvance(this.value)" style="width:auto; min-width:140px; padding:6px 12px; font-weight:700; border-radius:8px;">
            <option value="1">1 dia antes</option>
            <option value="2">2 dias antes</option>
            <option value="3">3 dias antes</option>
            <option value="5">5 dias antes</option>
            <option value="7">7 dias antes</option>
          </select>
        </div>

        <div class="notif-setting-row">
          <div class="notif-label-group">
            <span class="notif-label-title">Notificações Silenciadas</span>
            <span class="notif-label-sub" id="mutedCountSubText">0 notificações silenciadas permanentemente</span>
          </div>
          <button type="button" class="btn-notif-action" onclick="resetMutedNotifications()">Repor Lista</button>
        </div>

        <div class="notif-setting-row">
          <div class="notif-label-group">
            <span class="notif-label-title">Disparar Alerta de Teste</span>
            <span class="notif-label-sub">Verifique o fundo opaco sólido e a visibilidade perfeita</span>
          </div>
          <button type="button" class="btn-notif-blue" onclick="triggerTestNotification()">Testar Alerta</button>
        </div>
      </div>

      <div class="settings-section-card">
        <div class="settings-section-title">🗄️ Armazenamento do data.json</div>
        <p style="font-size:0.78rem; color:var(--text-muted); margin-bottom:12px;">
          Escolhe onde reside o ficheiro central. Podes apontar para uma pasta sincronizada (Google Drive, OneDrive, Dropbox, Syncthing) para replicar entre dispositivos. A aplicação nunca sobrescreve ficheiros sem a tua confirmação explícita.
        </p>

        <div class="field" style="margin-bottom:10px;">
          <label>Caminho do data.json</label>
          <input type="text" id="storagePathInput" placeholder="Ex: data.json ou /Users/ti/Google Drive/MyUniStation/data.json" style="font-family:var(--font-mono); font-size:0.82rem;">
        </div>

        <div style="display:flex; gap:10px; flex-wrap:wrap;">
          <button type="button" class="btn-puc" onclick="previewStorageChange()">🔍 Pré-visualizar alteração</button>
          <button type="button" class="btn-subtle" onclick="resetStoragePath()">↺ Repor por defeito</button>
        </div>

        <div id="storageInfoBox" style="margin-top:14px; padding:12px; background:var(--bg-base); border:1px solid var(--border-color); border-radius:8px; font-size:0.78rem; color:var(--text-muted);">
          <div style="font-weight:700; color:var(--text-dim); font-size:0.68rem; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:6px;">Estado Atual</div>
          <div id="storageInfoContent">A carregar...</div>
        </div>
      </div>

      <div class="settings-section-card" style="margin-bottom:0;">
        <div class="settings-section-title">💾 Sincronização & Backup do data.json</div>
        <p style="font-size:0.78rem; color:var(--text-muted); margin-bottom:12px;">
          Descarrega o ficheiro central para salvaguarda ou importa para sincronizar com outro computador:
        </p>
        <div style="display:flex; gap:10px; flex-wrap:wrap;">
          <button type="button" class="btn-subtle" style="flex:1;" onclick="exportData()">📥 Descarregar Backup JSON</button>
          <button type="button" class="btn-subtle" style="flex:1;" onclick="document.getElementById('fileUpload').click()">📤 Restaurar / Importar JSON</button>
          <input type="file" id="fileUpload" accept=".json" style="display:none;" onchange="importData(event)">
        </div>
      </div>
    </div>
  </div>
</div>

<div class="modal-overlay" id="storageChangeModalOverlay" onclick="closeStorageChangeModal(event)">
  <div class="modal-container" onclick="event.stopPropagation()">
    <div class="modal-header">
      <h3 class="modal-title" id="storageChangeTitle">⚠️ Alteração de Armazenamento</h3>
      <button class="modal-close-btn" onclick="closeStorageChangeModal()">✕</button>
    </div>
    <div id="storageChangeModalBody"></div>
  </div>
</div>

<script>
/* =========================================================================
   CONSTANTES GLOBAIS
   ========================================================================= */
const TOTAL_DEGREE_ECTS      = 180;
const DEFAULT_ECTS           = 6;
const GRADE_MIN              = 0;
const GRADE_MAX              = 20;
const DEFAULT_THEME          = "dark";
const DEFAULT_NOTIFY_ADVANCE = 3;
const MAX_NOTIFY_ADVANCE     = 7;

const UC_COLORS_MAP = {
  "SR": "#2563eb",
  "POO": "#8b5cf6",
  "LC": "#059669",
  "FBD": "#d97706",
  "FG": "#dc2626"
};

const UC_COLORS_FALLBACK = ['#2563eb', '#8b5cf6', '#059669', '#d97706', '#dc2626', '#38bdf8', '#a855f7'];

const MONTH_NAMES = [
  "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
  "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
];

const SEMESTER_WEEKS = {
  0:  { label: "14/09 a 18/09", start: "2026-09-14", end: "2026-09-20" },
  1:  { label: "21/09 a 27/09", start: "2026-09-21", end: "2026-09-27" },
  2:  { label: "28/09 a 04/10", start: "2026-09-28", end: "2026-10-04" },
  3:  { label: "05/10 a 11/10", start: "2026-10-05", end: "2026-10-11" },
  4:  { label: "12/10 a 18/10", start: "2026-10-12", end: "2026-10-18" },
  5:  { label: "19/10 a 25/10", start: "2026-10-19", end: "2026-10-25" },
  6:  { label: "26/10 a 01/11", start: "2026-10-26", end: "2026-11-01" },
  7:  { label: "02/11 a 08/11", start: "2026-11-02", end: "2026-11-08" },
  8:  { label: "09/11 a 15/11", start: "2026-11-09", end: "2026-11-15" },
  9:  { label: "16/11 a 22/11", start: "2026-11-16", end: "2026-11-22" },
  10: { label: "23/11 a 29/11", start: "2026-11-23", end: "2026-11-29" },
  11: { label: "30/11 a 06/12", start: "2026-11-30", end: "2026-12-06" },
  12: { label: "07/12 a 13/12", start: "2026-12-07", end: "2026-12-13" },
  13: { label: "14/12 a 21/12", start: "2026-12-14", end: "2026-12-21" },
  14: { label: "04/01 a 10/01", start: "2027-01-04", end: "2027-01-10" },
  15: { label: "11/01 a 17/01", start: "2027-01-11", end: "2027-01-17" },
  16: { label: "18/01 a 25/01", start: "2027-01-18", end: "2027-01-25" }
};

/* =========================================================================
   ESTADO DA APLICAÇÃO
   ========================================================================= */
let appState = {
  schema_version: 1,
  profile: { name: "Aluno", degree: "Engenharia Informática" },
  theme: DEFAULT_THEME,
  courses: [],
  notificationsActive: true,
  notifyAdvanceDays: DEFAULT_NOTIFY_ADVANCE,
  mutedNotifications: [],
  sessionDismissed: [],
  calYear: 2026,
  calMonth: 8,
  ucCalendarFilters: {},
  expandedWeeks: {},
  completedDeliveries: {}
};

let selectedDossierUcIdx = 0;
let currentSemesterSubView = 'sub-calendario';
let deliveriesSortState = { field: 'date_due', direction: 'asc' };
let currentStorageInfo = { resolved_path: "", exists: false, valid_json: false, metadata: {} };

/* =========================================================================
   HELPERS GENÉRICOS
   ========================================================================= */
function escapeHtml(str) {
  return String(str == null ? '' : str).replace(/[&<>'"]/g, t => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[t] || t));
}

function pad2(n) { return String(n).padStart(2, '0'); }

function parseIsoDate(str) {
  if (!str) return null;
  const dStr = str.includes('T') ? str.split('T')[0] : str;
  const parts = dStr.split('-');
  if (parts.length === 3) {
    return new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10));
  }
  return new Date(str);
}

function formatDayMonth(str) {
  const dt = parseIsoDate(str);
  if (!dt) return '';
  return `${pad2(dt.getDate())}/${pad2(dt.getMonth() + 1)}`;
}

function formatFullDate(str) {
  const dt = parseIsoDate(str);
  if (!dt) return '';
  return `${pad2(dt.getDate())}/${pad2(dt.getMonth() + 1)}/${dt.getFullYear()}`;
}

function formatBytes(n) {
  if (!n) return '0 B';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(2) + ' MB';
}

function formatDateTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return `${pad2(d.getDate())}/${pad2(d.getMonth()+1)}/${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  } catch (_) {
    return iso;
  }
}

function parseIntSafe(value, fallback) {
  const n = parseInt(value, 10);
  return Number.isFinite(n) ? n : fallback;
}

/* =========================================================================
   TEMA
   ========================================================================= */
function updateThemeIcon(theme) {
  const icon = theme === 'dark' ? '☀️' : '🌙';
  const btnDesktop = document.getElementById('themeToggleBtn');
  const btnMobile  = document.getElementById('themeToggleBtnMobile');
  if (btnDesktop) btnDesktop.innerText = icon;
  if (btnMobile)  btnMobile.innerText  = icon;
}

function toggleTheme() {
  const currentTheme = document.documentElement.getAttribute('data-theme') || DEFAULT_THEME;
  const nextTheme = currentTheme === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', nextTheme);
  appState.theme = nextTheme;
  updateThemeIcon(nextTheme);
  saveServerData();
}

/* =========================================================================
   UC HELPERS
   ========================================================================= */
function getUcShortCode(course) {
  if (course.shortCode) return course.shortCode;
  const name = (course.name || '').toLowerCase();
  if (name.includes("rede")) return "SR";
  if (name.includes("objeto") || name.includes("objecto")) return "POO";
  if (name.includes("linguagen") || name.includes("computação") || name.includes("computacao")) return "LC";
  if (name.includes("base") || name.includes("dados")) return "FBD";
  if (name.includes("física") || name.includes("fisica")) return "FG";
  return course.code ? course.code.toString()
    : (course.name ? course.name.substring(0, 3).toUpperCase() : "UC");
}

function getUcColor(course, idx = 0) {
  const code = getUcShortCode(course);
  return UC_COLORS_MAP[code] || UC_COLORS_FALLBACK[idx % UC_COLORS_FALLBACK.length];
}

function getScopedMilestoneId(courseName, m) {
  const rawId = m.id || m.name;
  return `${courseName}__${rawId}`.replace(/[^a-zA-Z0-9_-]/g, '_');
}

/* =========================================================================
   NAVEGAÇÃO
   ========================================================================= */
function syncBottomNav() {
  const bottomNav = document.getElementById('bottomNav');
  if (!bottomNav) return;
  const isHome = document.getElementById('tab-inicio').classList.contains('active');

  bottomNav.querySelectorAll('.bottom-btn').forEach(btn => {
    const tab = btn.dataset.tab;
    const isActive = isHome ? (tab === 'tab-inicio') : (tab === currentSemesterSubView);
    btn.classList.toggle('active', isActive);
  });
}

function navigate(tabId) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active'));

  const target = document.getElementById(tabId);
  if (target) target.classList.add('active');

  document.querySelectorAll('.nav-btn').forEach(b => {
    const oc = b.getAttribute('onclick');
    if (oc && oc.includes(tabId)) b.classList.add('active');
  });

  if (tabId !== 'tab-semestre') {
    document.getElementById('btnSemestreParent').classList.remove('active');
    document.querySelectorAll('.subnav-btn-side').forEach(b => b.classList.remove('active'));
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
  syncBottomNav();
}

function toggleSemesterTree(e) {
  if (e) e.stopPropagation();
  const tree = document.getElementById('semesterSubNavTree');
  const chevron = document.getElementById('semestreChevron');

  if (tree.style.display === 'none') {
    tree.style.display = 'flex';
    chevron.innerText = '▼';
    openSemesterSubView(currentSemesterSubView);
  } else {
    tree.style.display = 'none';
    chevron.innerText = '▶';
  }
}

function openSemesterSubView(subId) {
  currentSemesterSubView = subId;

  document.getElementById('semesterSubNavTree').style.display = 'flex';
  document.getElementById('semestreChevron').innerText = '▼';

  navigate('tab-semestre');

  document.getElementById('btnSemestreParent').classList.add('active');
  document.querySelectorAll('.subnav-btn-side').forEach(b => b.classList.remove('active'));

  const sideSubBtn = document.getElementById(`sideSubBtn-${subId.replace('sub-', '')}`);
  if (sideSubBtn) sideSubBtn.classList.add('active');

  document.querySelectorAll('.subview-section').forEach(el => el.classList.remove('active'));
  const targetSub = document.getElementById(subId);
  if (targetSub) targetSub.classList.add('active');

  const titles = {
    'sub-calendario': '🚀 Semestre em Curso • Calendário Geral',
    'sub-roteiro':    '🚀 Semestre em Curso • Roteiro Semanal Compilado',
    'sub-entregas':   '🚀 Semestre em Curso • Atividades Sumativas',
    'sub-dossie':     '🚀 Semestre em Curso • PUC das UC'
  };
  document.getElementById('semesterViewHeaderTitle').innerText = titles[subId] || '🚀 Semestre em Curso';

  if (subId === 'sub-calendario') renderCalendar();
  if (subId === 'sub-entregas')   renderConsolidatedDeliveriesTable();

  syncBottomNav();
}

function navigateToDeliveriesWithTarget(courseName, targetDelivId) {
  openSemesterSubView('sub-entregas');
  const filterSelect = document.getElementById('delivFilterUcSelect');
  if (filterSelect) {
    filterSelect.value = courseName;
    renderConsolidatedDeliveriesTable();
  }

  setTimeout(() => {
    const targetRow = document.getElementById(`deliv_row_${targetDelivId}`);
    if (targetRow) {
      targetRow.classList.add('highlight-target');
      targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => targetRow.classList.remove('highlight-target'), 2500);
    }
  }, 100);
}

/* =========================================================================
   MODAIS — UC
   ========================================================================= */
function openUcModal(mode = 'manual') {
  document.getElementById('ucModalOverlay').classList.add('active');
  selectEntryMode(mode);
}

function closeUcModal(e) {
  if (e && e.target !== e.currentTarget && e.target.className !== 'modal-close-btn') return;
  document.getElementById('ucModalOverlay').classList.remove('active');
  cancelEditCourse();
}

function selectEntryMode(mode) {
  const isPuc = mode === 'puc';
  document.getElementById('cardModeManual').classList.toggle('selected', !isPuc);
  document.getElementById('cardModePuc').classList.toggle('selected', isPuc);
  document.getElementById('pucUploadSection').style.display = isPuc ? 'block' : 'none';
  document.getElementById('courseForm').style.display = isPuc ? 'none' : 'block';
}

function toggleGradeFieldVisibility() {
  const isCompleted = document.getElementById('cStatus').value === 'completed';
  document.getElementById('finalGradeFieldContainer').style.display = isCompleted ? 'flex' : 'none';
}

/* =========================================================================
   DEFINIÇÕES CENTRAIS
   ========================================================================= */
function openCentralSettingsModal() {
  document.getElementById('modalSettingName').value = appState.profile.name || "Aluno";
  document.getElementById('modalSettingDegree').value = appState.profile.degree || "Engenharia Informática";
  document.getElementById('notifActiveToggle').checked = !!appState.notificationsActive;
  document.getElementById('notifAdvanceSelect').value = appState.notifyAdvanceDays || DEFAULT_NOTIFY_ADVANCE;
  const count = (appState.mutedNotifications || []).length;
  document.getElementById('mutedCountSubText').innerText =
    `${count} ${count === 1 ? 'notificação silenciada permanentemente' : 'notificações silenciadas permanentemente'}`;

  document.getElementById('storagePathInput').value = currentStorageInfo.resolved_path || '';
  renderStorageInfoBox();

  refreshStorageInfo();

  document.getElementById('centralSettingsModalOverlay').classList.add('active');
}

function closeCentralSettingsModal(e) {
  if (e && e.target !== e.currentTarget && e.target.className !== 'modal-close-btn') return;
  document.getElementById('centralSettingsModalOverlay').classList.remove('active');
}

async function saveProfileSettings() {
  appState.profile.name = document.getElementById('modalSettingName').value.trim() || 'Aluno';
  appState.profile.degree = document.getElementById('modalSettingDegree').value.trim() || 'Engenharia Informática';
  render();
  await saveServerData();
}

async function toggleNotifActive(val) {
  appState.notificationsActive = !!val;
  await saveServerData();
  checkToastDeadlines();
}

async function changeNotifAdvance(val) {
  const parsed = parseIntSafe(val, DEFAULT_NOTIFY_ADVANCE);
  appState.notifyAdvanceDays = Math.min(Math.max(parsed, 1), MAX_NOTIFY_ADVANCE);
  await saveServerData();
  checkToastDeadlines();
}

async function resetMutedNotifications() {
  appState.mutedNotifications = [];
  document.getElementById('mutedCountSubText').innerText = '0 notificações silenciadas permanentemente';
  await saveServerData();
  checkToastDeadlines();
  alert('Lista de notificações silenciadas reposta com sucesso!');
}

function triggerTestNotification() {
  showToast("Sistemas em Rede", {
    name: "Entrega de Teste • Validação Visual",
    date_due: "2026-10-25 23:59",
    weight: 3.0
  }, 2, "test_notification_key");
}

/* =========================================================================
   ARMAZENAMENTO — data.json configurável
   ========================================================================= */
function renderStorageInfoBox() {
  const box = document.getElementById('storageInfoContent');
  if (!box) return;

  const info = currentStorageInfo;
  if (!info || !info.resolved_path) {
    box.innerHTML = '<span style="color:var(--text-dim);">Informação indisponível.</span>';
    return;
  }

  const meta = info.metadata || {};
  const rows = [];
  rows.push(`<div class="storage-compare-row"><span>Caminho</span><b style="font-size:0.72rem;">${escapeHtml(info.resolved_path)}</b></div>`);

  if (!info.exists) {
    rows.push(`<div class="storage-compare-row"><span>Estado</span><b style="color:var(--text-dim);">Ainda não criado</b></div>`);
    rows.push(`<div class="storage-compare-row"><span>Nota</span><b style="font-size:0.72rem;">Será criado na primeira gravação</b></div>`);
  } else if (!info.valid_json) {
    rows.push(`<div class="storage-compare-row"><span>Estado</span><b style="color:var(--rose-accent);">Corrompido</b></div>`);
    if (meta.error) rows.push(`<div class="storage-compare-row"><span>Erro</span><b style="font-size:0.72rem;">${escapeHtml(meta.error)}</b></div>`);
  } else {
    rows.push(`<div class="storage-compare-row"><span>Estado</span><b style="color:var(--emerald-accent);">Acessível</b></div>`);
    rows.push(`<div class="storage-compare-row"><span>Tamanho</span><b>${formatBytes(meta.size_bytes || 0)}</b></div>`);
    rows.push(`<div class="storage-compare-row"><span>Modificado</span><b style="font-size:0.72rem;">${escapeHtml(formatDateTime(meta.modified))}</b></div>`);
    rows.push(`<div class="storage-compare-row"><span>UCs</span><b>${meta.courses_total || 0} (${meta.courses_active || 0} ativas, ${meta.courses_completed || 0} concluídas)</b></div>`);
  }
  box.innerHTML = rows.join('');
}

async function refreshStorageInfo() {
  try {
    const res = await fetch('/api/config');
    if (!res.ok) throw new Error('fetch falhou');
    const cfg = await res.json();
    currentStorageInfo = cfg;
    const input = document.getElementById('storagePathInput');
    if (input) input.value = cfg.resolved_path || '';
    renderStorageInfoBox();
  } catch (e) {
    const box = document.getElementById('storageInfoContent');
    if (box) box.innerHTML = '<span style="color:var(--rose-accent);">Falha a contactar o servidor.</span>';
  }
}

function resetStoragePath() {
  document.getElementById('storagePathInput').value = 'data.json';
}

async function previewStorageChange() {
  const newPath = document.getElementById('storagePathInput').value.trim();
  if (!newPath) {
    alert('⚠️ Indica um caminho antes de continuar.');
    return;
  }

  let preview;
  try {
    const res = await fetch('/api/config/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_path: newPath })
    });
    preview = await res.json();
  } catch (e) {
    alert('❌ Falha a contactar o servidor: ' + e.message);
    return;
  }

  if (preview.status === 'noop') {
    alert('ℹ️ ' + (preview.message || 'Já estás a usar este ficheiro.'));
    return;
  }
  if (preview.status === 'error') {
    alert('⚠️ ' + (preview.message || 'Erro ao validar o caminho.'));
    return;
  }

  renderStorageChangeModal(preview);
}

function renderStorageChangeModal(preview) {
  const body = document.getElementById('storageChangeModalBody');
  const title = document.getElementById('storageChangeTitle');

  const currentMeta = preview.current_meta || {};
  const destMeta = preview.destination_meta || {};
  const destExists = !!preview.destination_exists;

  const renderCol = (label, meta, path) => {
    if (!meta.exists) {
      return `
        <div class="storage-compare-col">
          <div class="storage-compare-title">${escapeHtml(label)}</div>
          <div class="storage-compare-row"><span>Caminho</span><b style="font-size:0.68rem;">${escapeHtml(path)}</b></div>
          <div class="storage-compare-row"><span>Ficheiro</span><b style="color:var(--text-dim);">Não existe</b></div>
        </div>`;
    }
    if (meta.valid_json === false) {
      return `
        <div class="storage-compare-col">
          <div class="storage-compare-title">${escapeHtml(label)}</div>
          <div class="storage-compare-row"><span>Caminho</span><b style="font-size:0.68rem;">${escapeHtml(path)}</b></div>
          <div class="storage-compare-row"><span>Estado</span><b style="color:var(--rose-accent);">JSON corrompido</b></div>
          <div class="storage-compare-row"><span>Erro</span><b style="font-size:0.68rem;">${escapeHtml(meta.error || '')}</b></div>
        </div>`;
    }
    return `
      <div class="storage-compare-col">
        <div class="storage-compare-title">${escapeHtml(label)}</div>
        <div class="storage-compare-row"><span>Caminho</span><b style="font-size:0.68rem;">${escapeHtml(path)}</b></div>
        <div class="storage-compare-row"><span>Tamanho</span><b>${formatBytes(meta.size_bytes || 0)}</b></div>
        <div class="storage-compare-row"><span>Modificado</span><b style="font-size:0.68rem;">${escapeHtml(formatDateTime(meta.modified))}</b></div>
        <div class="storage-compare-row"><span>UCs totais</span><b>${meta.courses_total || 0}</b></div>
        <div class="storage-compare-row"><span>Ativas</span><b>${meta.courses_active || 0}</b></div>
        <div class="storage-compare-row"><span>Concluídas</span><b>${meta.courses_completed || 0}</b></div>
        ${meta.last_updated_course ? `<div class="storage-compare-row"><span>Última UC</span><b style="font-size:0.68rem;">${escapeHtml(meta.last_updated_course)}</b></div>` : ''}
      </div>`;
  };

  const currentCol = renderCol('💻 Ficheiro atual (local em uso)', currentMeta, preview.current_path || '');
  const destCol    = renderCol('📄 Ficheiro de destino', destMeta, preview.new_path || '');

  let introHtml = '';
  let actionsHtml = '';

  if (!destExists) {
    title.innerText = '📁 Novo Armazenamento (destino vazio)';
    introHtml = `<p style="font-size:0.86rem; color:var(--text-muted);">O caminho de destino ainda não contém ficheiro. Como queres proceder?</p>`;
    actionsHtml = `
      <button type="button" class="btn-emerald" onclick="applyStorageChange('migrate_to_empty')">📦 Migrar os meus dados para lá</button>
      <button type="button" class="btn-subtle" onclick="applyStorageChange('start_fresh')">🆕 Começar do zero (estado padrão)</button>
      <button type="button" class="btn-subtle" onclick="closeStorageChangeModal()">❌ Cancelar</button>
    `;
  } else if (destMeta.valid_json === false) {
    title.innerText = '⚠️ Destino com JSON inválido';
    introHtml = `<p style="font-size:0.86rem; color:var(--text-muted);">O ficheiro de destino existe mas não é um JSON válido. Podes substituí-lo pelos teus dados atuais (será preservado como <code>.backup-*</code>).</p>`;
    actionsHtml = `
      <button type="button" class="btn-emerald" onclick="applyStorageChange('use_local')">➡️ Usar o meu local (substituir o destino)</button>
      <button type="button" class="btn-subtle" onclick="closeStorageChangeModal()">❌ Cancelar</button>
    `;
  } else {
    title.innerText = '⚠️ Ambos os ficheiros contêm dados';
    introHtml = `
      <p style="font-size:0.86rem; color:var(--text-muted);">
        Ambos os ficheiros contêm um <code>data.json</code> válido. Nenhum será apagado — o ficheiro substituído será renomeado para <code>.backup-&lt;timestamp&gt;</code> antes de qualquer alteração.
      </p>
    `;
    actionsHtml = `
      <button type="button" class="btn-emerald" onclick="applyStorageChange('use_local')">➡️ Usar o meu local (substituir o destino)</button>
      <button type="button" class="btn-puc" onclick="applyStorageChange('use_remote')">⬅️ Usar o remoto (substituir o meu local)</button>
      <button type="button" class="btn-subtle" onclick="closeStorageChangeModal()">❌ Cancelar</button>
    `;
  }

  body.innerHTML = `
    ${introHtml}
    <div class="storage-compare-grid">
      ${currentCol}
      ${destCol}
    </div>
    <div class="storage-action-stack">
      ${actionsHtml}
    </div>
  `;

  document.getElementById('storageChangeModalOverlay').classList.add('active');
}

function closeStorageChangeModal(e) {
  if (e && e.target !== e.currentTarget && e.target.className !== 'modal-close-btn') return;
  document.getElementById('storageChangeModalOverlay').classList.remove('active');
}

async function applyStorageChange(action) {
  const newPath = document.getElementById('storagePathInput').value.trim();
  if (!newPath) { alert('⚠️ Caminho vazio.'); return; }

  setSyncBadge('🟡 A aplicar...', 'var(--amber-accent)');

  let result;
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ new_path: newPath, action: action })
    });
    result = await res.json();
  } catch (e) {
    setSyncBadge('🔴 Erro de rede', 'var(--rose-accent)');
    alert('❌ Falha a contactar o servidor: ' + e.message);
    return;
  }

  if (result.status === 'noop') {
    closeStorageChangeModal();
    setSyncBadge('🟢 Sincronizado', 'var(--emerald-accent)');
    alert('ℹ️ ' + (result.message || 'Sem alterações a aplicar.'));
    return;
  }

  if (result.status !== 'ok') {
    setSyncBadge('🔴 Falha', 'var(--rose-accent)');
    alert('⚠️ ' + (result.message || 'Operação falhou.'));
    return;
  }

  closeStorageChangeModal();
  await loadServerData();
  setSyncBadge('🟢 Sincronizado', 'var(--emerald-accent)');
  alert('✅ Configuração de armazenamento atualizada com sucesso.');
}

/* =========================================================================
   EDITOR DE ATIVIDADES SUMATIVAS
   ========================================================================= */
function addMilestoneEditorRow(m = {}) {
  const container = document.getElementById('milestoneEditorRowsList');
  const card = document.createElement('div');
  card.className = 'milestone-editor-card';

  const startVal = m.date_start ? (m.date_start.includes('T') ? m.date_start.split('T')[0] : m.date_start) : '';
  let dueVal = '';
  if (m.date_due) {
    dueVal = m.date_due.length === 10 ? `${m.date_due}T23:59` : m.date_due;
  }

  card.dataset.id = m.id || ('deliv_' + Date.now() + '_' + Math.random().toString(36).slice(2, 5));
  card.dataset.type = m.type || 'efolio';
  card.dataset.desc = m.description || '';

  card.innerHTML = `
    <div class="milestone-editor-row">
      <input type="text" class="m-name-input" placeholder="Designação (ex: Atividade Sumativa 1)" value="${escapeHtml(m.name || '')}" style="font-weight:700;">
      <input type="number" class="m-weight-input" min="0" max="20" step="0.1" placeholder="Cotação (v)" value="${m.weight !== undefined ? m.weight : 3.0}">
      <button type="button" class="btn-danger" onclick="this.closest('.milestone-editor-card').remove()" title="Remover atividade">✕</button>
    </div>
    <div class="milestone-editor-dates">
      <div>
        <label>📅 Início / Disponibilização:</label>
        <input type="date" class="m-start-input" value="${startVal}">
      </div>
      <div>
        <label>🏁 Data & Hora Limite:</label>
        <input type="datetime-local" class="m-due-input" value="${dueVal}">
      </div>
    </div>
  `;
  container.appendChild(card);
}

async function quickEditMilestoneDates(courseName, milestoneId) {
  const c = appState.courses.find(x => x.name === courseName);
  if (!c || !c.milestones) return;
  const m = c.milestones.find(x => (x.id || x.name) === milestoneId);
  if (!m) return;

  const currentStart = m.date_start ? m.date_start.split('T')[0] : 'AAAA-MM-DD';
  const currentDue = m.date_due || 'AAAA-MM-DDTHH:MM';

  const newStart = prompt(`📅 Data de início/disponibilização para "${m.name}":\\n(Formato: AAAA-MM-DD)`, currentStart === 'AAAA-MM-DD' ? '' : currentStart);
  if (newStart === null) return;

  const newDue = prompt(`🏁 Data e hora limite de entrega para "${m.name}":\\n(Ex: 2026-11-16T23:55 ou 2026-11-16)`, currentDue === 'AAAA-MM-DDTHH:MM' ? '' : currentDue);
  if (newDue === null) return;

  m.date_start = newStart.trim() || null;
  m.date_due = newDue.trim() || null;
  c.updatedAt = new Date().toISOString();

  render();
  renderCalendar();
  await saveServerData();
  alert(`Datas de "${m.name}" atualizadas com sucesso!`);
}

/* =========================================================================
   PERSISTÊNCIA (SERVIDOR)
   ========================================================================= */
async function loadServerData() {
  try {
    const res = await fetch('/api/data');
    const data = await res.json();
    if (data && typeof data === 'object') {
      appState = {
        ...appState,
        schema_version: data.schema_version || 1,
        profile: data.profile || appState.profile,
        theme: data.theme || DEFAULT_THEME,
        courses: Array.isArray(data.courses) ? data.courses : [],
        notificationsActive: data.notificationsActive !== false,
        notifyAdvanceDays: parseIntSafe(data.notifyAdvanceDays, DEFAULT_NOTIFY_ADVANCE),
        mutedNotifications: data.mutedNotifications || [],
        expandedWeeks: data.expandedWeeks || {},
        completedDeliveries: data.completedDeliveries || {}
      };
      document.documentElement.setAttribute('data-theme', appState.theme);
      updateThemeIcon(appState.theme);
    }

    const now = new Date();
    appState.calYear = now.getFullYear();
    appState.calMonth = now.getMonth();

    setSyncBadge('🟢 Sincronizado', 'var(--emerald-accent)');
    render();
    checkToastDeadlines();

    await refreshStorageInfo();
  } catch (e) {
    setSyncBadge('🔴 Servidor Indisponível', 'var(--rose-accent)');
  }
}

async function saveServerData() {
  setSyncBadge('🟡 A guardar...', 'var(--amber-accent)');
  try {
    const res = await fetch('/api/data', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(appState)
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ message: 'Erro desconhecido' }));
      setSyncBadge('🔴 Erro de validação', 'var(--rose-accent)');
      console.error('POST /api/data falhou:', err);
      return;
    }
    setSyncBadge('🟢 Sincronizado', 'var(--emerald-accent)');
  } catch (e) {
    setSyncBadge('🔴 Erro de Escrita', 'var(--rose-accent)');
  }
}

function setSyncBadge(txt, col) {
  const el = document.getElementById('syncBadge');
  el.innerText = txt;
  el.style.color = col;
  el.style.borderColor = col;
}

/* =========================================================================
   RENDER PRINCIPAL
   ========================================================================= */
function render() {
  document.getElementById('userDegreeDisplay').innerText = `${appState.profile.name} • ${appState.profile.degree}`;

  let totalEcts = 0, sumGrades = 0, ectsAvg = 0, actCount = 0, doneCount = 0;
  const activeCards = document.getElementById('activeCardsContainer');
  const sideCourseList = document.getElementById('sideCourseList');

  activeCards.innerHTML = '';
  sideCourseList.innerHTML = '';

  let totalTasksGlobal = 0;
  let completedTasksGlobal = 0;
  const activeCourses = [];

  appState.courses.forEach((c, i) => {
    const ucCode = getUcShortCode(c);
    const ucColor = getUcColor(c, i);

    if (appState.ucCalendarFilters[c.name] === undefined) {
      appState.ucCalendarFilters[c.name] = true;
    }

    const isDone = c.isCompleted === true;

    let finalGrade = null;
    if (c.finalGrade !== null && c.finalGrade !== undefined && c.finalGrade !== '') {
      finalGrade = parseFloat(c.finalGrade).toFixed(1);
    } else if (c.model === 'uab_standard') {
      if (c.gfolio !== null && c.gfolio !== '' && c.gfolio !== undefined) {
        finalGrade = (parseFloat(c.efolioA || 0) + parseFloat(c.efolioB || 0) + parseFloat(c.gfolio)).toFixed(1);
      }
    } else {
      if (c.directGrade !== null && c.directGrade !== '' && c.directGrade !== undefined) {
        finalGrade = parseFloat(c.directGrade).toFixed(1);
      }
    }

    if (isDone) {
      totalEcts += parseIntSafe(c.ects, DEFAULT_ECTS);
      if (finalGrade !== null) {
        sumGrades += parseFloat(finalGrade) * parseIntSafe(c.ects, DEFAULT_ECTS);
        ectsAvg += parseIntSafe(c.ects, DEFAULT_ECTS);
      }
      doneCount++;
    } else {
      actCount++;
      activeCourses.push({ course: c, index: i, color: ucColor, code: ucCode });

      const weeks = c.weekly_roadmap || [];
      const milestones = c.milestones || [];

      const studyWeeksCount = weeks.length;
      const studyWeeksDone = weeks.filter(w => w.completed).length;

      const milestonesCount = milestones.filter(m => m.date_due || m.weight > 0).length;
      let milestonesDone = 0;
      milestones.forEach(m => {
        const delivId = getScopedMilestoneId(c.name, m);
        if (appState.completedDeliveries[delivId]) milestonesDone++;
      });

      const totalUcActions = studyWeeksCount + milestonesCount;
      const totalUcDone = studyWeeksDone + milestonesDone;
      const cPct = totalUcActions > 0 ? Math.round((totalUcDone / totalUcActions) * 100) : 0;

      totalTasksGlobal += totalUcActions;
      completedTasksGlobal += totalUcDone;

      const sItem = document.createElement('div');
      sItem.className = 'course-prog-item';
      sItem.innerHTML = `
        <div class="course-prog-meta">
          <span><span class="course-dot" style="background:${ucColor};"></span>${escapeHtml(c.name)}</span>
          <b>${cPct}%</b>
        </div>
        <div class="bar-bg">
          <div class="bar-fill" style="width:${cPct}%; background:${ucColor};"></div>
        </div>
      `;
      sideCourseList.appendChild(sItem);

      const ac = document.createElement('div');
      ac.className = 'active-course-card';
      ac.style.borderLeftColor = ucColor;
      ac.innerHTML = `
        <div>
          <div class="active-course-top">
            <span style="font-size:0.75rem; color:${ucColor}; font-weight:800; text-transform:uppercase; letter-spacing:0.04em;">
              ${escapeHtml(ucCode)} • ANO ${c.year} • SEMESTRE ${c.sem}
            </span>
            <span class="tag tag-active" style="border-color:${ucColor}40; color:${ucColor}; background:${ucColor}15;">${c.ects} ECTS</span>
          </div>
          <h4 class="active-course-title">${escapeHtml(c.name)}</h4>
          <div class="bar-bg"><div class="bar-fill" style="width:${cPct}%; background:${ucColor};"></div></div>
          <div style="display:flex; justify-content:space-between; margin-top:10px; align-items:center;">
            <small style="font-size:0.75rem; color:var(--text-dim); font-weight:500;">${totalUcDone}/${totalUcActions} tarefas & entregas cumpridas</small>
            <span style="font-size:0.8rem; font-family:var(--font-mono); font-weight:800; color:${ucColor};">${cPct}%</span>
          </div>
        </div>
        <div style="display:flex; justify-content:space-between; align-items:center; margin-top:18px; padding-top:12px; border-top:1px solid var(--border-color);">
          <div style="display:flex; gap:6px;">
            <button class="btn-edit" onclick="startEditCourse(${i})">Editar</button>
            <button class="btn-danger" onclick="deleteCourse(${i})">Remover</button>
          </div>
          <button class="btn-subtle" style="padding:4px 10px; font-size:0.75rem; font-weight:600;" onclick="openSemesterSubView('sub-calendario')">Ver no Calendário 📅</button>
        </div>
      `;
      activeCards.appendChild(ac);
    }
  });

  if (actCount === 0) {
    activeCards.innerHTML = '<div style="grid-column:1/-1; color:var(--text-dim); font-size:0.88rem; padding:28px; background:var(--bg-surface); border:1px solid var(--border-color); border-radius:10px; text-align:center;">Não existem Unidades Curriculares em frequência neste semestre. Podes registar uma UC manualmente ou importar o ficheiro PUC (JSON).</div>';
  }

  const globalPct = totalTasksGlobal > 0 ? Math.round((completedTasksGlobal / totalTasksGlobal) * 100) : 0;
  document.getElementById('sideGlobalPct').innerText = `${globalPct}%`;
  document.getElementById('sideGlobalBar').style.width = `${globalPct}%`;
  document.getElementById('sideGlobalCounts').innerText = `${completedTasksGlobal}/${totalTasksGlobal} concluídas`;

  document.getElementById('statsEcts').innerText = `${totalEcts} / ${TOTAL_DEGREE_ECTS} ECTS`;
  document.getElementById('statsProgressBar').style.width = `${Math.min(100, (totalEcts / TOTAL_DEGREE_ECTS) * 100)}%`;
  document.getElementById('statsGpa').innerText = ectsAvg > 0 ? (sumGrades / ectsAvg).toFixed(2) + ' val' : '—';
  document.getElementById('statsActive').innerText = actCount;
  document.getElementById('statsDone').innerText = doneCount;

  renderDoneUcTable();
  renderCalendarToggles(activeCourses);
  renderCalendar();
  renderUnifiedWeeklyRoadmap(activeCourses);
  renderConsolidatedDeliveriesTable();
  renderDossierSubView(activeCourses);
}

/* =========================================================================
   TABELA DE UCs CONCLUÍDAS
   ========================================================================= */
function renderDoneUcTable() {
  const tbody = document.getElementById('doneCoursesTableBody');
  if (!tbody) return;
  tbody.innerHTML = '';

  const filterYear = document.getElementById('filterYearSelect').value;
  const filterSem = document.getElementById('filterSemSelect').value;

  const filtered = appState.courses
    .map((c, originalIdx) => ({ c, originalIdx }))
    .filter(({ c }) => {
      const isDone = c.isCompleted === true;
      const matchesYear = (filterYear === 'all' || c.year.toString() === filterYear);
      const matchesSem = (filterSem === 'all' || c.sem.toString() === filterSem);
      return isDone && matchesYear && matchesSem;
    });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-dim); padding:24px;">Nenhuma Unidade Curricular concluída com os filtros selecionados.</td></tr>`;
    return;
  }

  filtered.forEach(({ c, originalIdx }) => {
    let finalGradeStr = '—';
    if (c.finalGrade !== null && c.finalGrade !== undefined && c.finalGrade !== '') {
      finalGradeStr = parseFloat(c.finalGrade).toFixed(1) + ' val';
    }

    const hasPucData = (c.weekly_roadmap && c.weekly_roadmap.length > 0) || !!c.puc_raw_sections;

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>
        <span class="clickable-uc-title" title="Clica para ver a discriminação dos trabalhos" onclick="openUcHistoryDetailsModal(${originalIdx})">
          ${escapeHtml(c.name)} <span style="font-size:0.75rem; color:var(--cyan-accent);">↗</span>
        </span>
      </td>
      <td><span class="tag" style="background:var(--bg-card-subtle); color:var(--text-muted);">${escapeHtml(c.academicYear || '—')}</span></td>
      <td>Ano ${c.year} • Sem ${c.sem}</td>
      <td>${c.ects} ECTS</td>
      <td>${c.model === 'uab_standard' ? 'Regime Geral UAb' : 'Flexível / PUC'}</td>
      <td><b style="color:var(--emerald-accent); font-family:var(--font-mono);">${finalGradeStr}</b></td>
      <td><span class="tag tag-passed">Concluída</span></td>
      <td style="text-align:right;">
        <div style="display:inline-flex; gap:6px; align-items:center;">
          ${hasPucData
            ? `<button class="btn-clean-puc" onclick="cleanCoursePuc(${originalIdx})">Limpar PUC</button>`
            : `<span style="font-size:0.72rem; color:var(--text-dim); padding:2px 6px;">✓ PUC Arquivado</span>`}
          <button class="btn-subtle" style="font-size:0.75rem; padding:4px 8px;" onclick="reopenCourse(${originalIdx})">Reabrir</button>
          <button class="btn-danger" onclick="deleteCourse(${originalIdx})">Remover</button>
        </div>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function openUcHistoryDetailsModal(idx) {
  const c = appState.courses[idx];
  if (!c) return;

  document.getElementById('histModalTag').innerText = `Ano ${c.year} • Semestre ${c.sem} • ${c.academicYear || '—'} • ${c.ects} ECTS`;
  document.getElementById('histModalTitle').innerText = `${c.name} — Histórico de Avaliações`;

  const modalBody = document.getElementById('histModalBody');
  modalBody.innerHTML = '';

  let breakdownRows = '';
  let hasMilestones = false;

  if (c.milestones && Array.isArray(c.milestones) && c.milestones.length > 0) {
    hasMilestones = true;
    c.milestones.forEach(m => {
      const score = (m.grade !== null && m.grade !== undefined && m.grade !== '')
        ? parseFloat(m.grade) : null;
      breakdownRows += `
        <tr>
          <td><strong>${escapeHtml(m.name)}</strong>${m.description ? `<br><small style="color:var(--text-dim);">${escapeHtml(m.description)}</small>` : ''}</td>
          <td><span class="tag tag-active">${m.weight ? m.weight + 'v' : '—'}</span></td>
          <td style="font-family:var(--font-mono); font-weight:700; color:${score !== null ? 'var(--emerald-accent)' : 'var(--text-dim)'};">
            ${score !== null ? score.toFixed(2) + ' val' : 'Não registado'}
          </td>
        </tr>
      `;
    });
  }

  modalBody.innerHTML = `
    <div style="background:var(--bg-base); border:1px solid var(--border-color); border-radius:8px; padding:16px; display:flex; justify-content:space-between; align-items:center;">
      <div>
        <small style="color:var(--text-dim); font-size:0.75rem; text-transform:uppercase; font-weight:700;">Classificação Oficial Final</small>
        <div style="font-size:1.6rem; font-weight:800; font-family:var(--font-mono); color:var(--emerald-accent);">
          ${c.finalGrade !== null ? parseFloat(c.finalGrade).toFixed(1) + ' valores' : '—'}
        </div>
      </div>
      <div style="text-align:right;">
        <span class="tag tag-passed" style="font-size:0.8rem;">Aprovada / Creditada</span>
      </div>
    </div>
    <div style="margin-top:14px;">
      <h4 style="font-size:0.85rem; color:var(--text-main); font-weight:700; margin-bottom:8px;">Discriminação dos Momentos de Avaliação Anteriores:</h4>
      ${hasMilestones ? `
        <div class="table-container" style="border:1px solid var(--border-color); border-radius:8px;">
          <table>
            <thead><tr><th>Atividade / Prova</th><th>Cotação Máxima</th><th>Nota Obtida</th></tr></thead>
            <tbody>${breakdownRows}</tbody>
          </table>
        </div>
      ` : '<div style="padding:16px; background:var(--bg-card); border-radius:8px; color:var(--text-dim); text-align:center;">Sem parcelas discriminadas.</div>'}
    </div>
  `;

  document.getElementById('histModalEditBtn').onclick = () => {
    closeUcHistoryModal();
    startEditCourse(idx);
  };

  document.getElementById('ucHistoryModalOverlay').classList.add('active');
}

function closeUcHistoryModal(e) {
  if (e && e.target !== e.currentTarget && e.target.className !== 'modal-close-btn') return;
  document.getElementById('ucHistoryModalOverlay').classList.remove('active');
}

async function cleanCoursePuc(idx) {
  const c = appState.courses[idx];
  if (!c) return;

  if (confirm(`Pretende limpar os dados operacionais do PUC de "${c.name}"?`)) {
    c.weekly_roadmap = [];
    c.puc_raw_sections = null;
    if (Array.isArray(c.milestones)) {
      c.milestones.forEach(m => { m.date_due = null; m.date_start = null; });
    }
    c.updatedAt = new Date().toISOString();
    render();
    await saveServerData();
  }
}

/* =========================================================================
   CALENDÁRIO MATRICIAL
   ========================================================================= */
function renderCalendarToggles(activeCourses) {
  const container = document.getElementById('calUcToggles');
  if (!container) return;
  container.innerHTML = '<span style="font-size:12px; font-weight:700; color:var(--text-dim); text-transform:uppercase;">Filtrar UCs:</span>';

  activeCourses.forEach(ac => {
    const isChecked = appState.ucCalendarFilters[ac.course.name] !== false;
    const pill = document.createElement('label');
    pill.className = `cal-toggle-pill ${isChecked ? 'active' : ''}`;
    pill.style.color = ac.color;
    pill.innerHTML = `
      <input type="checkbox" ${isChecked ? 'checked' : ''} style="display:none;">
      <span class="dot" style="background:${ac.color}; width:8px; height:8px; border-radius:50%;"></span>
      <span>${escapeHtml(ac.code)}: ${escapeHtml(ac.course.name)}</span>
    `;
    pill.querySelector('input').addEventListener('change', (e) => {
      appState.ucCalendarFilters[ac.course.name] = e.target.checked;
      pill.classList.toggle('active', e.target.checked);
      renderCalendar();
    });
    container.appendChild(pill);
  });
}

function goToCurrentCalendarMonth() {
  const now = new Date();
  appState.calYear = now.getFullYear();
  appState.calMonth = now.getMonth();
  renderCalendar();
}

function renderCalendar() {
  const displayEl = document.getElementById("calMonthDisplay");
  const gridEl = document.getElementById("calDaysGrid");
  if (!displayEl || !gridEl) return;

  const year = appState.calYear;
  const month = appState.calMonth;

  displayEl.innerText = `${MONTH_NAMES[month]} ${year}`;
  gridEl.innerHTML = "";

  const firstDay = new Date(year, month, 1);
  const totalDays = new Date(year, month + 1, 0).getDate();

  let startDayOfWeek = firstDay.getDay() - 1;
  if (startDayOfWeek === -1) startDayOfWeek = 6;

  const prevMonthTotalDays = new Date(year, month, 0).getDate();
  for (let i = startDayOfWeek - 1; i >= 0; i--) {
    const dayCell = document.createElement("div");
    dayCell.className = "cal-day-cell other-month";
    dayCell.innerHTML = `<span class="day-number">${prevMonthTotalDays - i}</span>`;
    gridEl.appendChild(dayCell);
  }

  const today = new Date();
  for (let d = 1; d <= totalDays; d++) {
    const dayCell = document.createElement("div");
    dayCell.className = "cal-day-cell";
    if (today.getFullYear() === year && today.getMonth() === month && today.getDate() === d) {
      dayCell.classList.add("today");
    }

    dayCell.innerHTML = `<span class="day-number">${d}</span>`;

    const cellDateStart = new Date(year, month, d, 0, 0, 0);
    const cellDateEnd = new Date(year, month, d, 23, 59, 59);

    appState.courses.forEach((c, cIdx) => {
      if (c.isCompleted || appState.ucCalendarFilters[c.name] === false) return;
      const ucColor = getUcColor(c, cIdx);
      const ucCode = getUcShortCode(c);

      (c.milestones || []).forEach(m => {
        if (!m.date_due) return;

        const delivId = getScopedMilestoneId(c.name, m);
        if (appState.completedDeliveries[delivId]) return;

        const dueDate = parseIsoDate(m.date_due);
        const startDate = m.date_start ? parseIsoDate(m.date_start) : new Date(dueDate);

        if (cellDateEnd >= startDate && cellDateStart <= dueDate) {
          const isFinalDay = (dueDate.getFullYear() === year && dueDate.getMonth() === month && dueDate.getDate() === d);
          const eventBar = document.createElement("div");
          eventBar.className = "cal-event-bar";
          eventBar.style.backgroundColor = ucColor;
          eventBar.title = `${ucCode}: ${m.name}\\nPeríodo: ${m.date_start || 'Início'} até ${m.date_due}\\nCotação: ${m.weight}v`;

          eventBar.innerHTML = `
            <span class="event-title">${isFinalDay ? '🏁 ' : ''}${escapeHtml(ucCode)}: ${escapeHtml(m.name)}</span>
            <span class="event-val">${parseFloat(m.weight).toFixed(1)}v</span>
          `;
          eventBar.onclick = () => openSemesterSubView('sub-entregas');
          dayCell.appendChild(eventBar);
        }
      });
    });

    gridEl.appendChild(dayCell);
  }

  const totalCellsRendered = startDayOfWeek + totalDays;
  const remainingCells = (7 - (totalCellsRendered % 7)) % 7;
  for (let nextD = 1; nextD <= remainingCells; nextD++) {
    const dayCell = document.createElement("div");
    dayCell.className = "cal-day-cell other-month";
    dayCell.innerHTML = `<span class="day-number">${nextD}</span>`;
    gridEl.appendChild(dayCell);
  }
}

document.getElementById("prevMonthBtn").addEventListener("click", () => {
  if (appState.calMonth === 0) {
    appState.calMonth = 11;
    appState.calYear--;
  } else {
    appState.calMonth--;
  }
  renderCalendar();
});

document.getElementById("nextMonthBtn").addEventListener("click", () => {
  if (appState.calMonth === 11) {
    appState.calMonth = 0;
    appState.calYear++;
  } else {
    appState.calMonth++;
  }
  renderCalendar();
});

/* =========================================================================
   PAINEL DE ATIVIDADES SUMATIVAS
   ========================================================================= */
function renderConsolidatedDeliveriesTable() {
  const tbody = document.getElementById('consolidatedDeliveriesTbody');
  const filterSelect = document.getElementById('delivFilterUcSelect');
  if (!tbody || !filterSelect) return;

  const selectedFilter = filterSelect.value || 'ALL';
  filterSelect.innerHTML = '<option value="ALL">Todas as Disciplinas</option>';

  const activeCourses = appState.courses.filter(c => !c.isCompleted);
  activeCourses.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.name;
    opt.innerText = `${c.name} (${c.code || getUcShortCode(c)})`;
    filterSelect.appendChild(opt);
  });
  filterSelect.value = selectedFilter;

  const deliveries = [];
  activeCourses.forEach((c, cIdx) => {
    if (selectedFilter !== 'ALL' && c.name !== selectedFilter) return;

    const ucCode = getUcShortCode(c);
    const ucColor = getUcColor(c, cIdx);

    (c.milestones || []).forEach(m => {
      const delivId = getScopedMilestoneId(c.name, m);
      deliveries.push({
        id: delivId,
        rawId: m.id || m.name,
        courseName: c.name,
        ucCode: ucCode,
        ucColor: ucColor,
        name: m.name,
        description: m.description || '',
        type: m.type || (m.weight >= 10 ? 'exam' : 'efolio'),
        date_start: m.date_start || '',
        date_due: m.date_due || '',
        weight: parseFloat(m.weight || 0)
      });
    });
  });

  const { field, direction } = deliveriesSortState;
  deliveries.sort((a, b) => {
    let valA = a[field] || '';
    let valB = b[field] || '';

    if (field === 'weight') {
      valA = parseFloat(valA);
      valB = parseFloat(valB);
    }

    if (valA < valB) return direction === 'asc' ? -1 : 1;
    if (valA > valB) return direction === 'asc' ? 1 : -1;
    return 0;
  });

  document.getElementById('delivCountLabel').innerText = `A mostrar ${deliveries.length} momentos de avaliação`;
  tbody.innerHTML = '';

  if (deliveries.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:24px; color:var(--text-dim);">Nenhum momento de avaliação encontrado.</td></tr>';
    return;
  }

  deliveries.forEach(d => {
    const isDone = !!appState.completedDeliveries[d.id];
    const tr = document.createElement('tr');
    tr.id = `deliv_row_${d.id}`;
    if (isDone) tr.classList.add('is-done');

    let startFmt = '<span style="color:var(--rose-accent);">Sem data</span>';
    if (d.date_start) {
      const fmt = formatFullDate(d.date_start);
      if (fmt) startFmt = fmt;
    }

    let dueFmt = '<span style="color:var(--rose-accent);">Sem data</span>';
    if (d.date_due) {
      const fmt = formatFullDate(d.date_due);
      if (fmt) {
        dueFmt = fmt;
        if (d.date_due.includes('T')) {
          dueFmt += ` às ${d.date_due.split('T')[1].substring(0, 5)}`;
        }
      }
    }

    let typeBadge = '<span class="type-badge-pill type-async">ASSÍNCRONA</span>';
    if (d.type === 'exam' || d.type === 'Síncrona') {
      typeBadge = '<span class="type-badge-pill type-sync">SÍNCRONA</span>';
    } else if (d.type === 'participation') {
      typeBadge = '<span class="type-badge-pill type-part">PARTICIPAÇÃO</span>';
    }

    tr.innerHTML = `
      <td style="text-align: center;">
        <input type="checkbox" class="smart-checkbox" id="deliv_chk_${escapeHtml(d.id)}" ${isDone ? 'checked' : ''}
          data-deliv-id="${escapeHtml(d.id)}"
          onchange="toggleDeliveryStatus(this.dataset.delivId, this.checked)">
      </td>
      <td>
        <span class="uc-badge-pill" style="border: 1px solid ${d.ucColor}; color: ${d.ucColor}; background: ${d.ucColor}15;">
          ${escapeHtml(d.ucCode)}
        </span>
      </td>
      <td>
        <strong class="deliv-task-name">${escapeHtml(d.name)}</strong>
        ${d.description ? `<div style="font-size:11.5px; color:var(--text-muted); margin-top:2px;">${escapeHtml(d.description)}</div>` : ''}
      </td>
      <td>${typeBadge}</td>
      <td style="font-family:var(--font-mono); font-size:12px; color:var(--text-muted);">${startFmt}</td>
      <td style="font-family:var(--font-mono); font-size:12px; font-weight:700; color:${d.type === 'exam' ? 'var(--rose-accent)' : 'inherit'};">
        ${dueFmt}
      </td>
      <td>
        <span class="cota-badge">${d.weight.toFixed(1)}v</span>
      </td>
      <td style="text-align:right;">
        <button class="btn-subtle" style="font-size:0.75rem; padding:4px 8px;"
          data-course="${escapeHtml(d.courseName)}"
          data-milestone="${escapeHtml(d.rawId)}"
          onclick="quickEditMilestoneDates(this.dataset.course, this.dataset.milestone)"
          title="Ajustar ou definir datas de início e entrega">✏️ Datas</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function sortDeliveries(field) {
  if (deliveriesSortState.field === field) {
    deliveriesSortState.direction = deliveriesSortState.direction === 'asc' ? 'desc' : 'asc';
  } else {
    deliveriesSortState.field = field;
    deliveriesSortState.direction = 'asc';
  }
  renderConsolidatedDeliveriesTable();
}

async function toggleDeliveryStatus(delivId, isChecked) {
  if (isChecked) {
    appState.completedDeliveries[delivId] = true;
  } else {
    delete appState.completedDeliveries[delivId];
  }

  document.querySelectorAll(`[data-deliv-id="${delivId}"]`).forEach(chk => {
    chk.checked = isChecked;
    const bar = chk.closest('.card-highlight-bar');
    if (bar) bar.classList.toggle('is-done', isChecked);
  });

  const tableChk = document.getElementById(`deliv_chk_${delivId}`);
  if (tableChk) {
    tableChk.checked = isChecked;
    const tr = tableChk.closest('tr');
    if (tr) tr.classList.toggle('is-done', isChecked);
  }

  render();
  renderCalendar();
  await saveServerData();
}

/* =========================================================================
   ROTEIRO SEMANAL COMPILADO
   ========================================================================= */
function detectRealCurrentWeek() {
  const today = new Date();
  today.setHours(12, 0, 0, 0);

  for (const [wNum, range] of Object.entries(SEMESTER_WEEKS)) {
    const sDate = parseIsoDate(range.start);
    const eDate = parseIsoDate(range.end);
    eDate.setHours(23, 59, 59, 999);
    if (today >= sDate && today <= eDate) {
      return parseInt(wNum, 10);
    }
  }
  return 0;
}

function renderUnifiedWeeklyRoadmap(activeCourses) {
  const container = document.getElementById('unifiedWeeklyRoadmapContainer');
  if (!container) return;
  container.innerHTML = '';

  if (activeCourses.length === 0) {
    container.innerHTML = `<div style="color:var(--text-dim); font-size:0.9rem; padding:32px; background:var(--bg-surface); border-radius:10px; text-align:center;">Não existem Unidades Curriculares em frequência.<br><br><button class="btn-puc" onclick="openUcModal('puc')">🤖 Importar PUC (JSON)</button></div>`;
    return;
  }

  const weeksMap = {};
  activeCourses.forEach(({ course, index, color, code }) => {
    (course.weekly_roadmap || []).forEach((w, wIdx) => {
      const weekNum = w.week !== undefined ? w.week : 1;
      if (!weeksMap[weekNum]) weeksMap[weekNum] = [];

      let tags = w.tags && Array.isArray(w.tags) && w.tags.length >= 3 ? [...w.tags] : [];
      if (tags.length < 3) {
        const fullText = ((w.topic || '') + ' ' + (w.activities || '')).toLowerCase();
        const candidateTags = [];

        if (code === 'SR' || fullText.includes('rede') || fullText.includes('wireshark')) candidateTags.push('Wireshark', 'TCP/IP', 'Redes');
        if (code === 'POO' || fullText.includes('python') || fullText.includes('classe')) candidateTags.push('Python', 'POO', 'SOLID', 'Testes');
        if (code === 'LC' || fullText.includes('autómato') || fullText.includes('regex')) candidateTags.push('Linguagens Formais', 'UAbALL', 'Autómatos');
        if (code === 'FBD' || fullText.includes('sql') || fullText.includes('relacional')) candidateTags.push('Bases de Dados', 'SQL', 'Modelo ER', 'Álgebra');
        if (code === 'FG' || fullText.includes('mecânica') || fullText.includes('euler')) candidateTags.push('Mecânica', 'Física', 'Métodos Numéricos');

        candidateTags.forEach(t => { if (!tags.includes(t)) tags.push(t); });
        if (tags.length < 3) tags.push('Estudo Autónomo', 'Atividade Formativa');
      }
      tags = tags.slice(0, 5);

      const weekRange = SEMESTER_WEEKS[weekNum] || { start: "2026-09-14", end: "2027-01-25" };
      const wStartDate = parseIsoDate(weekRange.start);
      const wEndDate = parseIsoDate(weekRange.end);
      wEndDate.setHours(23, 59, 59, 999);

      let dueMilestone = null;
      let openMilestone = null;

      (course.milestones || []).forEach(m => {
        if (!m.date_due) return;
        const due = parseIsoDate(m.date_due);
        const mStart = m.date_start ? parseIsoDate(m.date_start) : due;

        if (due >= wStartDate && due <= wEndDate) dueMilestone = m;
        if (mStart >= wStartDate && mStart <= wEndDate) openMilestone = m;
      });

      const isFullCycleInWeek = dueMilestone && openMilestone && (dueMilestone.id === openMilestone.id);
      let highlightHtml = '';

      if (isFullCycleInWeek) {
        const m = dueMilestone;
        const delivId = getScopedMilestoneId(course.name, m);
        const isDone = !!appState.completedDeliveries[delivId];
        const sFmt = formatDayMonth(m.date_start);
        const dFmt = formatDayMonth(m.date_due);
        highlightHtml = `
          <div class="card-highlight-bar alert-fullcycle ${isDone ? 'is-done' : ''}">
            <div style="display:flex; align-items:center; gap:8px;">
              <input type="checkbox" class="smart-checkbox" data-deliv-id="${escapeHtml(delivId)}" ${isDone ? 'checked' : ''}
                onchange="toggleDeliveryStatus(this.dataset.delivId, this.checked)" title="Marcar entrega sumativa como concluída">
              <span class="highlight-clickable-title"
                data-course="${escapeHtml(course.name)}"
                data-deliv-id="${escapeHtml(delivId)}"
                onclick="navigateToDeliveriesWithTarget(this.dataset.course, this.dataset.delivId)"
                title="Clica para ver os detalhes nas Atividades Sumativas">
                🟢 Abertura & 🚨 Fecho: ${escapeHtml(m.name)} (${parseFloat(m.weight).toFixed(1)}v) ↗
              </span>
            </div>
            <span class="highlight-date-span">${sFmt} a ${dFmt}</span>
          </div>`;
      } else if (dueMilestone) {
        const m = dueMilestone;
        const delivId = getScopedMilestoneId(course.name, m);
        const isDone = !!appState.completedDeliveries[delivId];
        const dFmt = formatDayMonth(m.date_due);
        highlightHtml = `
          <div class="card-highlight-bar alert-danger ${isDone ? 'is-done' : ''}">
            <div style="display:flex; align-items:center; gap:8px;">
              <input type="checkbox" class="smart-checkbox" data-deliv-id="${escapeHtml(delivId)}" ${isDone ? 'checked' : ''}
                onchange="toggleDeliveryStatus(this.dataset.delivId, this.checked)" title="Marcar entrega sumativa como concluída">
              <span class="highlight-clickable-title"
                data-course="${escapeHtml(course.name)}"
                data-deliv-id="${escapeHtml(delivId)}"
                onclick="navigateToDeliveriesWithTarget(this.dataset.course, this.dataset.delivId)"
                title="Clica para ver os detalhes nas Atividades Sumativas">
                🚨 🚨 Fecho: ${escapeHtml(m.name)} (${parseFloat(m.weight).toFixed(1)}v) ↗
              </span>
            </div>
            <span class="highlight-date-span">Limite: ${dFmt}</span>
          </div>`;
      } else if (openMilestone) {
        const m = openMilestone;
        const delivId = getScopedMilestoneId(course.name, m);
        const isDone = !!appState.completedDeliveries[delivId];
        const sFmt = formatDayMonth(m.date_start);
        const dFmt = formatDayMonth(m.date_due);
        highlightHtml = `
          <div class="card-highlight-bar alert-open ${isDone ? 'is-done' : ''}">
            <div style="display:flex; align-items:center; gap:8px;">
              <input type="checkbox" class="smart-checkbox" data-deliv-id="${escapeHtml(delivId)}" ${isDone ? 'checked' : ''}
                onchange="toggleDeliveryStatus(this.dataset.delivId, this.checked)" title="Marcar entrega sumativa como concluída">
              <span class="highlight-clickable-title"
                data-course="${escapeHtml(course.name)}"
                data-deliv-id="${escapeHtml(delivId)}"
                onclick="navigateToDeliveriesWithTarget(this.dataset.course, this.dataset.delivId)"
                title="Clica para ver os detalhes nas Atividades Sumativas">
                🚀 🚀 Abertura: ${escapeHtml(m.name)} (${parseFloat(m.weight).toFixed(1)}v) ↗
              </span>
            </div>
            <span class="highlight-date-span">Prazo: ${sFmt} a ${dFmt}</span>
          </div>`;
      } else {
        highlightHtml = `
          <div class="card-highlight-bar alert-info">
            <span>📌 ${escapeHtml(w.topic)}</span>
          </div>`;
      }

      weeksMap[weekNum].push({
        courseName: course.name,
        ucCode: code,
        courseIdx: index,
        weekIdx: wIdx,
        color: color,
        topic: w.topic,
        activities: w.activities,
        tags: tags,
        highlightBar: highlightHtml,
        hasMilestoneDue: !!dueMilestone,
        hasMilestoneOpen: !!openMilestone,
        completed: w.completed
      });
    });
  });

  const sortedWeeks = Object.keys(weeksMap).map(Number).sort((a, b) => a - b);
  if (sortedWeeks.length === 0) return;

  const realCurrentWeekNumber = detectRealCurrentWeek();

  sortedWeeks.forEach(weekNum => {
    const tasks = weeksMap[weekNum];
    const totalWTasks = tasks.length;
    const doneWTasks = tasks.filter(t => t.completed).length;
    const isCurrent = (weekNum === realCurrentWeekNumber);
    const isExpanded = appState.expandedWeeks[weekNum] !== false;

    const dueUcsSet = new Set();
    const openUcsSet = new Set();
    tasks.forEach(t => {
      if (t.hasMilestoneDue) dueUcsSet.add(t.ucCode);
      if (t.hasMilestoneOpen) openUcsSet.add(t.ucCode);
    });

    const dueUcsArray = Array.from(dueUcsSet);
    const openUcsArray = Array.from(openUcsSet);

    const weekDatesStr = SEMESTER_WEEKS[weekNum] ? SEMESTER_WEEKS[weekNum].label : `Semana ${weekNum}`;

    const weekBlock = document.createElement('div');
    weekBlock.className = `week-block ${isCurrent ? 'current-week' : ''} ${isExpanded ? 'expanded' : ''}`;
    weekBlock.id = `weekBlock_${weekNum}`;

    let tasksHtml = '';
    tasks.forEach(t => {
      const tagsGroupHtml = t.tags.map(tag => `<span class="topic-tag">${escapeHtml(tag)}</span>`).join(' ');

      tasksHtml += `
        <div class="uc-activity-card ${t.completed ? 'is-done' : ''}">
          <div class="card-study-body">
            <div class="card-top">
              <span class="tag" style="background:${t.color}20; color:${t.color}; border:1px solid ${t.color}50; font-size:11.5px; font-weight:800;">
                ${escapeHtml(t.courseName)}
              </span>
              <input type="checkbox" class="smart-checkbox" ${t.completed ? 'checked' : ''}
                onchange="toggleWeekTask(${t.courseIdx}, ${t.weekIdx}, this.checked)" title="Marcar estudo da matéria semanal como concluído">
            </div>
            <div class="card-task-title">${escapeHtml(t.topic)}</div>
            ${t.activities ? `<div class="card-topics">${escapeHtml(t.activities)}</div>` : ''}
            <div class="card-tags-group">${tagsGroupHtml}</div>
          </div>
          ${t.highlightBar}
        </div>
      `;
    });

    weekBlock.innerHTML = `
      <div class="week-header" onclick="toggleWeekAccordion(${weekNum})">
        <div class="week-header-left">
          <span class="week-chevron">▶</span>
          <span class="week-number">Semana ${weekNum}:</span>
          <span class="week-dates">${weekDatesStr}</span>
          ${isCurrent ? '<span class="current-week-tag">Semana Atual</span>' : ''}
        </div>
        <div class="week-header-right">
          ${openUcsArray.length > 0 ? `<span class="critical-open-badge" title="Esta semana há abertura de atividades sumativas!">🟢 Aberturas: ${openUcsArray.join(', ')}</span>` : ''}
          ${dueUcsArray.length > 0 ? `<span class="critical-due-badge" title="Esta semana há prazos limite de entregas!">🚨 Entregas: ${dueUcsArray.join(', ')}</span>` : ''}
          <div class="week-dates">${doneWTasks}/${totalWTasks} tarefas cumpridas</div>
        </div>
      </div>
      <div class="week-content-body">
        <div class="week-cards-grid">${tasksHtml}</div>
      </div>
    `;
    container.appendChild(weekBlock);
  });
}

function toggleWeekAccordion(weekNum) {
  const block = document.getElementById(`weekBlock_${weekNum}`);
  if (!block) return;
  const isNowExpanded = !block.classList.contains('expanded');
  block.classList.toggle('expanded', isNowExpanded);
  appState.expandedWeeks[weekNum] = isNowExpanded;
  saveServerData();
}

function focusCurrentWeek(smooth = true) {
  const currentBlock = document.querySelector('.week-block.current-week') || document.querySelector('.week-block');
  if (currentBlock) {
    currentBlock.classList.add('expanded');
    currentBlock.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'center' });
  }
}

function expandAllWeeks(expand) {
  document.querySelectorAll('.week-block').forEach(b => {
    b.classList.toggle('expanded', expand);
    const id = b.id.replace('weekBlock_', '');
    appState.expandedWeeks[id] = expand;
  });
  saveServerData();
}

/* =========================================================================
   PUC DAS UC
   ========================================================================= */
function renderDossierSubView(activeCourses) {
  const dBar = document.getElementById('dossierUcFilterBar');
  const container = document.getElementById('dossierContent');
  if (!dBar || !container) return;

  dBar.innerHTML = '';
  container.innerHTML = '';

  if (activeCourses.length === 0) {
    container.innerHTML = '<p style="color:var(--text-dim); font-size:0.85rem; padding:16px;">Sem UCs em frequência.</p>';
    return;
  }

  if (selectedDossierUcIdx >= activeCourses.length) selectedDossierUcIdx = 0;

  activeCourses.forEach((ac, pos) => {
    const btnD = document.createElement('button');
    btnD.className = `uc-pill-btn ${pos === selectedDossierUcIdx ? 'active' : ''}`;
    btnD.innerHTML = `<span class="course-dot" style="background:${ac.color};"></span>${escapeHtml(ac.course.name)}`;
    btnD.onclick = () => { selectedDossierUcIdx = pos; renderDossierSubView(activeCourses); };
    dBar.appendChild(btnD);
  });

  const ac = activeCourses[selectedDossierUcIdx];
  if (!ac) return;

  const { course, color } = ac;
  const rOutcomes = (course.learning_outcomes || []).map(ra => `<li>${escapeHtml(ra)}</li>`).join('');
  const bMandatory = (course.bibliography?.mandatory || []).map(b => `<li>${escapeHtml(b)}</li>`).join('');
  const bComplem   = (course.bibliography?.complementary || []).map(b => `<li>${escapeHtml(b)}</li>`).join('');

  const card = document.createElement('div');
  card.className = 'roadmap-course-card';
  card.innerHTML = `
    <div class="roadmap-header">
      <div>
        <span style="font-size:0.72rem; color:${color}; font-weight:800;">PUC DA UNIDADE CURRICULAR</span>
        <h3 style="font-size:1.15rem; margin-top:2px;">${escapeHtml(course.name)}</h3>
        <p style="font-size:0.8rem; color:var(--text-muted); margin-top:3px;">
          Regência: <b>${course.faculty?.regent || 'Não especificado'}</b> ${course.faculty?.team ? '• Docentes: ' + course.faculty.team.join(', ') : ''}
        </p>
      </div>
      <span class="tag tag-active">${course.ects} ECTS</span>
    </div>

    ${course.presentation ? `
      <div class="puc-block">
        <div class="puc-block-title">Apresentação & Enquadramento</div>
        <p style="font-size:0.82rem; color:var(--text-muted); line-height:1.5;">${escapeHtml(course.presentation)}</p>
      </div>` : ''}

    ${rOutcomes ? `
      <div class="puc-block">
        <div class="puc-block-title">Resultados de Aprendizagem (RA)</div>
        <ul style="font-size:0.82rem; color:var(--text-muted); margin-left:18px; line-height:1.5;">${rOutcomes}</ul>
      </div>` : ''}

    ${course.methodology_and_tools ? `
      <div class="puc-block">
        <div class="puc-block-title">Metodologia & Ferramentas</div>
        <p style="font-size:0.82rem; color:var(--text-muted); line-height:1.5;">${escapeHtml(course.methodology_and_tools)}</p>
      </div>` : ''}

    ${course.approval_conditions ? `
      <div class="puc-block">
        <div class="puc-block-title">Condições Oficiais de Aprovação</div>
        <div style="font-size:0.82rem; color:var(--text-muted); display:flex; flex-direction:column; gap:4px;">
          ${course.approval_conditions.regime ? `<div>• <b>Regime:</b> ${escapeHtml(course.approval_conditions.regime)}</div>` : ''}
          ${course.approval_conditions.weights_summary ? `<div>• <b>Ponderações:</b> ${escapeHtml(course.approval_conditions.weights_summary)}</div>` : ''}
          ${course.approval_conditions.chain_rules ? `<div>• <b>Regras em cadeia:</b> ${escapeHtml(course.approval_conditions.chain_rules)}</div>` : ''}
          ${course.approval_conditions.sync_min_grade ? `<div>• <b>Momento Síncrono / Presencial:</b> ${escapeHtml(course.approval_conditions.sync_min_grade)}</div>` : ''}
          ${course.approval_conditions.exam_rules ? `<div>• <b>Recurso / Exame:</b> ${escapeHtml(course.approval_conditions.exam_rules)}</div>` : ''}
        </div>
      </div>` : ''}

    ${(bMandatory || bComplem) ? `
      <div class="puc-block">
        <div class="puc-block-title">Bibliografia & Recursos</div>
        ${bMandatory ? `<strong style="font-size:0.75rem; color:var(--text-dim);">Obrigatória:</strong><ul style="font-size:0.8rem; color:var(--text-muted); margin-left:18px; margin-bottom:8px;">${bMandatory}</ul>` : ''}
        ${bComplem   ? `<strong style="font-size:0.75rem; color:var(--text-dim);">Complementar:</strong><ul style="font-size:0.8rem; color:var(--text-muted); margin-left:18px;">${bComplem}</ul>` : ''}
      </div>` : ''}
  `;

  if (course.puc_raw_sections) {
    const raw = course.puc_raw_sections;

    if (raw.raw_activity_plan && raw.raw_activity_plan.length > 0) {
      const rowsHtml = raw.raw_activity_plan.map(row => `
        <tr>
          <td style="font-weight:700; color:var(--cyan-accent); white-space:nowrap;">${escapeHtml(row.activity_or_theme || '')}</td>
          <td class="col-mono" style="font-size:0.75rem; white-space:nowrap;">${escapeHtml(row.period || '')}</td>
          <td><b>${escapeHtml(row.contents_or_topic || '')}</b><br><small style="color:var(--text-muted);">${escapeHtml(row.summary_description || '')}</small></td>
          <td style="font-size:0.75rem; color:var(--text-dim);">${escapeHtml(row.resources || '')}</td>
        </tr>
      `).join('');

      card.innerHTML += `
        <div class="puc-block">
          <div class="puc-block-title">📋 Plano de Atividades Original (Fonte da Verdade)</div>
          <div class="table-container" style="max-height: 340px; overflow-y: auto;">
            <table>
              <thead><tr><th>Atividade/Tema</th><th>Período</th><th>Descrição Sumária</th><th>Recursos</th></tr></thead>
              <tbody>${rowsHtml}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    if (raw.raw_evaluation_schedule && raw.raw_evaluation_schedule.length > 0) {
      const schedHtml = raw.raw_evaluation_schedule.map(s => `
        <tr>
          <td><strong>${escapeHtml(s.activity_name || '')}</strong></td>
          <td><span class="tag tag-active">${escapeHtml(s.weight_text || '')}</span></td>
          <td><span class="tag ${s.modality === 'Síncrona' ? 'tag-passed' : 'tag-active'}">${escapeHtml(s.modality || '')}</span></td>
          <td class="col-mono" style="font-size:0.75rem;">${escapeHtml(s.release_date || '—')}</td>
          <td class="col-mono" style="font-size:0.75rem; font-weight:700; color:var(--rose-accent);">${escapeHtml(s.due_date || '—')}</td>
        </tr>
      `).join('');

      card.innerHTML += `
        <div class="puc-block">
          <div class="puc-block-title">⚖️ Calendário Oficial de Avaliação (Transcrição do PUC)</div>
          <div class="table-container">
            <table>
              <thead><tr><th>Atividade</th><th>Cotação</th><th>Modalidade</th><th>Disponibilização</th><th>Data Limite</th></tr></thead>
              <tbody>${schedHtml}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    if (raw.raw_approval_rules) {
      card.innerHTML += `
        <div class="puc-block">
          <div class="puc-block-title">📜 Regulamento Integral de Avaliação e Recurso</div>
          <p style="font-size:0.8rem; color:var(--text-muted); line-height:1.6; white-space:pre-wrap;">${escapeHtml(raw.raw_approval_rules)}</p>
        </div>
      `;
    }
  }

  container.appendChild(card);
}

/* =========================================================================
   TOASTS / NOTIFICAÇÕES
   ========================================================================= */
function checkToastDeadlines() {
  if (!appState.notificationsActive) return;
  const container = document.getElementById("toastContainer");
  if (!container) return;
  container.innerHTML = "";

  const now = new Date();
  const advance = appState.notifyAdvanceDays;

  appState.courses.forEach(c => {
    if (c.isCompleted) return;
    (c.milestones || []).forEach(m => {
      if (!m.date_due) return;
      const key = getScopedMilestoneId(c.name, m);
      if (appState.mutedNotifications.includes(key) ||
          appState.sessionDismissed.includes(key) ||
          appState.completedDeliveries[key]) return;

      const due = parseIsoDate(m.date_due);
      const diffDays = (due.getTime() - now.getTime()) / (1000 * 3600 * 24);

      if (diffDays <= advance && diffDays >= -0.5) {
        showToast(c.name, m, diffDays, key);
      }
    });
  });
}

function showToast(courseName, milestone, daysDiff, key) {
  const container = document.getElementById("toastContainer");
  const isUrgent = daysDiff <= 1;

  const toast = document.createElement("div");
  toast.className = `toast-card ${isUrgent ? 'urgent' : ''}`;
  toast.innerHTML = `
    <div class="toast-header">
      <span class="tag tag-active">${escapeHtml(courseName)}</span>
      <button style="background:none; border:none; color:var(--text-dim); cursor:pointer;" onclick="this.closest('.toast-card').remove()">✕</button>
    </div>
    <div class="toast-title">${escapeHtml(milestone.name)}</div>
    <div class="toast-desc">
      ${daysDiff < 0 ? 'Prazo a terminar hoje!' : `Faltam ${Math.ceil(daysDiff)} dias (${milestone.date_due})`} • Cotação: <strong>${milestone.weight}v</strong>
    </div>
    <div class="toast-footer">
      <button class="toast-btn toast-btn-dismiss" data-key="${escapeHtml(key)}" onclick="dismissToast(this.dataset.key)">Entendido</button>
      <button class="toast-btn toast-btn-mute" data-key="${escapeHtml(key)}" onclick="muteToast(this.dataset.key)">Não avisar mais</button>
    </div>
  `;
  container.appendChild(toast);
}

function dismissToast(key) {
  appState.sessionDismissed.push(key);
  checkToastDeadlines();
}

function muteToast(key) {
  if (!appState.mutedNotifications.includes(key)) {
    appState.mutedNotifications.push(key);
    saveServerData();
  }
  checkToastDeadlines();
}

/* =========================================================================
   CRUD DE UCs
   ========================================================================= */
function startEditCourse(idx) {
  const c = appState.courses[idx];
  if (!c) { alert('UC não encontrada.'); return; }

  document.getElementById('courseForm').reset();
  document.getElementById('milestoneEditorRowsList').innerHTML = '';

  document.getElementById('editingCourseIndex').value = idx;
  document.getElementById('cName').value         = c.name || '';
  document.getElementById('cCode').value         = c.code || '';
  document.getElementById('cAcademicYear').value = c.academicYear || '';
  document.getElementById('cEcts').value         = c.ects || DEFAULT_ECTS;
  document.getElementById('cYear').value         = (c.year || 1).toString();
  document.getElementById('cSem').value          = (c.sem  || 1).toString();
  document.getElementById('cModel').value        = c.model || 'uab_standard';
  document.getElementById('cStatus').value       = c.isCompleted ? 'completed' : 'ongoing';
  document.getElementById('cFinalGrade').value   = (c.finalGrade !== null && c.finalGrade !== undefined) ? c.finalGrade : '';

  if (Array.isArray(c.milestones) && c.milestones.length > 0) {
    c.milestones.forEach(m => addMilestoneEditorRow(m));
  } else {
    addMilestoneEditorRow({ name: 'Atividade Sumativa 1', weight: 4.0, date_start: null, date_due: null, type: 'efolio' });
    addMilestoneEditorRow({ name: 'Atividade Sumativa 2', weight: 4.0, date_start: null, date_due: null, type: 'efolio' });
    addMilestoneEditorRow({ name: 'Prova Global / Final', weight: 12.0, date_start: null, date_due: null, type: 'exam' });
  }

  toggleGradeFieldVisibility();

  document.getElementById('entryModeSelector').style.display = 'none';
  document.getElementById('pucUploadSection').style.display  = 'none';
  document.getElementById('courseForm').style.display        = 'block';
  document.getElementById('ucModalTitle').innerText          = `✏️ A Editar: ${c.name}`;
  document.getElementById('saveCourseBtn').innerText         = 'Atualizar Unidade Curricular';

  document.getElementById('ucModalOverlay').classList.add('active');
}

function cancelEditCourse() {
  document.getElementById('courseForm').reset();
  document.getElementById('editingCourseIndex').value = '-1';
  document.getElementById('ucModalTitle').innerText = 'Adicionar / Configurar Unidade Curricular';
  document.getElementById('saveCourseBtn').innerText = 'Guardar Unidade Curricular';
  document.getElementById('entryModeSelector').style.display = 'grid';
  document.getElementById('milestoneEditorRowsList').innerHTML = '';
  selectEntryMode('manual');
  toggleGradeFieldVisibility();
}

async function handleSaveCourse(e) {
  e.preventDefault();

  const editIdx = parseIntSafe(document.getElementById('editingCourseIndex').value, -1);
  const modal   = document.querySelector('#ucModalOverlay .modal-container');

  const nameVal = document.getElementById('cName').value.trim();
  const yearVal = document.getElementById('cAcademicYear').value.trim();
  const ectsVal = parseIntSafe(document.getElementById('cEcts').value, NaN);
  const isCompleted = document.getElementById('cStatus').value === 'completed';
  const gradeInputVal = document.getElementById('cFinalGrade').value;

  const errors = [];
  if (!nameVal) errors.push('• O nome da Unidade Curricular é obrigatório.');
  if (!yearVal) errors.push('• O ano letivo de frequência é obrigatório (ex: 2026/2027).');
  if (!ectsVal || ectsVal < 1 || ectsVal > 30) errors.push('• Os créditos ECTS devem estar entre 1 e 30.');

  if (isCompleted && gradeInputVal !== '') {
    const g = parseFloat(gradeInputVal);
    if (isNaN(g) || g < GRADE_MIN || g > GRADE_MAX) {
      errors.push(`• A nota final deve estar entre ${GRADE_MIN}.0 e ${GRADE_MAX}.0.`);
    }
  }

  if (errors.length > 0) {
    if (modal) modal.scrollTo({ top: 0, behavior: 'smooth' });
    alert('⚠️ Corrige os seguintes campos antes de guardar:\\n\\n' + errors.join('\\n'));
    return;
  }

  const parsedFinalGrade = (isCompleted && gradeInputVal !== '') ? parseFloat(gradeInputVal) : null;
  const newName         = nameVal;
  const newCode         = document.getElementById('cCode').value.trim() || null;
  const newAcademicYear = yearVal;
  const newEcts         = ectsVal;
  const newYear         = document.getElementById('cYear').value;
  const newSem          = document.getElementById('cSem').value;
  const newModel        = document.getElementById('cModel').value || 'uab_standard';

  const updatedMilestones = [];
  document.querySelectorAll('.milestone-editor-card').forEach(card => {
    const name = card.querySelector('.m-name-input').value.trim();
    const weight = parseFloat(card.querySelector('.m-weight-input').value) || 0;
    const start = card.querySelector('.m-start-input').value || null;
    const due = card.querySelector('.m-due-input').value || null;

    if (name) {
      updatedMilestones.push({
        id: card.dataset.id || ('deliv_' + Date.now() + '_' + Math.random().toString(36).slice(2, 5)),
        name: name,
        type: card.dataset.type || (weight >= 10 ? 'exam' : 'efolio'),
        weight: weight,
        date_start: start,
        date_due: due,
        description: card.dataset.desc || '',
        grade: null
      });
    }
  });

  if (editIdx >= 0) {
    const c = appState.courses[editIdx];

    if (c.name !== newName && appState.ucCalendarFilters[c.name] !== undefined) {
      appState.ucCalendarFilters[newName] = appState.ucCalendarFilters[c.name];
      delete appState.ucCalendarFilters[c.name];
    }

    c.name         = newName;
    c.code         = newCode;
    c.academicYear = newAcademicYear;
    c.ects         = newEcts;
    c.year         = newYear;
    c.sem          = newSem;
    c.model        = newModel;
    c.isCompleted  = isCompleted;
    c.finalGrade   = parsedFinalGrade;

    if (updatedMilestones.length > 0) {
      if (Array.isArray(c.milestones)) {
        updatedMilestones.forEach(m => {
          const old = c.milestones.find(x => x.id === m.id);
          if (old && old.grade !== undefined) m.grade = old.grade;
        });
      }
      c.milestones = updatedMilestones;
    }

    c.updatedAt = new Date().toISOString();
  } else {
    appState.courses.push({
      name: newName,
      code: newCode,
      academicYear: newAcademicYear,
      ects: newEcts,
      year: newYear,
      sem: newSem,
      model: newModel,
      isCompleted: isCompleted,
      finalGrade: parsedFinalGrade,
      milestones: updatedMilestones,
      weekly_roadmap: [],
      updatedAt: new Date().toISOString()
    });
  }

  cancelEditCourse();
  document.getElementById('ucModalOverlay').classList.remove('active');
  render();
  renderCalendar();
  await saveServerData();
  navigate('tab-inicio');
}

async function deleteCourse(idx) {
  if (confirm('Eliminar esta unidade curricular?')) {
    appState.courses.splice(idx, 1);
    render();
    renderCalendar();
    await saveServerData();
  }
}

async function toggleWeekTask(courseIdx, weekIdx, isChecked) {
  appState.courses[courseIdx].weekly_roadmap[weekIdx].completed = isChecked;
  render();
  await saveServerData();
}

async function reopenCourse(idx) {
  if (confirm(`Desejas reabrir a frequência da Unidade Curricular "${appState.courses[idx].name}"?`)) {
    appState.courses[idx].isCompleted = false;
    render();
    renderCalendar();
    await saveServerData();
    navigate('tab-inicio');
  }
}

/* =========================================================================
   IMPORTAÇÃO / EXPORTAÇÃO
   ========================================================================= */
function importPucJSON(e) {
  const file = e.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = async (evt) => {
    try {
      const data = JSON.parse(evt.target.result);
      if (!data.name || !data.ects) {
        alert('O JSON do PUC não respeita a estrutura mínima.');
        return;
      }

      const newCourse = {
        name: data.name,
        code: data.code || null,
        academicYear: data.academicYear || "2026/2027",
        ects: parseIntSafe(data.ects, DEFAULT_ECTS),
        year: data.year || "1",
        sem: data.sem || "1",
        model: data.approval_conditions?.model || data.model || "uab_standard",
        isCompleted: false,
        finalGrade: null,
        faculty: data.faculty || {},
        presentation: data.presentation || null,
        learning_outcomes: data.learning_outcomes || [],
        competences: data.competences || [],
        methodology_and_tools: data.methodology_and_tools || null,
        approval_conditions: data.approval_conditions || {},
        milestones: (data.milestones || []).map(m => ({
          id: m.id || m.name,
          name: m.name,
          type: m.type || 'efolio',
          weight: m.weight !== undefined ? m.weight : (m.maxScore !== undefined ? m.maxScore : 0),
          date_start: m.date_start ? m.date_start.split('T')[0] : null,
          date_due: m.date_due ? (m.date_due.includes('T') ? m.date_due : m.date_due + 'T23:59') : null,
          grade: m.grade !== undefined ? m.grade : (m.currentScore !== undefined ? m.currentScore : null),
          description: m.description || ''
        })),
        weekly_roadmap: (data.weekly_roadmap || []).map(w => ({
          week: w.week,
          topic: w.topic,
          activities: w.activities,
          tags: w.tags || [],
          highlight: w.highlight || null,
          highlightType: w.highlightType || 'normal',
          completed: !!w.completed
        })),
        bibliography: data.bibliography || {},
        puc_raw_sections: data.puc_raw_sections || null,
        updatedAt: new Date().toISOString()
      };

      if (newCourse.milestones.length > 0) {
        const mA = newCourse.milestones.find(m => m.id === 'efolioA' || m.id === 'deliv_1');
        const mB = newCourse.milestones.find(m => m.id === 'efolioB' || m.id === 'deliv_2');
        const mG = newCourse.milestones.find(m => m.id === 'gfolio'  || m.type === 'exam');
        newCourse.efolioA = mA ? mA.grade : null;
        newCourse.efolioB = mB ? mB.grade : null;
        newCourse.gfolio  = mG ? mG.grade : null;
      }

      const existingIdx = appState.courses.findIndex(
        c => c.name.toLowerCase() === newCourse.name.toLowerCase()
      );
      if (existingIdx >= 0) {
        appState.courses[existingIdx] = newCourse;
      } else {
        appState.courses.push(newCourse);
      }

      document.getElementById('ucModalOverlay').classList.remove('active');
      render();
      renderCalendar();
      await saveServerData();
      openSemesterSubView('sub-calendario');
      alert(`PUC de "${data.name}" importado e integrado com sucesso!`);
    } catch (err) {
      alert('Erro ao carregar PUC: ' + err.message);
    }
  };
  reader.readAsText(file);
}

function copyMasterPrompt() {
  const text = document.getElementById('masterPromptText').innerText;
  navigator.clipboard.writeText(text).then(() => {
    alert('Master Prompt copiada para a área de transferência!');
  });
}

function exportData() {
  const blob = new Blob([JSON.stringify(appState, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `my_unistation_uab_${new Date().toISOString().slice(0, 10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

function importData(e) {
  const f = e.target.files[0];
  if (!f) return;
  const r = new FileReader();
  r.onload = async (evt) => {
    try {
      const data = JSON.parse(evt.target.result);
      if (data && typeof data === 'object') {
        appState = {
          ...appState,
          schema_version: data.schema_version || 1,
          profile: data.profile || appState.profile,
          theme: data.theme || DEFAULT_THEME,
          courses: Array.isArray(data.courses) ? data.courses : [],
          notificationsActive: data.notificationsActive !== false,
          notifyAdvanceDays: parseIntSafe(data.notifyAdvanceDays, DEFAULT_NOTIFY_ADVANCE),
          mutedNotifications: data.mutedNotifications || [],
          expandedWeeks: data.expandedWeeks || {},
          completedDeliveries: data.completedDeliveries || {}
        };
        document.documentElement.setAttribute('data-theme', appState.theme);
        updateThemeIcon(appState.theme);
        render();
        renderCalendar();
        await saveServerData();
        checkToastDeadlines();
        alert('Backup restaurado com sucesso!');
      }
    } catch (err) {
      alert('Ficheiro inválido: ' + err.message);
    }
  };
  reader.readAsText(f);
}

/* =========================================================================
   BOOTSTRAP
   ========================================================================= */
window.onload = loadServerData;
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Servidor HTTP
# ---------------------------------------------------------------------------
class DashboardServer(http.server.BaseHTTPRequestHandler):
    """Servidor HTTP minimalista: SPA + persistência JSON configurável."""

    # -- Encaminhamento ----------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._respond_bytes(APP_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/data":
            self._handle_get_data()
        elif self.path == "/api/config":
            self._handle_get_config()
        else:
            self.send_error(404, "Rota não encontrada")

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/data":
            self._handle_post_data()
        elif self.path == "/api/config/preview":
            self._handle_config_preview()
        elif self.path == "/api/config":
            self._handle_post_config()
        else:
            self.send_error(404, "Rota não encontrada")

    # -- Helpers -----------------------------------------------------------
    def _read_body_json(self) -> Any | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self.send_error(400, "Content-Length inválido")
            return None
        if length <= 0:
            self.send_error(400, "Corpo vazio")
            return None
        payload = self.rfile.read(length)
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.send_error(400, f"JSON inválido: {exc}")
            return None

    def _respond_bytes(self, payload: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _respond_json(self, obj: Any, status: int = 200) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    # -- /api/data GET -----------------------------------------------------
    def _handle_get_data(self) -> None:
        data_file = get_data_file()

        if os.path.exists(data_file):
            try:
                with open(data_file, "rb") as f:
                    raw = f.read()
                parsed = json.loads(raw.decode("utf-8"))
                parsed = _migrate_state(parsed)
                payload = json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8")
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.send_error(500, f"Falha a ler data.json: {exc}")
                return
        else:
            payload = json.dumps(DEFAULT_STATE, ensure_ascii=False).encode("utf-8")

        self._respond_bytes(payload, "application/json; charset=utf-8")

    # -- /api/data POST ----------------------------------------------------
    def _handle_post_data(self) -> None:
        data = self._read_body_json()
        if data is None:
            return

        ok, msg = _validate_state(data)
        if not ok:
            self._respond_json({"status": "error", "message": msg}, status=400)
            return

        data = _migrate_state(data)

        data_file = get_data_file()
        try:
            _write_json_atomic(data_file, data)
        except OSError as exc:
            self.send_error(500, f"Falha a escrever data.json: {exc}")
            return

        self._respond_json({"status": "ok"})

    # -- /api/config GET ---------------------------------------------------
    def _handle_get_config(self) -> None:
        cfg = load_config()
        data_file = get_data_file()
        meta = _file_metadata(data_file)

        self._respond_json({
            "status": "ok",
            "data_file": cfg.get("data_file", DEFAULT_DATA_FILE),
            "resolved_path": data_file,
            "exists": meta.get("exists", False),
            "valid_json": meta.get("valid_json", False),
            "metadata": meta,
        })

    # -- /api/config/preview POST -----------------------------------------
    def _handle_config_preview(self) -> None:
        body = self._read_body_json()
        if body is None:
            return

        new_path_raw = str(body.get("new_path", "")).strip()
        if not new_path_raw:
            self._respond_json({"status": "error", "message": "Caminho vazio."})
            return

        new_path = _resolve_path(new_path_raw)
        current_path = get_data_file()

        try:
            if os.path.exists(new_path) and os.path.exists(current_path) \
               and os.path.samefile(new_path, current_path):
                self._respond_json({"status": "noop", "message": "Já estás a usar este ficheiro."})
                return
        except OSError:
            pass
        if new_path == current_path:
            self._respond_json({"status": "noop", "message": "Já estás a usar este ficheiro."})
            return

        parent = os.path.dirname(new_path) or "."
        if not os.path.isdir(parent):
            self._respond_json({
                "status": "error",
                "message": f"A pasta de destino não existe: {parent}",
            })
            return
        if not os.access(parent, os.W_OK):
            self._respond_json({
                "status": "error",
                "message": "Sem permissões de escrita na pasta de destino.",
            })
            return

        self._respond_json({
            "status": "ok",
            "current_path": current_path,
            "new_path": new_path,
            "destination_exists": os.path.exists(new_path),
            "destination_meta": _file_metadata(new_path),
            "current_meta": _file_metadata(current_path),
        })

    # -- /api/config POST --------------------------------------------------
    def _handle_post_config(self) -> None:
        body = self._read_body_json()
        if body is None:
            return

        new_path_raw = str(body.get("new_path", "")).strip()
        action = str(body.get("action", "")).strip()

        if not new_path_raw:
            self._respond_json({"status": "error", "message": "Caminho vazio."})
            return
        if action not in ("use_local", "use_remote", "migrate_to_empty", "start_fresh"):
            self._respond_json({"status": "error", "message": "Ação inválida."})
            return

        new_path = _resolve_path(new_path_raw)
        current_path = get_data_file()

        try:
            if os.path.exists(new_path) and os.path.exists(current_path) \
               and os.path.samefile(new_path, current_path):
                self._respond_json({"status": "noop", "message": "Já estás a usar este ficheiro."})
                return
        except OSError:
            pass
        if new_path == current_path:
            self._respond_json({"status": "noop", "message": "Já estás a usar este ficheiro."})
            return

        parent = os.path.dirname(new_path) or "."
        if not os.path.isdir(parent):
            self._respond_json({"status": "error", "message": f"A pasta de destino não existe: {parent}"})
            return
        if not os.access(parent, os.W_OK):
            self._respond_json({"status": "error", "message": "Sem permissões de escrita na pasta de destino."})
            return

        dest_exists = os.path.exists(new_path)

        try:
            if action == "use_local":
                try:
                    current_data = _read_json(current_path)
                except (OSError, json.JSONDecodeError) as exc:
                    self._respond_json({"status": "error", "message": f"Não foi possível ler o ficheiro atual: {exc}"})
                    return

                if dest_exists:
                    _backup_before_replace(new_path)
                _backup_before_replace(current_path)

                _write_json_atomic(new_path, current_data)

            elif action == "use_remote":
                if not dest_exists:
                    self._respond_json({"status": "error", "message": "Ficheiro de destino não existe."})
                    return
                dest_meta = _file_metadata(new_path)
                if not dest_meta.get("valid_json"):
                    self._respond_json({"status": "error", "message": "Ficheiro de destino inválido."})
                    return
                _backup_before_replace(current_path)

            elif action == "migrate_to_empty":
                if dest_exists:
                    self._respond_json({"status": "error", "message": "Destino já tem ficheiro. Usa 'use_local'."})
                    return
                try:
                    current_data = _read_json(current_path)
                except (OSError, json.JSONDecodeError):
                    current_data = DEFAULT_STATE
                _write_json_atomic(new_path, current_data)
                _backup_before_replace(current_path)

            elif action == "start_fresh":
                if dest_exists:
                    _backup_before_replace(new_path)
                _backup_before_replace(current_path)
                _write_json_atomic(new_path, DEFAULT_STATE)

        except OSError as exc:
            self._respond_json({
                "status": "error",
                "message": f"Falha durante a operação: {exc}",
            })
            return

        cfg = load_config()
        cfg["data_file"] = new_path
        try:
            _save_config_file(cfg)
        except OSError as exc:
            self._respond_json({
                "status": "error",
                "message": f"Ficheiros movidos mas falhou gravar config.json: {exc}",
            })
            return

        self._respond_json({"status": "ok", "new_path": new_path})

    # -- Log ---------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[My UniStation] {self.address_string()} — {fmt % args}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    data_path = get_data_file()

    print("✅ My UniStation ativo")
    print(f"🌐 Aceda a: http://{BIND_ADDRESS}:{PORT}")
    print(f"⚙️  Config bootstrap: {os.path.abspath(CONFIG_FILE)}")
    print(f"💾 Ficheiro de dados: {data_path}")
    print(f"🛡️  Backups automáticos com retenção de {BACKUP_RETENTION} ficheiros")
    print(f"📦 Schema version: {SCHEMA_VERSION}")

    httpd = http.server.ThreadingHTTPServer((BIND_ADDRESS, PORT), DashboardServer)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Servidor encerrado.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()