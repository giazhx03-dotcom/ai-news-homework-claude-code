"""SQLite 持久化：文章、去重键、来源状态、运行记录。（主控维护）"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    guid TEXT,
    title TEXT NOT NULL,
    link TEXT,
    canonical_link TEXT,
    summary TEXT,
    published_raw TEXT,
    published_at TEXT,
    date_status TEXT NOT NULL,
    relevance TEXT NOT NULL,
    category TEXT NOT NULL,
    category_reason TEXT,
    collected_at TEXT NOT NULL,
    report_date TEXT NOT NULL,
    run_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_report_date ON articles(report_date);
CREATE TABLE IF NOT EXISTS article_keys (
    key TEXT PRIMARY KEY,
    article_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    last_success_at TEXT,
    last_window_end TEXT,
    last_attempt_at TEXT,
    last_status TEXT,
    last_error TEXT,
    consecutive_failures INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    report_date TEXT NOT NULL,
    mode TEXT,
    new_count INTEGER DEFAULT 0,
    detail_json TEXT
);
"""


class Store:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------- 去重 ----------
    def existing_key(self, keys: List[str]) -> Optional[str]:
        for k in keys:
            row = self.conn.execute("SELECT article_id FROM article_keys WHERE key=?", (k,)).fetchone()
            if row:
                return row["article_id"]
        return None

    def insert_article(self, art: Dict, keys: List[str]) -> bool:
        """事务内插入文章及其全部去重键；若任一键已存在返回 False（不插入）。"""
        with self.conn:
            if self.existing_key(keys):
                return False
            cols = list(art.keys())
            self.conn.execute(
                f"INSERT INTO articles ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [art[c] for c in cols],
            )
            for k in keys:
                self.conn.execute("INSERT OR IGNORE INTO article_keys(key, article_id) VALUES (?,?)", (k, art["id"]))
        return True

    def articles_for_date(self, report_date: str) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM articles WHERE report_date=? ORDER BY COALESCE(published_at, collected_at) DESC, title",
            (report_date,),
        ).fetchall()
        return [dict(r) for r in rows]

    def report_dates(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT report_date FROM runs UNION SELECT report_date FROM articles ORDER BY 1 DESC"
        ).fetchall()
        return [r[0] for r in rows]

    def count_for_date(self, report_date: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM articles WHERE report_date=?", (report_date,)).fetchone()[0]

    # ---------- 来源状态 ----------
    def get_state(self, source_id: str) -> Optional[Dict]:
        row = self.conn.execute("SELECT * FROM source_state WHERE source_id=?", (source_id,)).fetchone()
        return dict(row) if row else None

    def all_states(self) -> List[Dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM source_state ORDER BY source_id")]

    def record_success(self, source_id: str, attempt_at: str, window_end: Optional[str]):
        """成功：推进 last_success_at / last_window_end（window_end 为 None 表示手动区间，不推进窗口）。"""
        cur = self.get_state(source_id)
        with self.conn:
            if cur is None:
                self.conn.execute(
                    "INSERT INTO source_state(source_id,last_success_at,last_window_end,last_attempt_at,last_status,last_error,consecutive_failures)"
                    " VALUES (?,?,?,?, 'ok', NULL, 0)",
                    (source_id, attempt_at if window_end else None, window_end, attempt_at),
                )
            else:
                if window_end:
                    self.conn.execute(
                        "UPDATE source_state SET last_success_at=?, last_window_end=?, last_attempt_at=?, last_status='ok',"
                        " last_error=NULL, consecutive_failures=0 WHERE source_id=?",
                        (attempt_at, window_end, attempt_at, source_id),
                    )
                else:
                    self.conn.execute(
                        "UPDATE source_state SET last_attempt_at=?, last_status='ok', last_error=NULL WHERE source_id=?",
                        (attempt_at, source_id),
                    )

    def record_failure(self, source_id: str, attempt_at: str, error: str):
        """失败：只记录尝试与错误，绝不推进 last_success_at / last_window_end。"""
        cur = self.get_state(source_id)
        with self.conn:
            if cur is None:
                self.conn.execute(
                    "INSERT INTO source_state(source_id,last_attempt_at,last_status,last_error,consecutive_failures)"
                    " VALUES (?,?, 'failed', ?, 1)",
                    (source_id, attempt_at, error),
                )
            else:
                self.conn.execute(
                    "UPDATE source_state SET last_attempt_at=?, last_status='failed', last_error=?,"
                    " consecutive_failures=COALESCE(consecutive_failures,0)+1 WHERE source_id=?",
                    (attempt_at, error, source_id),
                )

    # ---------- 运行记录 ----------
    def save_run(self, run: Dict):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO runs(run_id,started_at,finished_at,report_date,mode,new_count,detail_json)"
                " VALUES (?,?,?,?,?,?,?)",
                (run["run_id"], run["started_at"], run.get("finished_at"), run["report_date"], run["mode"],
                 run["new_count"], json.dumps(run, ensure_ascii=False)),
            )

    def runs_for_date(self, report_date: str) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT detail_json FROM runs WHERE report_date=? ORDER BY started_at", (report_date,)
        ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def last_runs(self, n: int = 5) -> List[Dict]:
        rows = self.conn.execute("SELECT detail_json FROM runs ORDER BY started_at DESC LIMIT ?", (n,)).fetchall()
        return [json.loads(r[0]) for r in rows]
