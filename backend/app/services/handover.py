from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_shifts() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())


def list_batches(owner_shift_id: int | None = None) -> list[dict]:
    with connect() as conn:
        if owner_shift_id is None:
            rows = conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b
                LEFT JOIN shifts s ON s.id = b.owner_shift_id
                ORDER BY b.id
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b
                LEFT JOIN shifts s ON s.id = b.owner_shift_id
                WHERE b.owner_shift_id = ?
                ORDER BY b.id
                """,
                (owner_shift_id,),
            ).fetchall()
        return rows_to_dicts(rows)


def list_handovers() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            ORDER BY h.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def get_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            WHERE h.id = ?
            """,
            (handover_id,),
        ).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        items = conn.execute(
            """
            SELECT b.*
            FROM handover_items hi
            JOIN batches b ON b.id = hi.batch_id
            WHERE hi.handover_id = ?
            ORDER BY b.id
            """,
            (handover_id,),
        ).fetchall()
        out = dict(h)
        out["items"] = rows_to_dicts(items)
        return out


def dashboard() -> dict:
    with connect() as conn:
        shifts = rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())
        inflight = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE status = '在途'"
        ).fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM handovers WHERE status = '进行中'"
        ).fetchone()["c"]
        last = conn.execute(
            "SELECT * FROM handovers ORDER BY id DESC LIMIT 1"
        ).fetchone()
        # 今日交入/交出：由流转账本查询时聚合派生，不另维护计数器
        start, end = _period(None, None)
        flows = {f["shift_id"]: f for f in _flow_summary(conn, start, end)}
        for s in shifts:
            f = flows[s["id"]]
            s["今日交入"] = f["交入"]
            s["今日交出"] = f["交出"]
            s["今日净流量"] = f["净流量"]
        return {
            "班次数": len(shifts),
            "在途批次数": inflight,
            "进行中交班": active,
            "最近交班": dict(last) if last else None,
            "班次": shifts,
        }


def start_handover(from_shift_id: int, to_shift_id: int) -> dict:
    if from_shift_id == to_shift_id:
        raise HTTPException(400, "交班双方不能是同一班次")
    with connect() as conn:
        fs = conn.execute("SELECT * FROM shifts WHERE id = ?", (from_shift_id,)).fetchone()
        ts = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not fs or not ts:
            raise HTTPException(404, "班次不存在")
        active = conn.execute(
            "SELECT id FROM handovers WHERE status = '进行中' LIMIT 1"
        ).fetchone()
        if active:
            raise HTTPException(409, f"已有进行中的交班单#{active['id']}，禁止并行交班")
        batches = conn.execute(
            "SELECT id FROM batches WHERE owner_shift_id = ? AND status = '在途' ORDER BY id",
            (from_shift_id,),
        ).fetchall()
        if not batches:
            raise HTTPException(400, "交班方没有在途批次可交接")
        cur = conn.execute(
            """
            INSERT INTO handovers(from_shift_id, to_shift_id, status, batch_count, started_at)
            VALUES (?, ?, '进行中', ?, ?)
            """,
            (from_shift_id, to_shift_id, len(batches), _now()),
        )
        hid = cur.lastrowid
        for b in batches:
            conn.execute(
                "INSERT INTO handover_items(handover_id, batch_id) VALUES (?, ?)",
                (hid, b["id"]),
            )
        conn.execute("UPDATE shifts SET status = '交班中' WHERE id = ?", (from_shift_id,))
        conn.execute("UPDATE shifts SET status = '接班中' WHERE id = ?", (to_shift_id,))
        conn.commit()
    return get_handover(hid)


