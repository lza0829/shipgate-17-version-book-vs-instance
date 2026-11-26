from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from app.db import db, rows_to_list
from app.services.versioning import (
    acknowledge_gap,
    all_cells,
    cell_read_model,
    correct_gap,
    recompute_gap,
)

router = APIRouter(tags=["对账"])


class GapActionIn(BaseModel):
    备注: str = ""


@router.get("/对账")
def reconcile():
    """对账只认一套账：名册版本（真源）对实例心跳汇总（附件）。

    双账不允许长期存在：每个不一致都有缺口行、缺口类型与确认/纠正入口。
    """
    with db() as conn:
        release_stats = {
            "申请数": conn.execute("SELECT COUNT(*) AS c FROM releases").fetchone()["c"],
            "已放行": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已放行'"
            ).fetchone()["c"],
            "已回滚": conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status='已回滚'"
            ).fetchone()["c"],
            "在途": conn.execute(
                """
                SELECT COUNT(*) AS c FROM releases
                WHERE status IN ('已入闸','晋升中','已放行','回滚中')
                """
            ).fetchone()["c"],
        }
        cells = all_cells(conn)
        version_vs_instance = [
            {
                "应用": c["应用"],
                "应用编号": c["应用编号"],
                "环境": c["环境"],
                "环境编号": c["环境编号"],
                "环境名": c["环境名"],
                "名册版本": c["名册版本"],
                "实例汇总版本": c["实例汇总版本"] or "",
                "实例版本们": c["实例版本们"] or "-",
                "实例数": c["实例数"],
                "红灯数": c["红灯数"],
                "缺口类型": c["缺口类型"],
                "缺口状态": c["缺口状态"] or "",
                "缺口说明": c["缺口说明"],
                "缺口编号": c["缺口编号"],
                "可纠正": c["可纠正"],
                "一致": c["一致"],
                "最近心跳": c["最近心跳"],
            }
            for c in cells
        ]
        gaps = [
            {
                "缺口编号": c["缺口编号"],
                "应用": c["应用"],
                "环境": c["环境"],
                "名册版本": c["名册版本"],
                "实例汇总版本": c["实例汇总版本"] or "",
                "实例版本们": c["实例版本们"],
                "缺口类型": c["缺口类型"],
                "缺口状态": c["缺口状态"],
                "缺口说明": c["缺口说明"],
                "可纠正": c["可纠正"],
            }
            for c in cells
            if c["缺口状态"]
        ]
        promote_count = conn.execute("SELECT COUNT(*) AS c FROM promote_events").fetchone()["c"]
        return {
            "发版统计": release_stats,
            "晋升事件数": promote_count,
            "名册与实例": version_vs_instance,
            "缺口台账": gaps,
            "开口缺口数": len(gaps),
        }


@router.post("/缺口/{gap_id}/确认")
def acknowledge(gap_id: int, body: GapActionIn):
    with db() as conn:
        return acknowledge_gap(conn, gap_id, body.备注)


@router.post("/缺口/{gap_id}/纠正")
def correct(gap_id: int, body: GapActionIn):
    # 仅实例全员同版本（实例未达版本）时可纠正名册；混杂/缺失一律拒绝盖真源
    with db() as conn:
        return correct_gap(conn, gap_id, body.备注)


@router.get("/缺口")
def list_gaps():
    with db() as conn:
        rows = conn.execute(
            """
            SELECT g.id AS 缺口编号, a.name AS 应用, e.code AS 环境,
                   g.registry_version AS 名册版本, g.observed_summary AS 实例版本们,
                   g.gap_kind AS 缺口类型, g.status AS 缺口状态,
                   g.detected_at AS 发现时间, g.acknowledged_at AS 确认时间,
                   g.closed_at AS 闭环时间, g.note AS 备注
            FROM version_gaps g
            JOIN apps a ON a.id = g.app_id
            JOIN environments e ON e.id = g.env_id
            ORDER BY g.id DESC
            """
        ).fetchall()
        result = rows_to_list(rows)
        for r in result:
            r["可纠正"] = r["缺口类型"] == "实例未达版本" and r["缺口状态"] in ("未确认", "已确认")
        return result


@router.get("/应用/{app_id}/环境/{env_id}/版本读模型")
def one_cell(app_id: int, env_id: int):
    with db() as conn:
        return cell_read_model(conn, app_id, env_id)


class RecomputeIn(BaseModel):
    应用编号: int
    环境编号: int


@router.post("/对账/重算")
def recompute(body: RecomputeIn):
    """对账纠偏入口：按名册真源与实例心跳事实重算缺口（半截双账可单独复现/复核）。"""
    with db() as conn:
        if not conn.execute("SELECT id FROM apps WHERE id=?", (body.应用编号,)).fetchone():
            from fastapi import HTTPException
            raise HTTPException(404, "应用不存在")
        if not conn.execute("SELECT id FROM environments WHERE id=?", (body.环境编号,)).fetchone():
            from fastapi import HTTPException
            raise HTTPException(404, "环境不存在")
        recompute_gap(conn, body.应用编号, body.环境编号)
        return cell_read_model(conn, body.应用编号, body.环境编号)
