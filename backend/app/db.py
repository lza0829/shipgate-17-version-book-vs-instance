from __future__ import annotations
import sqlite3
import threading
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  owner TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '启用' CHECK(status IN ('启用','停用'))
);
CREATE TABLE IF NOT EXISTS environments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  sort_order INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS app_env_versions (
  app_id INTEGER NOT NULL,
  env_id INTEGER NOT NULL,
  version TEXT NOT NULL DEFAULT '',
  updated_at TEXT,
  PRIMARY KEY(app_id, env_id),
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  serial TEXT NOT NULL UNIQUE,
  app_id INTEGER NOT NULL,
  target_version TEXT NOT NULL,
  artifact_digest TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '待入闸'
    CHECK(status IN ('待入闸','已入闸','晋升中','已放行','回滚中','已回滚','已关闭')),
  pipeline_status TEXT NOT NULL DEFAULT '未回执'
    CHECK(pipeline_status IN ('未回执','绿','红')),
  current_env_id INTEGER,
  canary_ratio INTEGER NOT NULL DEFAULT 10,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  admitted_at TEXT,
  released_at TEXT,
  rolled_back_at TEXT,
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(current_env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS promote_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  release_id INTEGER NOT NULL,
  from_env_id INTEGER,
  to_env_id INTEGER NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('放行','拒绝')),
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(release_id) REFERENCES releases(id),
  FOREIGN KEY(from_env_id) REFERENCES environments(id),
  FOREIGN KEY(to_env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS release_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env_id INTEGER NOT NULL,
  open_at TEXT NOT NULL,
  close_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '窗开' CHECK(status IN ('窗开','窗关')),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS rollback_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env_id INTEGER NOT NULL,
  open_at TEXT NOT NULL,
  close_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '窗开' CHECK(status IN ('窗开','窗关')),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id INTEGER NOT NULL,
  env_id INTEGER NOT NULL,
  instance_key TEXT NOT NULL DEFAULT '主实例',
  version TEXT NOT NULL,
  health TEXT NOT NULL DEFAULT '探针绿' CHECK(health IN ('探针绿','探针红')),
  release_id INTEGER,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id),
  FOREIGN KEY(release_id) REFERENCES releases(id)
);
-- 实例心跳流水：只增不改，是实例册（探活附件）的登记通道，永不写名册
CREATE TABLE IF NOT EXISTS instance_heartbeats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id INTEGER NOT NULL,
  env_id INTEGER NOT NULL,
  instance_key TEXT NOT NULL,
  version TEXT NOT NULL,
  health TEXT NOT NULL,
  release_id INTEGER,
  source TEXT NOT NULL DEFAULT '心跳',
  created_at TEXT NOT NULL,
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id),
  FOREIGN KEY(release_id) REFERENCES releases(id)
);
-- 名册（真源）与实例心跳汇总之间的缺口台账：半截双账必有一行可核
CREATE TABLE IF NOT EXISTS version_gaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  app_id INTEGER NOT NULL,
  env_id INTEGER NOT NULL,
  registry_version TEXT NOT NULL DEFAULT '',
  observed_summary TEXT NOT NULL DEFAULT '',
  gap_kind TEXT NOT NULL DEFAULT ''
    CHECK(gap_kind IN ('','实例缺失','实例未达版本','实例版本混杂')),
  status TEXT NOT NULL DEFAULT '未确认'
    CHECK(status IN ('未确认','已确认','已纠正','已闭合')),
  detected_at TEXT NOT NULL,
  acknowledged_at TEXT,
  closed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  UNIQUE(app_id, env_id),
  FOREIGN KEY(app_id) REFERENCES apps(id),
  FOREIGN KEY(env_id) REFERENCES environments(id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  ref_id INTEGER,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
"""

# 旧库增量迁移：新列不存在则补
COLUMN_MIGRATIONS = (
    ("instances", "instance_key", "TEXT NOT NULL DEFAULT '主实例'"),
    ("app_env_versions", "expected_release_id", "INTEGER"),
)

# 依赖迁移后新列的索引，须在 COLUMN_MIGRATIONS 之后创建
POST_MIGRATION_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_instances_cell_key "
    "ON instances(app_id, env_id, instance_key)",
)

RELEASE_STATUSES = ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭")
PIPELINE_STATUSES = ("未回执", "绿", "红")
HEALTH_STATUSES = ("探针绿", "探针红")
WINDOW_STATUSES = ("窗开", "窗关")
GAP_STATUSES = ("未确认", "已确认", "已纠正", "已闭合")
GAP_KINDS = ("实例缺失", "实例未达版本", "实例版本混杂")

# 单进程写锁：晋升（名册通道）与心跳（实例通道）串行落账，
# 任何并发交错都只能看到「改前」或「改后」，不会出现半截双账。
_write_lock = threading.RLock()


def write_lock() -> threading.RLock:
    return _write_lock


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db():
    conn = connect()
    with _write_lock:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        for table, column, decl in COLUMN_MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        # 旧库同格可能有多版本实例行，默认实例键会撞唯一索引：按 id 顺序补实例键
        dup_cells = conn.execute(
            "SELECT app_id, env_id FROM instances GROUP BY app_id, env_id HAVING COUNT(*) > 1"
        ).fetchall()
        for cell in dup_cells:
            rows = conn.execute(
                "SELECT id, instance_key FROM instances WHERE app_id=? AND env_id=? ORDER BY id",
                (cell["app_id"], cell["env_id"]),
            ).fetchall()
            for idx, r in enumerate(rows):
                key = "主实例" if idx == 0 and r["instance_key"] == "主实例" else (
                    r["instance_key"] if idx == 0 else f"实例{idx + 1}"
                )
                if r["instance_key"] != key:
                    conn.execute("UPDATE instances SET instance_key=? WHERE id=?", (key, r["id"]))
        for stmt in POST_MIGRATION_INDEXES:
            conn.execute(stmt)


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]
