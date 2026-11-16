from pathlib import Path
import os
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["DATA_DIR"] = str(ROOT / "backend" / "data_test")
os.environ["FIXTURES_DIR"] = str(ROOT / "fixtures")

db_path = Path(os.environ["DATA_DIR"]) / "app.db"
if db_path.exists():
    db_path.unlink()

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["项目"] == "交班台"


def test_handover_and_reconcile(client):
    shifts = client.get("/api/shifts").json()
    assert len(shifts) >= 2
    batches = client.get("/api/batches", params={"owner_shift_id": 1}).json()
    assert any(b["status"] == "在途" for b in batches)

    started = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 1, "to_shift_id": 2},
    )
    assert started.status_code == 200, started.text
    hid = started.json()["id"]
    assert started.json()["status"] == "进行中"

    # 并行交班应被拒绝
    again = client.post(
        "/api/handovers/start",
        json={"from_shift_id": 1, "to_shift_id": 3},
    )
    assert again.status_code == 409

    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "已完成"

    after = client.get("/api/batches", params={"owner_shift_id": 1}).json()
    assert all(b["status"] != "在途" or b["owner_shift_id"] != 1 for b in after) or not any(
        b["status"] == "在途" for b in after
    )
    to_batches = client.get("/api/batches", params={"owner_shift_id": 2}).json()
    assert sum(1 for b in to_batches if b["status"] == "在途") >= 4

    reports = client.get("/api/reconcile").json()
    assert reports
    assert reports[0]["一致"] is True


def _done_handover_id(client) -> int:
    logs = client.get("/api/handovers").json()
    return next(h["id"] for h in logs if h["status"] == "已完成")


def test_ledger_rows_same_tx_and_flows_derived(client):
    hid = _done_handover_id(client)
    rows = client.get("/api/ledger", params={"handover_id": hid}).json()
    # 每一笔成功过户都有一行同事务账本：时间、单号、业务键、from、to、方向
    assert len(rows) == 4
    assert all(r["direction"] == "过户" for r in rows)
    assert all(r["handover_id"] == hid and r["occurred_at"] for r in rows)
    assert {r["biz_key"] for r in rows} == {
        "B20261115-01",
        "B20261115-02",
        "B20261115-03",
        "B20261114-09",
    }
    assert all(r["from_shift_id"] == 1 and r["to_shift_id"] == 2 for r in rows)

    # 今日交入/交出由账本派生
    flows = client.get("/api/ledger/flows").json()["rows"]
    by_id = {f["shift_id"]: f for f in flows}
    assert by_id[1]["交出"] == 4 and by_id[1]["净流量"] == -4
    assert by_id[2]["交入"] == 4 and by_id[2]["净流量"] == 4
    assert by_id[3]["净流量"] == 0

    # 任意时段净流量同样由账本派生（全部时段）
    all_flows = client.get(
        "/api/ledger/flows", params={"start": "2000-01-01", "end": "2100-01-01"}
    ).json()["rows"]
    assert {f["shift_id"]: f for f in all_flows}[2]["交入"] == 4

    # 总览班次携带账本派生的今日流量
    dash = client.get("/api/dashboard").json()
    s2 = next(s for s in dash["班次"] if s["id"] == 2)
    assert s2["今日交入"] == 4 and s2["今日净流量"] == 4


def test_ledger_append_only(client):
    import sqlite3

    from app.db import connect

    with connect() as conn:
        with pytest.raises(sqlite3.Error):
            conn.execute("UPDATE transfer_ledger SET biz_key = 'X' WHERE id = 1")
        with pytest.raises(sqlite3.Error):
            conn.execute("DELETE FROM transfer_ledger WHERE id = 1")


def test_compensation_reversal_same_tx(client):
    started = client.post(
        "/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3}
    )
    assert started.status_code == 200, started.text
    hid = started.json()["id"]
    items = started.json()["items"]

    # 交班进行中，清单内一批被旁路结清 → 完成时归属不一致，触发补偿回拨
    from app.db import connect

    with connect() as conn:
        conn.execute(
            "UPDATE batches SET status = '已结清' WHERE id = ?", (items[0]["id"],)
        )
        conn.commit()

    done = client.post(f"/api/handovers/{hid}/complete")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "失败"

    rows = client.get("/api/ledger", params={"handover_id": hid}).json()
    fwd = [r for r in rows if r["direction"] == "过户"]
    rev = [r for r in rows if r["direction"] == "回拨"]
    # 每笔已发生的过户都有同事务的补偿回拨行，单号一致、方向相反
    assert len(fwd) == len(rev) == len(items) - 1
    assert {r["biz_key"] for r in fwd} == {r["biz_key"] for r in rev}
    assert all(r["from_shift_id"] == 2 and r["to_shift_id"] == 3 for r in fwd)
    assert all(r["from_shift_id"] == 3 and r["to_shift_id"] == 2 for r in rev)

    # 回拨后归属回到交班方：失败单净流量为 0
    b2 = client.get("/api/batches", params={"owner_shift_id": 2}).json()
    assert sum(1 for b in b2 if b["status"] == "在途") == len(items) - 1
    b3 = client.get("/api/batches", params={"owner_shift_id": 3}).json()
    assert not any(b["status"] == "在途" for b in b3)


def test_reconcile_run_materializes_diffs(client):
    # 当前账本链与归属一致 → 抽查无差异
    r1 = client.post("/api/reconcile/run", json={"sample_size": 50})
    assert r1.status_code == 200, r1.text
    run1 = r1.json()
    assert run1["checked_count"] > 0
    assert run1["diff_count"] == 0

    # 制造撕裂：直接改归属、不落账本（绕过服务层）
    from app.db import connect

    with connect() as conn:
        conn.execute(
            "UPDATE batches SET owner_shift_id = 3 WHERE biz_key = 'B20261115-01'"
        )
        conn.commit()

    r2 = client.post("/api/reconcile/run", json={"sample_size": 50})
    run2 = r2.json()
    assert run2["diff_count"] >= 1

    # 差异物化在独立对账差异表，列出解释不了的业务键
    diffs = client.get("/api/reconcile/diffs", params={"run_id": run2["id"]}).json()
    hit = next(d for d in diffs if d["biz_key"] == "B20261115-01")
    assert hit["expected_shift_id"] == 2
    assert hit["actual_shift_id"] == 3

    runs = client.get("/api/reconcile/runs").json()
    assert runs[0]["id"] == run2["id"]

    # 差异表只追加，禁止为抹平而删改
    import sqlite3

    with connect() as conn:
        with pytest.raises(sqlite3.Error):
            conn.execute("DELETE FROM reconcile_diffs WHERE run_id = ?", (run2["id"],))
