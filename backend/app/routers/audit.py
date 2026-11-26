from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list

router = APIRouter(tags=["审计"])


@router.get("/审计")
def list_audit(类别: str | None = None, 关联编号: int | None = None):
    """审计流水按真源事件留痕：名册变更、心跳登记、缺口发现/确认/纠正/闭合均可筛。"""
    sql = (
        "SELECT id AS 编号, kind AS 类别, ref_id AS 关联编号, "
        "action AS 动作, created_at AS 时间, note AS 备注 "
        "FROM audit_events WHERE 1=1"
    )
    params: list = []
    if 类别:
        sql += " AND kind=?"
        params.append(类别)
    if 关联编号 is not None:
        sql += " AND ref_id=?"
        params.append(关联编号)
    sql += " ORDER BY id DESC LIMIT 200"
    with db() as conn:
        return rows_to_list(conn.execute(sql, params).fetchall())
