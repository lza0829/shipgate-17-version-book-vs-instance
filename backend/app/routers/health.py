from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list
from app.services.common import audit, now_str
from app.services.versioning import cell_read_model, recompute_gap, register_heartbeat

router = APIRouter(tags=["健康"])


class HealthIn(BaseModel):
    健康: str


class HeartbeatIn(BaseModel):
    应用编号: int
    环境编号: int
    版本: str = Field(min_length=1)
    健康: str = "探针绿"
    实例键: str = "主实例"
    发版单编号: int | None = None


@router.get("/实例")
def list_instances():
    """实例册（探活附件）：逐实例心跳 + 所属环境的名册真源与缺口标。

    名册版本列只来自 app_env_versions，实例行不得盖写。
    """
    with db() as conn:
        rows = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, i.app_id AS 应用编号, i.env_id AS 环境编号,
                       a.name AS 应用, e.code AS 环境, e.name AS 环境名,
                       i.instance_key AS 实例键,
                       i.version AS 版本, i.health AS 健康, i.release_id AS 发版单编号,
                       i.updated_at AS 更新时间
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                ORDER BY a.id, e.sort_order, i.instance_key, i.id
                """
            ).fetchall()
        )
        cells = {}
        for r in rows:
            key = (r["应用编号"], r["环境编号"])
            if key not in cells:
                cells[key] = cell_read_model(conn, *key)
        for r in rows:
            cell = cells[(r["应用编号"], r["环境编号"])]
            r["名册版本"] = cell["名册版本"]
            r["实例汇总版本"] = cell["实例汇总版本"]
            r["缺口类型"] = cell["缺口类型"]
            r["缺口状态"] = cell["缺口状态"]
            r["缺口说明"] = cell["缺口说明"]
        return rows


@router.post("/实例/心跳")
def heartbeat(body: HeartbeatIn):
    """实例登记通道：只写实例册与心跳流水，永不改名册；登记后自动重算缺口。"""
    if body.健康 not in ("探针绿", "探针红"):
        raise HTTPException(400, "健康状态非法")
    with db() as conn:
        app = conn.execute("SELECT id, name FROM apps WHERE id=?", (body.应用编号,)).fetchone()
        if not app:
            raise HTTPException(404, "应用不存在")
        env = conn.execute("SELECT id, code FROM environments WHERE id=?", (body.环境编号,)).fetchone()
        if not env:
            raise HTTPException(404, "环境不存在")
        if body.发版单编号 is not None:
            rel = conn.execute("SELECT id FROM releases WHERE id=?", (body.发版单编号,)).fetchone()
            if not rel:
                raise HTTPException(404, "发版单不存在")
        register_heartbeat(
            conn, body.应用编号, body.环境编号, body.版本, body.健康,
            body.发版单编号, instance_key=body.实例键, source="心跳",
        )
        return cell_read_model(conn, body.应用编号, body.环境编号)


@router.post("/实例/{instance_id}/健康")
def set_health(instance_id: int, body: HealthIn):
    if body.健康 not in ("探针绿", "探针红"):
        raise HTTPException(400, "健康状态非法")
    with db() as conn:
        row = conn.execute("SELECT * FROM instances WHERE id=?", (instance_id,)).fetchone()
        if not row:
            raise HTTPException(404, "实例不存在")
        conn.execute(
            "UPDATE instances SET health=?, updated_at=? WHERE id=?",
            (body.健康, now_str(), instance_id),
        )
        audit(conn, "探针", instance_id, body.健康, "")
        # 探针变化不改变版本缺口口径，但刷新读模型（红灯数/最近心跳）
        recompute_gap(conn, row["app_id"], row["env_id"])
        return {"编号": instance_id, "健康": body.健康}
