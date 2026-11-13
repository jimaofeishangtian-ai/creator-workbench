from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


DEFAULTS = {
    "creator_domain": {"configured":False,"name":"","include_keywords":[],"exclude_keywords":[],"prefer_low_followers":True}, "traffic_tags": [],
    "profile": "尚未完成领域访谈；请先让Agent了解你的行业、受众和赛道。",
    "keywords": [],
    "model": "deepseek-flash",
    "categories": ["教程方法","案例效果","产品工具","经验观点","趋势资讯","其他"],
    "purposes": ["选题候选", "开头参考", "结构参考", "演示参考", "待验证方法", "画面钩子", "分镜参考", "商单参考", "提示词"],
    "states": ["待看", "备选", "已选题", "已用过", "已归档"],
    "budgets": {"total": 300, "data": 200, "model": 80, "reserve": 20},
    "low_followers": 50000, "low_min_likes": 1000, "low_ratio": 0.25,
    "hotspots": {"enabled": False, "min_likes": 10000},
    "hotspot_state": {}, "hotspot_selection_cache": {},
    "usd_cny": 8.0,
    "pricing": {"data_request_usd": 0.001, "search_request_usd": 0.01, "model_input_cny_million": 20, "model_output_cny_million": 40, "confirmed": False},
    "data_pricing_confirmed": False,
    "automation": {"enabled": False, "favorites_minutes": 1440, "creator_minutes": 1440, "backfill_enabled": False},
    "whisper_model": "small", "whisper_device": "auto", "transcription_download_approved": False,
    "last_sync": {}, "collection_state": {}, "favorites_checkpoint": [], "backfill_cursor": 0,
    "favorites_transport": "existing_edge",
    "remote_access": {"url": "", "allowed_device": ""},
    "knowledge": {"enabled": False, "vault_path": "", "folder": "08-自媒体知识库", "weekday": 6, "hour": 21, "enabled_at": None},
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auth (id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, expires REAL NOT NULL, csrf TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS browser_pairing (id INTEGER PRIMARY KEY CHECK(id=1), code_hash TEXT NOT NULL, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS browser_bridge (id INTEGER PRIMARY KEY CHECK(id=1), token_hash TEXT NOT NULL, extension_id TEXT NOT NULL, created_at TEXT NOT NULL, last_seen TEXT);
CREATE TABLE IF NOT EXISTS browser_requests (job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE, mode TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', lease_until REAL NOT NULL DEFAULT 0, nonce TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS creators (id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL, followers INTEGER, enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT);
CREATE TABLE IF NOT EXISTS author_profiles (id TEXT PRIMARY KEY, name TEXT NOT NULL, followers INTEGER, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS author_follower_history (id INTEGER PRIMARY KEY, author_id TEXT NOT NULL, observed_at TEXT NOT NULL, followers INTEGER NOT NULL, source TEXT NOT NULL, UNIQUE(author_id,observed_at,followers));
CREATE INDEX IF NOT EXISTS follower_history_author ON author_follower_history(author_id,observed_at DESC);
CREATE TABLE IF NOT EXISTS recommendation_records (id INTEGER PRIMARY KEY, record_key TEXT NOT NULL UNIQUE, author_id TEXT NOT NULL, video_id TEXT NOT NULL, shown_at TEXT NOT NULL, followers INTEGER, followers_observed_at TEXT);
CREATE INDEX IF NOT EXISTS recommendation_author ON recommendation_records(author_id,id DESC);
CREATE TABLE IF NOT EXISTS author_profile_refreshes (author_id TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, error_code TEXT);
CREATE TABLE IF NOT EXISTS videos (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, author TEXT NOT NULL DEFAULT '', author_id TEXT,
 url TEXT NOT NULL, cover TEXT, published_at TEXT, collected_at TEXT, discovered_at TEXT NOT NULL,
 duration REAL, media_kind TEXT NOT NULL DEFAULT 'video', followers INTEGER, followers_at TEXT, likes INTEGER, comments INTEGER, favorites INTEGER, shares INTEGER,
 metrics_at TEXT, category TEXT NOT NULL DEFAULT '其他', is_ai INTEGER, confidence REAL,
 tools TEXT NOT NULL DEFAULT '[]', purposes TEXT NOT NULL DEFAULT '[]', state TEXT NOT NULL DEFAULT '待看',
 manual_classification INTEGER NOT NULL DEFAULT 0, classification_method TEXT NOT NULL DEFAULT '初步分类',
 topic_key TEXT, fit REAL, feasibility REAL, recommendation TEXT, notes TEXT NOT NULL DEFAULT '',
 media_url TEXT, subtitle_url TEXT, raw_hash TEXT
);
CREATE TABLE IF NOT EXISTS sources (video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE, kind TEXT NOT NULL, source_key TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL, PRIMARY KEY(video_id,kind,source_key));
CREATE TABLE IF NOT EXISTS favorite_order (video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE, sampled_at TEXT NOT NULL, likes INTEGER, comments INTEGER, favorites INTEGER, shares INTEGER, followers INTEGER);
CREATE INDEX IF NOT EXISTS snapshots_video ON snapshots(video_id,sampled_at);
CREATE TABLE IF NOT EXISTS transcripts (video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE, text TEXT NOT NULL, segments TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL, content_hash TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analyses (cache_key TEXT PRIMARY KEY, video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE CASCADE, kind TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, video_id TEXT REFERENCES videos(id), title TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '选开头', duration INTEGER NOT NULL DEFAULT 120, revision INTEGER NOT NULL DEFAULT 1, hooks TEXT NOT NULL DEFAULT '[]', selected_hook TEXT NOT NULL DEFAULT '', content TEXT NOT NULL DEFAULT '', script_kind TEXT NOT NULL DEFAULT '完整脚本', shooting_plan TEXT NOT NULL DEFAULT '[]', verification TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_versions (id INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE, revision INTEGER NOT NULL, payload TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(project_id,revision));
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '', result TEXT, error_code TEXT, attempts INTEGER NOT NULL DEFAULT 0, dedup_key TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS active_job_key ON jobs(dedup_key) WHERE status IN ('queued','running') AND dedup_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS expenses (id TEXT PRIMARY KEY, month TEXT NOT NULL, provider TEXT NOT NULL, kind TEXT NOT NULL, reserved REAL NOT NULL, actual REAL, status TEXT NOT NULL, usage TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schedule_runs (slot TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_items (video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE, priority INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'indexed', payload TEXT NOT NULL DEFAULT '{}', error TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_exports (path TEXT PRIMARY KEY, generated_hash TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_reports (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL);
PRAGMA user_version=1;
CREATE TABLE IF NOT EXISTS discovery_feedback (
 video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
 viewed_at TEXT, dismissed_at TEXT, reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate_saves (
 video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE, saved_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, directory: Path):
        self.root = directory.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workbench.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as c:
            c.executescript(SCHEMA)
            if "media_kind" not in {r[1] for r in c.execute("PRAGMA table_info(videos)")}:
                c.execute("ALTER TABLE videos ADD COLUMN media_kind TEXT NOT NULL DEFAULT 'video'")
            if "followers_at" not in {r[1] for r in c.execute("PRAGMA table_info(videos)")}:
                c.execute("ALTER TABLE videos ADD COLUMN followers_at TEXT")
            if "verified_items" not in {r[1] for r in c.execute("PRAGMA table_info(projects)")}:
                c.execute("ALTER TABLE projects ADD COLUMN verified_items TEXT NOT NULL DEFAULT '[]'")
            for column, declaration in {"creation_mode": "TEXT NOT NULL DEFAULT 'tutorial'", "brief": "TEXT NOT NULL DEFAULT '{}'", "reference_ids": "TEXT NOT NULL DEFAULT '[]'", "reference_analysis": "TEXT NOT NULL DEFAULT '{}'"}.items():
                if column not in {r[1] for r in c.execute("PRAGMA table_info(projects)")}:
                    c.execute(f"ALTER TABLE projects ADD COLUMN {column} {declaration}")
            if "content_tracks" not in {r[1] for r in c.execute("PRAGMA table_info(videos)")}:
                c.execute("ALTER TABLE videos ADD COLUMN content_tracks TEXT NOT NULL DEFAULT '[]'")
            if "collection_group" not in {r[1] for r in c.execute("PRAGMA table_info(videos)")}:
                c.execute("ALTER TABLE videos ADD COLUMN collection_group TEXT NOT NULL DEFAULT ''")
            for key, value in DEFAULTS.items():
                c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, encode(value)))
            # Import only explicitly timestamped counts, never publication/metrics times.
            c.execute("INSERT OR IGNORE INTO author_follower_history (author_id,observed_at,followers,source) SELECT id,updated_at,followers,'已有作者资料采样' FROM author_profiles WHERE followers IS NOT NULL AND updated_at IS NOT NULL")
            c.execute("INSERT OR IGNORE INTO author_follower_history (author_id,observed_at,followers,source) SELECT id,updated_at,followers,'已有对标资料采样' FROM creators WHERE followers IS NOT NULL AND updated_at IS NOT NULL")
            c.execute("INSERT OR IGNORE INTO author_follower_history (author_id,observed_at,followers,source) SELECT DISTINCT author_id,followers_at,followers,'已有作品关联资料采样' FROM videos WHERE author_id IS NOT NULL AND author_id!='' AND followers IS NOT NULL AND followers_at IS NOT NULL")
            if not c.execute("SELECT 1 FROM favorite_order LIMIT 1").fetchone():
                # Legacy data has no explicit page order: retain first-discovery
                # order behind the head whose page order is actually known.
                favorites = [r[0] for r in c.execute("SELECT video_id FROM sources WHERE kind='favorites' GROUP BY video_id ORDER BY MIN(rowid)")]
                valid = set(favorites)
                head = json.loads(c.execute("SELECT value FROM settings WHERE key='favorites_checkpoint'").fetchone()[0])
                ordered = list(dict.fromkeys([x for x in head if x in valid] + favorites))
                c.executemany("INSERT INTO favorite_order VALUES (?,?)", [(identity, index) for index, identity in enumerate(ordered)])

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def settings(self):
        with self.connect() as c:
            return {r["key"]: json.loads(r["value"]) for r in c.execute("SELECT * FROM settings")}

    def set(self, key, value):
        with self.connect() as c:
            c.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, encode(value)))

    def one(self, sql, args=()):
        with self.connect() as c:
            row = c.execute(sql, args).fetchone()
            return dict(row) if row else None

    def rows(self, sql, args=()):
        with self.connect() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def run(self, sql, args=()):
        with self.connect() as c:
            return c.execute(sql, args).rowcount


def data_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime"
