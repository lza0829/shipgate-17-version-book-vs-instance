from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.db import db, rows_to_list
from app.services.common import audit, now_str
from app.services.versioning import cell_read_model

router = APIRouter(tags=["应用"])


class AppIn(BaseModel):
    名称: str = Field(min_length=1)
    负责人: str = ""
    状态: str = "启用"


@router.get("/应用")
def list_apps():
    """应用名册：版本列只认名册真源，缺口作为标注附上，不盖版本。"""
    with db() as conn:
        apps = rows_to_list(
            conn.execute(
                "SELECT id AS 编号, name AS 名称, owner AS 负责人, status AS 状态 FROM apps ORDER BY id"
            ).fetchall()
        )
        envs = conn.execute("SELECT id, code FROM environments ORDER BY sort_order").fetchall()
        for app in apps:
            环境版本 = []
            for env in envs:
                cell = cell_read_model(conn, app["编号"], env["id"])
                环境版本.append({
                    "环境": env["code"],
                    "环境编号": env["id"],
                    "名册版本": cell["名册版本"],
                    "版本": cell["名册版本"],
                    "实例汇总版本": cell["实例汇总版本"],
                    "实例数": cell["实例数"],
                    "缺口类型": cell["缺口类型"],
                    "缺口状态": cell["缺口状态"],
                    "缺口说明": cell["缺口说明"],
                    "缺口编号": cell["缺口编号"],
                    "可纠正": cell["可纠正"],
                    "版本更新时间": cell["版本更新时间"],
                })
            app["环境版本"] = 环境版本
        return apps


@router.post("/应用")
def create_app(body: AppIn):
    if body.状态 not in ("启用", "停用"):
        raise HTTPException(400, "状态仅允许启用或停用")
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO apps(name, owner, status) VALUES (?, ?, ?)",
                (body.名称, body.负责人, body.状态),
            )
        except Exception:
            raise HTTPException(400, "应用名已存在")
        aid = cur.lastrowid
        # 新应用各环境开空名册行（空版本不立缺口），不登记任何实例
        for env in conn.execute("SELECT id FROM environments").fetchall():
            conn.execute(
                "INSERT INTO app_env_versions(app_id, env_id, version, updated_at) VALUES (?, ?, '', ?)",
                (aid, env["id"], now_str()),
            )
        audit(conn, "应用", aid, "新建应用", body.名称)
        return {"编号": aid}


@router.patch("/应用/{app_id}")
def patch_app(app_id: int, body: AppIn):
    with db() as conn:
        row = conn.execute("SELECT id FROM apps WHERE id=?", (app_id,)).fetchone()
        if not row:
            raise HTTPException(404, "应用不存在")
        if body.状态 not in ("启用", "停用"):
            raise HTTPException(400, "状态仅允许启用或停用")
        conn.execute(
            "UPDATE apps SET name=?, owner=?, status=? WHERE id=?",
            (body.名称, body.负责人, body.状态, app_id),
        )
        audit(conn, "应用", app_id, "更新应用", body.状态)
        return {"编号": app_id, "更新时间": now_str()}
