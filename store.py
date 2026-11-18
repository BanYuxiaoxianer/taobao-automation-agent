"""本地持久化（深加工版 / by 31）

上游默认 MySQL + Milvus，要 Docker、要 1GB 内存，个人电脑基本起不来。
这里默认全部落到本地 SQLite + JSON：

  data/merchant.db    对话历史 / 会话摘要 / 调用监控 / 草稿
  data/knowledge/*.json  知识库
  data/vector.json    轻量向量缓存（可选，仅当有 embedding 模型时启用）

需要 MySQL 时设置 CONVERSATION_BACKEND=mysql，接口保持不变。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta

from config import DATA_DIR

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role      TEXT NOT NULL,
    content   TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS session_summary (
    session_id TEXT PRIMARY KEY,
    summary    TEXT NOT NULL,
    key_info   TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT, model TEXT, agent TEXT, session_id TEXT,
    prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0, cost REAL DEFAULT 0,
    duration_ms INTEGER DEFAULT 0, success INTEGER DEFAULT 1,
    error       TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_time ON llm_calls(created_at);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payload TEXT NOT NULL,
    status TEXT DEFAULT 'draft',
    created_at TEXT NOT NULL
);
"""


def db_path() -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    return os.path.join(DATA_DIR, "merchant.db")


def get_conn() -> sqlite3.Connection:
    """全局单连接 + 锁。check_same_thread=False 让 Web 多线程可用。"""
    global _CONN
    with _LOCK:
        if _CONN is None:
            _CONN = sqlite3.connect(db_path(), check_same_thread=False)
            _CONN.row_factory = sqlite3.Row
            _CONN.executescript(SCHEMA)
            _CONN.commit()
            logger.debug("SQLite 打开：%s", db_path())
        return _CONN


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ─────────────────────────── 对话历史 ───────────────────────────

def append_message(session_id: str, role: str, content: str) -> None:
    with _LOCK:
        conn = get_conn()
        conn.execute(
            "INSERT INTO messages(session_id, role, content, created_at) VALUES (?,?,?,?)",
            (session_id, role, content, _now()),
        )
        conn.commit()


def recent_messages(session_id: str, limit: int = 12) -> list[dict]:
    with _LOCK:
        rows = get_conn().execute(
            "SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_session(session_id: str) -> int:
    with _LOCK:
        conn = get_conn()
        cur = conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM session_summary WHERE session_id=?", (session_id,))
        conn.commit()
        return cur.rowcount


def list_sessions(limit: int = 20) -> list[dict]:
    with _LOCK:
        rows = get_conn().execute(
            """SELECT session_id, COUNT(*) AS n, MAX(created_at) AS last_at
               FROM messages GROUP BY session_id ORDER BY last_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── 会话摘要 ───────────────────────────

def save_summary(session_id: str, summary: str, key_info: str) -> None:
    with _LOCK:
        conn = get_conn()
        conn.execute(
            """INSERT INTO session_summary(session_id, summary, key_info, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(session_id) DO UPDATE SET
                 summary=excluded.summary, key_info=excluded.key_info, updated_at=excluded.updated_at""",
            (session_id, summary, key_info, _now()),
        )
        conn.commit()


def load_summary(session_id: str) -> dict:
    with _LOCK:
        row = get_conn().execute(
            "SELECT summary, key_info, updated_at FROM session_summary WHERE session_id=?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else {"summary": "", "key_info": "", "updated_at": ""}


# ─────────────────────────── 调用监控 ───────────────────────────

# 每百万 token 的估算单价（人民币），只用于大致估算成本，可在 .env 之外改这里
PRICE_TABLE = {
    "deepseek:deepseek-chat": {"input": 2.0, "output": 8.0},
    "deepseek:deepseek-reasoner": {"input": 4.0, "output": 16.0},
}


def estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = PRICE_TABLE.get(f"{provider}:{model}")
    if not price:
        return 0.0
    return round((prompt_tokens * price["input"] + completion_tokens * price["output"]) / 1_000_000, 6)


def log_call(provider: str, model: str, agent: str, session_id: str,
             prompt_tokens: int, completion_tokens: int, duration_ms: int,
             success: bool, error: str = "") -> None:
    cost = estimate_cost(provider, model, prompt_tokens, completion_tokens)
    with _LOCK:
        conn = get_conn()
        conn.execute(
            """INSERT INTO llm_calls(provider, model, agent, session_id, prompt_tokens,
               completion_tokens, total_tokens, cost, duration_ms, success, error, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (provider, model, agent, session_id, prompt_tokens, completion_tokens,
             prompt_tokens + completion_tokens, cost, duration_ms, 1 if success else 0,
             (error or "")[:200], _now()),
        )
        conn.commit()


def stats(hours: int = 24) -> dict:
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    with _LOCK:
        conn = get_conn()
        row = conn.execute(
            """SELECT COUNT(*) AS calls,
                      COALESCE(SUM(success),0) AS ok,
                      COALESCE(SUM(cost),0) AS cost,
                      COALESCE(AVG(NULLIF(duration_ms,0)),0) AS dur
               FROM llm_calls WHERE created_at >= ?""",
            (cutoff,),
        ).fetchone()
        by_provider = conn.execute(
            """SELECT provider, COUNT(*) AS n, COALESCE(SUM(cost),0) AS cost
               FROM llm_calls WHERE created_at >= ? GROUP BY provider""",
            (cutoff,),
        ).fetchall()
        by_agent = conn.execute(
            """SELECT agent, COUNT(*) AS n FROM llm_calls WHERE created_at >= ?
               GROUP BY agent ORDER BY n DESC""",
            (cutoff,),
        ).fetchall()
    return {
        "calls": row["calls"], "success": row["ok"], "failed": row["calls"] - row["ok"],
        "cost": round(row["cost"], 4), "avg_duration_ms": round(row["dur"]),
        "by_provider": {r["provider"]: {"calls": r["n"], "cost": round(r["cost"], 4)} for r in by_provider},
        "by_agent": {r["agent"]: r["n"] for r in by_agent},
    }


def recent_calls(limit: int = 20) -> list[dict]:
    with _LOCK:
        rows = get_conn().execute(
            "SELECT provider, model, agent, duration_ms, success, error, created_at "
            "FROM llm_calls ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def daily_stats(days: int = 7) -> list[dict]:
    cutoff = (datetime.now() - timedelta(days=days)).date().isoformat()
    with _LOCK:
        rows = get_conn().execute(
            """SELECT substr(created_at,1,10) AS day, COUNT(*) AS calls,
                      COALESCE(SUM(cost),0) AS cost
               FROM llm_calls WHERE substr(created_at,1,10) >= ?
               GROUP BY day ORDER BY day""",
            (cutoff,),
        ).fetchall()
    return [{"day": r["day"], "calls": r["calls"], "cost": round(r["cost"], 4)} for r in rows]


def db_health() -> tuple[bool, str]:
    try:
        get_conn().execute("SELECT 1").fetchone()
        return True, f"SQLite 正常（{db_path()}）"
    except sqlite3.Error as exc:
        return False, f"SQLite 异常：{exc}"


# ─────────────────────────── 轻量向量检索 ───────────────────────────

def _vector_path() -> str:
    return os.path.join(DATA_DIR, "vector.json")


def save_vector_entry(text: str, vector: list[float], meta: dict | None = None) -> None:
    path = _vector_path()
    store = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                store = json.load(fh)
        except (OSError, json.JSONDecodeError):
            store = []
    store.append({"text": text, "vector": vector, "meta": meta or {}, "ts": time.time()})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(store[-2000:], fh, ensure_ascii=False)


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)