def complete_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        if h["status"] != "进行中":
            raise HTTPException(400, "只能完成进行中的交班单")
        items = conn.execute(
            """
            SELECT hi.batch_id, b.biz_key
            FROM handover_items hi
            JOIN batches b ON b.id = hi.batch_id
            WHERE hi.handover_id = ?
            ORDER BY hi.batch_id
            """,
            (handover_id,),
        ).fetchall()
        before = len(items)
        now = _now()
        moved = []
        # 交班完成：整批过户，禁止只改交班单状态不改归属；
        # 每一笔过户与流转账本行同一事务追加，禁止先改归属再异步补账本
        for it in items:
            cur = conn.execute(
                "UPDATE batches SET owner_shift_id = ? WHERE id = ? AND status = '在途'",
                (h["to_shift_id"], it["batch_id"]),
            )
            if cur.rowcount == 1:
                moved.append(it)
                conn.execute(
                    """
                    INSERT INTO transfer_ledger(
                      occurred_at, handover_id, biz_key, from_shift_id, to_shift_id, direction
                    ) VALUES (?, ?, ?, ?, ?, '过户')
                    """,
                    (now, handover_id, it["biz_key"], h["from_shift_id"], h["to_shift_id"]),
                )
        after_from = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
            (h["from_shift_id"],),
        ).fetchone()["c"]
        after_to = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
            (h["to_shift_id"],),
        ).fetchone()["c"]
        consistent = 1 if after_from == 0 and after_to >= before else 0
        detail = {
            "交班前在途数": before,
            "交班后交班方在途": after_from,
            "交班后接班方在途": after_to,
        }
        conn.execute(
            """
            UPDATE handovers
            SET status = ?, finished_at = ?, batch_count = ?
            WHERE id = ?
            """,
            ("已完成" if consistent else "失败", now, before, handover_id),
        )
        if consistent:
            conn.execute("UPDATE shifts SET status = '已交班' WHERE id = ?", (h["from_shift_id"],))
            conn.execute("UPDATE shifts SET status = '值班中' WHERE id = ?", (h["to_shift_id"],))
        else:
            # 补偿回拨：同一事务把已过户批次拨回交班方，并追加回拨账本行，
            # 禁止无账本行却归属已变
            for it in moved:
                conn.execute(
                    "UPDATE batches SET owner_shift_id = ? WHERE id = ? AND owner_shift_id = ?",
                    (h["from_shift_id"], it["batch_id"], h["to_shift_id"]),
                )
                conn.execute(
                    """
                    INSERT INTO transfer_ledger(
                      occurred_at, handover_id, biz_key, from_shift_id, to_shift_id, direction
                    ) VALUES (?, ?, ?, ?, ?, '回拨')
                    """,
                    (now, handover_id, it["biz_key"], h["to_shift_id"], h["from_shift_id"]),
                )
            conn.execute(
                "UPDATE handovers SET reason = ? WHERE id = ?",
                ("交班后批次归属与清单不一致，已同事务补偿回拨", handover_id),
            )
            conn.execute("UPDATE shifts SET status = '值班中' WHERE id = ?", (h["from_shift_id"],))
            conn.execute("UPDATE shifts SET status = '空闲' WHERE id = ?", (h["to_shift_id"],))
        conn.execute(
            """
            INSERT INTO reconcile_reports(
              handover_id, before_count, after_from_count, after_to_count,
              consistent, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handover_id,
                before,
                after_from,
                after_to,
                consistent,
                json.dumps(detail, ensure_ascii=False),
                now,
            ),
        )
        conn.commit()
    return get_handover(handover_id)


def list_reconcile() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reconcile_reports ORDER BY id DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            d["一致"] = bool(d.pop("consistent"))
            out.append(d)
        return out


def _period(start: str | None, end: str | None) -> tuple[str, str]:
    """时段边界：start 含、end 不含；缺省为今日（UTC）。"""
    today = datetime.now(timezone.utc).date()
    start = start or today.isoformat()
    end = end or (today + timedelta(days=1)).isoformat()
    return start, end


def _flow_summary(conn, start: str, end: str) -> list[dict]:
    """交入/交出/净流量：查询时聚合流转账本派生（择一形态：查询时聚合，
    不建物化日汇总表，交班服务不另维护计数器）。"""
    rows = conn.execute(
        """
        SELECT s.id AS shift_id, s.name AS shift_name,
          (SELECT COUNT(*) FROM transfer_ledger l
            WHERE l.to_shift_id = s.id AND l.occurred_at >= ? AND l.occurred_at < ?) AS inflow,
          (SELECT COUNT(*) FROM transfer_ledger l
            WHERE l.from_shift_id = s.id AND l.occurred_at >= ? AND l.occurred_at < ?) AS outflow
        FROM shifts s
        ORDER BY s.id
        """,
        (start, end, start, end),
    ).fetchall()
    return [
        {
            "shift_id": r["shift_id"],
            "shift_name": r["shift_name"],
            "交入": r["inflow"],
            "交出": r["outflow"],
            "净流量": r["inflow"] - r["outflow"],
        }
        for r in rows
    ]


def flow_summary(start: str | None = None, end: str | None = None) -> dict:
    start, end = _period(start, end)
    with connect() as conn:
        return {"start": start, "end": end, "rows": _flow_summary(conn, start, end)}


def list_ledger(
    handover_id: int | None = None,
    biz_key: str | None = None,
    day: str | None = None,
) -> list[dict]:
    sql = """
        SELECT l.*, fs.name AS from_shift_name, ts.name AS to_shift_name
        FROM transfer_ledger l
        JOIN shifts fs ON fs.id = l.from_shift_id
        JOIN shifts ts ON ts.id = l.to_shift_id
    """
    cond: list[str] = []
    args: list = []
    if handover_id is not None:
        cond.append("l.handover_id = ?")
        args.append(handover_id)
    if biz_key:
        cond.append("l.biz_key = ?")
        args.append(biz_key)
    if day:
        cond.append("substr(l.occurred_at, 1, 10) = ?")
        args.append(day)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY l.id DESC"
    with connect() as conn:
        return rows_to_dicts(conn.execute(sql, args).fetchall())


def run_reconcile(sample_size: int = 10) -> dict:
    """抽查对账：抽账本过户行，沿业务键的账本链追到链尾，
    链尾指向的归属必须能解释批次表现状（接班方在途 / 后续结清 / 后续再交），
    解释不了则物化差异到 reconcile_diffs（只追加，禁止删改账本抹平）。"""
    with connect() as conn:
        sampled = conn.execute(
            "SELECT * FROM transfer_ledger ORDER BY RANDOM() LIMIT ?", (sample_size,)
        ).fetchall()
        diffs: list[dict] = []
        seen: set[str] = set()
        for row in sampled:
            biz = row["biz_key"]
            if biz in seen:
                continue
            seen.add(biz)
            tail = conn.execute(
                "SELECT * FROM transfer_ledger WHERE biz_key = ? ORDER BY id DESC LIMIT 1",
                (biz,),
            ).fetchone()
            b = conn.execute(
                "SELECT * FROM batches WHERE biz_key = ?", (biz,)
            ).fetchone()
            if not b:
                diffs.append(
                    {
                        "biz_key": biz,
                        "handover_id": row["handover_id"],
                        "expected_shift_id": tail["to_shift_id"],
                        "actual_shift_id": None,
                        "reason": "批次表查无此业务键，账本链无法解释",
                    }
                )
            elif b["owner_shift_id"] != tail["to_shift_id"]:
                diffs.append(
                    {
                        "biz_key": biz,
                        "handover_id": tail["handover_id"],
                        "expected_shift_id": tail["to_shift_id"],
                        "actual_shift_id": b["owner_shift_id"],
                        "reason": (
                            f"账本链尾单号#{tail['handover_id']}指向班次#{tail['to_shift_id']}，"
                            f"实际归属班次#{b['owner_shift_id']}（{b['status']}），"
                            "在途/结清/再交链均无法解释"
                        ),
                    }
                )
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO reconcile_runs(sample_size, checked_count, diff_count, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (sample_size, len(sampled), len(diffs), now),
        )
        run_id = cur.lastrowid
        for d in diffs:
            conn.execute(
                """
                INSERT INTO reconcile_diffs(
                  run_id, biz_key, handover_id, expected_shift_id, actual_shift_id, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    d["biz_key"],
                    d["handover_id"],
                    d["expected_shift_id"],
                    d["actual_shift_id"],
                    d["reason"],
                    now,
                ),
            )
        conn.commit()
    return {
        "id": run_id,
        "sample_size": sample_size,
        "checked_count": len(sampled),
        "diff_count": len(diffs),
        "差异": diffs,
    }


def list_reconcile_runs() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(
            conn.execute("SELECT * FROM reconcile_runs ORDER BY id DESC").fetchall()
        )


def list_reconcile_diffs(run_id: int | None = None) -> list[dict]:
    sql = """
        SELECT d.*,
               es.name AS expected_shift_name,
               ac.name AS actual_shift_name
        FROM reconcile_diffs d
        LEFT JOIN shifts es ON es.id = d.expected_shift_id
        LEFT JOIN shifts ac ON ac.id = d.actual_shift_id
    """
    args: list = []
    if run_id is not None:
        sql += " WHERE d.run_id = ?"
        args.append(run_id)
    sql += " ORDER BY d.id DESC"
    with connect() as conn:
        return rows_to_dicts(conn.execute(sql, args).fetchall())
