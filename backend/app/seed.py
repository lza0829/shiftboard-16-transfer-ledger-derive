from __future__ import annotations

import json
from pathlib import Path

from app.db import FIXTURES_DIR, connect


SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  keeper TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '空闲'
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  biz_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_shift_id INTEGER REFERENCES shifts(id),
  status TEXT NOT NULL DEFAULT '在途',
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS handovers (
  id INTEGER PRIMARY KEY,
  from_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  to_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  status TEXT NOT NULL,
  batch_count INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS handover_items (
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  PRIMARY KEY (handover_id, batch_id)
);
CREATE TABLE IF NOT EXISTS reconcile_reports (
  id INTEGER PRIMARY KEY,
  handover_id INTEGER REFERENCES handovers(id),
  before_count INTEGER NOT NULL,
  after_from_count INTEGER NOT NULL,
  after_to_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
-- 流转账本（择一形态：独立只追加账本表，由过户事务同事务写入；不采用按日分区表）
CREATE TABLE IF NOT EXISTS transfer_ledger (
  id INTEGER PRIMARY KEY,
  occurred_at TEXT NOT NULL,
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  biz_key TEXT NOT NULL,
  from_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  to_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  direction TEXT NOT NULL CHECK (direction IN ('过户', '回拨'))
);
CREATE INDEX IF NOT EXISTS idx_transfer_ledger_biz ON transfer_ledger(biz_key);
CREATE INDEX IF NOT EXISTS idx_transfer_ledger_handover ON transfer_ledger(handover_id);
-- 账本只追加：触发器硬禁改删历史行
CREATE TRIGGER IF NOT EXISTS trg_transfer_ledger_no_update BEFORE UPDATE ON transfer_ledger
BEGIN SELECT RAISE(ABORT, '流转账本只追加，禁止改删历史行'); END;
CREATE TRIGGER IF NOT EXISTS trg_transfer_ledger_no_delete BEFORE DELETE ON transfer_ledger
BEGIN SELECT RAISE(ABORT, '流转账本只追加，禁止改删历史行'); END;
-- 抽查对账运行记录
CREATE TABLE IF NOT EXISTS reconcile_runs (
  id INTEGER PRIMARY KEY,
  sample_size INTEGER NOT NULL,
  checked_count INTEGER NOT NULL,
  diff_count INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
-- 对账差异（择一形态：独立对账差异表，只追加；不采用报告快照列）
CREATE TABLE IF NOT EXISTS reconcile_diffs (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES reconcile_runs(id),
  biz_key TEXT NOT NULL,
  handover_id INTEGER,
  expected_shift_id INTEGER,
  actual_shift_id INTEGER,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_reconcile_diffs_no_update BEFORE UPDATE ON reconcile_diffs
BEGIN SELECT RAISE(ABORT, '对账差异只追加，禁止为抹平而删改'); END;
CREATE TRIGGER IF NOT EXISTS trg_reconcile_diffs_no_delete BEFORE DELETE ON reconcile_diffs
BEGIN SELECT RAISE(ABORT, '对账差异只追加，禁止为抹平而删改'); END;
"""


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS c FROM shifts").fetchone()["c"]
        if n:
            return
        seed_path = FIXTURES_DIR / "seed.json"
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        for s in data["shifts"]:
            conn.execute(
                "INSERT INTO shifts(id, name, keeper, status) VALUES (?, ?, ?, ?)",
                (s["id"], s["name"], s["keeper"], s["status"]),
            )
        for b in data["batches"]:
            conn.execute(
                "INSERT INTO batches(id, biz_key, title, owner_shift_id, status, note) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    b["id"],
                    b["biz_key"],
                    b["title"],
                    b["owner_shift_id"],
                    b["status"],
                    b.get("note", ""),
                ),
            )
        conn.commit()
