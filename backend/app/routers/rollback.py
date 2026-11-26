from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.db import db, row_to_dict
from app.services.common import (
    active_rollback_window,
    audit,
    get_release,
    now_str,
)
from app.services.versioning import recompute_gap, register_heartbeat, write_registry_version

router = APIRouter(tags=["回滚"])


class RollbackIn(BaseModel):
    目标版本: str
    备注: str = ""


@router.post("/发版单/{release_id}/回滚")
def rollback(release_id: int, body: RollbackIn):
    with db() as conn:
        r = conn.execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        if not r:
            raise HTTPException(404, "发版单不存在")
        if r["status"] not in ("已放行", "晋升中"):
            raise HTTPException(400, "当前状态不可回滚")
        if not r["current_env_id"]:
            raise HTTPException(400, "无当前环境，无法回滚")
        win = active_rollback_window(conn, r["current_env_id"])
        if not win:
            raise HTTPException(400, "回滚窗已关，禁止回滚")
        now = now_str()
        conn.execute(
            """
            UPDATE releases SET status='已回滚', rolled_back_at=?, target_version=? WHERE id=?
            """,
            (now, body.目标版本, release_id),
        )
        # 回滚同样经名册唯一写口落真源，实例登记走心跳通道，同事务同命运
        write_registry_version(
            conn, r["app_id"], r["current_env_id"], body.目标版本,
            expected_release_id=None,
        )
        register_heartbeat(
            conn, r["app_id"], r["current_env_id"], body.目标版本, "探针绿",
            release_id, instance_key="主实例", source="回滚登记",
        )
        # 旧版本实例标红，便于对账发现残留（残留治理另立；此处只保证缺口可见）
        conn.execute(
            """
            UPDATE instances SET health='探针红', updated_at=?
            WHERE app_id=? AND env_id=? AND version!=? AND release_id=?
            """,
            (now, r["app_id"], r["current_env_id"], body.目标版本, release_id),
        )
        recompute_gap(conn, r["app_id"], r["current_env_id"])
        audit(conn, "回滚", release_id, f"回滚到{body.目标版本}", body.备注)
        return row_to_dict(get_release(conn, release_id))
