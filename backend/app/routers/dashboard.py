from __future__ import annotations
from fastapi import APIRouter
from app.db import db, rows_to_list
from app.services.common import RELEASE_SELECT
from app.services.versioning import all_cells

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        counts = {}
        for name in ("待入闸", "已入闸", "晋升中", "已放行", "回滚中", "已回滚", "已关闭"):
            counts[name] = conn.execute(
                "SELECT COUNT(*) AS c FROM releases WHERE status=?", (name,)
            ).fetchone()["c"]
        active = rows_to_list(
            conn.execute(
                RELEASE_SELECT
                + " WHERE r.status IN ('已入闸','晋升中','已放行','回滚中') ORDER BY r.id DESC"
            ).fetchall()
        )
        # 环境版本读侧按真源重放：版本=名册，实例只作附件，不一致看缺口标
        cells = all_cells(conn)
        versions = [
            {
                "应用": c["应用"],
                "应用编号": c["应用编号"],
                "环境": c["环境"],
                "环境编号": c["环境编号"],
                "环境名": c["环境名"],
                "版本": c["名册版本"],
                "名册版本": c["名册版本"],
                "实例汇总版本": c["实例汇总版本"],
                "实例数": c["实例数"],
                "缺口类型": c["缺口类型"],
                "缺口状态": c["缺口状态"],
                "缺口说明": c["缺口说明"],
                "更新时间": c["版本更新时间"],
                "最近心跳": c["最近心跳"],
            }
            for c in cells
        ]
        health = rows_to_list(
            conn.execute(
                """
                SELECT i.id AS 编号, a.name AS 应用, e.code AS 环境, i.version AS 版本,
                       i.health AS 健康, i.release_id AS 发版单编号, i.updated_at AS 更新时间
                FROM instances i
                JOIN apps a ON a.id = i.app_id
                JOIN environments e ON e.id = i.env_id
                ORDER BY a.id, e.sort_order, i.id
                """
            ).fetchall()
        )
        open_gaps = [c for c in cells if c["缺口状态"]]
        gap_counts = {}
        for c in open_gaps:
            gap_counts[c["缺口类型"]] = gap_counts.get(c["缺口类型"], 0) + 1
        return {
            "分布": counts,
            "在途": active,
            "环境版本": versions,
            "实例": health,
            "缺口数": len(open_gaps),
            "缺口分类": gap_counts,
        }
