"""环境版本真源服务。

真源约定（全局唯一一套）：
- 应用名册 app_env_versions.version 是环境当前版本的唯一真源；
- 实例册 instances + instance_heartbeats 只是探活附件，只做心跳登记，永不盖写名册；
- 名册与实例汇总不一致时，只在 version_gaps 留缺口标，缺口不得盖掉名册版本；
- 所有读侧（名册页、实例册、健康、总览、发版单当前环境、对账、审计）
  都经本模块重放同一读模型，禁止各认一本账。
"""
from __future__ import annotations

from app.services.common import audit, now_str

# 缺口类型 → 缺口说明
GAP_LABEL = {
    "实例缺失": "实例册无心跳（名册已登记版本）",
    "实例未达版本": "实例心跳汇总落后于名册版本",
    "实例版本混杂": "实例版本未收敛，无法判定统一汇总",
}
CONSISTENT = "版本一致"
NO_VERSION = "名册未登记版本"


def write_registry_version(conn, app_id: int, env_id: int, version: str,
                           expected_release_id: int | None = None,
                           audit_kind: str | None = None,
                           audit_ref: int | None = None,
                           audit_action: str = "") -> None:
    """名册通道唯一写口：只改真源，不碰实例册。

    同版本重复晋升/心跳不制造新缺口；版本变化时重置缺口台账的闭合态，
    交 recompute_gap 按最新事实重算。
    """
    row = conn.execute(
        "SELECT version FROM app_env_versions WHERE app_id=? AND env_id=?",
        (app_id, env_id),
    ).fetchone()
    old_version = row["version"] if row else ""
    conn.execute(
        """
        INSERT INTO app_env_versions(app_id, env_id, version, updated_at, expected_release_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(app_id, env_id) DO UPDATE SET
          version=excluded.version,
          updated_at=excluded.updated_at,
          expected_release_id=excluded.expected_release_id
        """,
        (app_id, env_id, version, now_str(), expected_release_id),
    )
    if old_version != version:
        # 版本真源已变，旧缺口结论失效：若已有缺口行则重开待重算；
        # 没有缺口行时不预插，由 recompute_gap 按事实决定是否立缺口。
        grow = conn.execute(
            "SELECT id FROM version_gaps WHERE app_id=? AND env_id=?",
            (app_id, env_id),
        ).fetchone()
        if grow:
            conn.execute(
                """
                UPDATE version_gaps SET
                  registry_version=?, observed_summary='', gap_kind='',
                  status='未确认', detected_at=?, acknowledged_at=NULL,
                  closed_at=NULL, note=''
                WHERE id=?
                """,
                (version, now_str(), grow["id"]),
            )
        if audit_kind and audit_action:
            audit(conn, audit_kind, audit_ref, audit_action, f"名册版本→{version or '空'}")
    recompute_gap(conn, app_id, env_id)


def register_heartbeat(conn, app_id: int, env_id: int, version: str,
                       health: str = "探针绿", release_id: int | None = None,
                       instance_key: str = "主实例", source: str = "心跳") -> None:
    """实例通道唯一写口：心跳登记只写实例册，并追加不可变流水。

    永不写 app_env_versions（探活附件不得盖掉真源）；登记后重算缺口。
    """
    now = now_str()
    conn.execute(
        """
        INSERT INTO instances(app_id, env_id, instance_key, version, health, release_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(app_id, env_id, instance_key) DO UPDATE SET
          version=excluded.version,
          health=excluded.health,
          release_id=excluded.release_id,
          updated_at=excluded.updated_at
        """,
        (app_id, env_id, instance_key, version, health, release_id, now),
    )
    conn.execute(
        """
        INSERT INTO instance_heartbeats(app_id, env_id, instance_key, version,
                                        health, release_id, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (app_id, env_id, instance_key, version, health, release_id, source, now),
    )
    audit(conn, "心跳", None, "实例心跳登记",
          f"应用{app_id}/环境{env_id}/{instance_key}@{version}")
    recompute_gap(conn, app_id, env_id)


def instance_summary(conn, app_id: int, env_id: int) -> dict:
    """实例心跳汇总算法（全局唯一）：

    - 无实例：汇总为空、实例数 0；
    - 所有实例同版本：汇总=该版本（按实例键去重，每键一票）；
    - 存在多版本：汇总=None（未收敛），各版本计数交调用方展示。
    """
    rows = conn.execute(
        """
        SELECT instance_key, version, health, updated_at
        FROM instances WHERE app_id=? AND env_id=?
        ORDER BY instance_key
        """,
        (app_id, env_id),
    ).fetchall()
    total = len(rows)
    if total == 0:
        return {"汇总版本": None, "实例数": 0, "同版本": True, "版本计数": {},
                "绿灯数": 0, "红灯数": 0, "最近心跳": None}
    counts: dict[str, int] = {}
    red = 0
    latest = ""
    for r in rows:
        counts[r["version"]] = counts.get(r["version"], 0) + 1
        if r["health"] == "探针红":
            red += 1
        if r["updated_at"] and r["updated_at"] > latest:
            latest = r["updated_at"]
    unanimous = len(counts) == 1
    return {
        "汇总版本": rows[0]["version"] if unanimous else None,
        "实例数": total,
        "同版本": unanimous,
        "版本计数": counts,
        "绿灯数": total - red,
        "红灯数": red,
        "最近心跳": latest or None,
    }


def classify_gap(registry_version: str, summary: dict) -> tuple[str, str]:
    """缺口判定（全局唯一口径），返回 (缺口类型, 观察摘要文本)。"""
    counts = summary["版本计数"]
    observed = ",".join(f"{v}×{c}" for v, c in sorted(counts.items()))
    if not registry_version:
        return "", observed  # 名册未登记版本，无真源可对，不立缺口
    if summary["实例数"] == 0:
        return "实例缺失", observed
    if not summary["同版本"]:
        return "实例版本混杂", observed
    if summary["汇总版本"] != registry_version:
        return "实例未达版本", observed
    return "", observed


def recompute_gap(conn, app_id: int, env_id: int) -> dict | None:
    """按最新名册与实例事实重算单个环境的缺口标。

    缺口行每应用×环境至多一条，反复对账不制造重复台账；
    缺口闭合（实例追平/纠正名册）后记录 closed_at，审计可查。
    """
    vrow = conn.execute(
        "SELECT * FROM app_env_versions WHERE app_id=? AND env_id=?",
        (app_id, env_id),
    ).fetchone()
    if not vrow:
        return None
    registry_version = vrow["version"]
    summary = instance_summary(conn, app_id, env_id)
    kind, observed = classify_gap(registry_version, summary)

    grow = conn.execute(
        "SELECT * FROM version_gaps WHERE app_id=? AND env_id=?",
        (app_id, env_id),
    ).fetchone()

    if not kind:
        # 账实相符：清掉未确认/已确认的开口；历史已纠正/已闭合行保留为闭环凭证
        if grow and grow["status"] in ("未确认", "已确认"):
            conn.execute(
                "UPDATE version_gaps SET status='已闭合', gap_kind='', observed_summary=?, "
                "closed_at=? WHERE id=?",
                (observed, now_str(), grow["id"]),
            )
            audit(conn, "缺口", grow["id"], "缺口闭合",
                  f"应用{app_id}/环境{env_id} 名册与实例一致@{registry_version or '空'}")
        return None

    if grow:
        prior_status = grow["status"]
        # 已确认的缺口仅在「缺口类型与观察事实都没变」时保持已确认；
        # 实例汇总/名册版本一旦变化即回到未确认，重新等待人工处置。
        keep_ack = (
            prior_status == "已确认"
            and grow["gap_kind"] == kind
            and grow["observed_summary"] == observed
            and grow["registry_version"] == registry_version
        )
        new_status = "已确认" if keep_ack else "未确认"
        conn.execute(
            """
            UPDATE version_gaps SET registry_version=?, observed_summary=?, gap_kind=?,
              status=?, detected_at=?
            WHERE id=?
            """,
            (registry_version, observed, kind, new_status, now_str(), grow["id"]),
        )
        if prior_status in ("已闭合", "已纠正"):
            audit(conn, "缺口", grow["id"], "缺口重开", f"{kind}:{observed}")
        elif prior_status == "已确认" and new_status == "未确认":
            audit(conn, "缺口", grow["id"], "缺口重开", f"事实变更 {kind}:{observed}")
        return {"缺口编号": grow["id"], "缺口类型": kind, "缺口状态": new_status}

    cur = conn.execute(
        """
        INSERT INTO version_gaps(app_id, env_id, registry_version, observed_summary,
                                 gap_kind, status, detected_at)
        VALUES (?, ?, ?, ?, ?, '未确认', ?)
        """,
        (app_id, env_id, registry_version, observed, kind, now_str()),
    )
    audit(conn, "缺口", cur.lastrowid, "发现缺口", f"{kind}:{observed}")
    return {"缺口编号": cur.lastrowid, "缺口类型": kind, "缺口状态": "未确认"}


def cell_read_model(conn, app_id: int, env_id: int) -> dict:
    """单个应用×环境的统一读模型：所有读侧共用，版本列只认名册。"""
    vrow = conn.execute(
        "SELECT * FROM app_env_versions WHERE app_id=? AND env_id=?",
        (app_id, env_id),
    ).fetchone()
    arow = conn.execute("SELECT name FROM apps WHERE id=?", (app_id,)).fetchone()
    erow = conn.execute(
        "SELECT code, name, sort_order FROM environments WHERE id=?", (env_id,)
    ).fetchone()
    registry_version = vrow["version"] if vrow else ""
    summary = instance_summary(conn, app_id, env_id)
    kind, observed = classify_gap(registry_version, summary)
    grow = conn.execute(
        "SELECT * FROM version_gaps WHERE app_id=? AND env_id=?",
        (app_id, env_id),
    ).fetchone()
    gap_status = grow["status"] if grow and grow["status"] not in ("已闭合", "已纠正") else None
    if not kind:
        gap_status = None
    return {
        "应用编号": app_id,
        "应用": arow["name"] if arow else str(app_id),
        "环境编号": env_id,
        "环境": erow["code"] if erow else str(env_id),
        "环境名": erow["name"] if erow else "",
        "环境顺序": erow["sort_order"] if erow else 0,
        # 环境当前版本：唯一名册真源
        "名册版本": registry_version,
        "版本": registry_version,
        "版本更新时间": vrow["updated_at"] if vrow else None,
        "预期发版单编号": vrow["expected_release_id"] if vrow else None,
        # 实例册只是附件
        "实例汇总版本": summary["汇总版本"],
        "实例数": summary["实例数"],
        "实例同版本": summary["同版本"],
        "实例版本计数": summary["版本计数"],
        "实例版本们": observed,
        "绿灯数": summary["绿灯数"],
        "红灯数": summary["红灯数"],
        "最近心跳": summary["最近心跳"],
        # 缺口标
        "缺口类型": kind,
        "缺口说明": GAP_LABEL.get(kind, CONSISTENT if registry_version and not kind else NO_VERSION),
        "缺口状态": gap_status,
        "缺口编号": grow["id"] if grow and gap_status else None,
        "可纠正": kind == "实例未达版本",
        "一致": not kind and bool(registry_version),
    }


def all_cells(conn) -> list[dict]:
    pairs = conn.execute(
        """
        SELECT v.app_id AS app_id, v.env_id AS env_id
        FROM app_env_versions v
        JOIN apps a ON a.id = v.app_id
        JOIN environments e ON e.id = v.env_id
        ORDER BY a.id, e.sort_order
        """
    ).fetchall()
    return [cell_read_model(conn, p["app_id"], p["env_id"]) for p in pairs]


def acknowledge_gap(conn, gap_id: int, note: str = "") -> dict:
    """缺口确认口：只改缺口台账状态，不动真源、不动实例。"""
    grow = conn.execute("SELECT * FROM version_gaps WHERE id=?", (gap_id,)).fetchone()
    if not grow:
        from fastapi import HTTPException
        raise HTTPException(404, "缺口不存在")
    if grow["status"] in ("已闭合", "已纠正"):
        from fastapi import HTTPException
        raise HTTPException(400, "缺口已闭环，无需确认")
    conn.execute(
        "UPDATE version_gaps SET status='已确认', acknowledged_at=?, note=? WHERE id=?",
        (now_str(), note, gap_id),
    )
    audit(conn, "缺口", gap_id, "缺口确认", note or grow["gap_kind"])
    return cell_read_model(conn, grow["app_id"], grow["env_id"])


def correct_gap(conn, gap_id: int, note: str = "") -> dict:
    """缺口纠正口：仅当实例已全员收敛到同一版本时，才允许把名册纠正到实例汇总。

    混杂/缺失缺口无统一汇总，禁止用附件盖真源，只能继续挂缺口或确认。
    """
    from fastapi import HTTPException
    grow = conn.execute("SELECT * FROM version_gaps WHERE id=?", (gap_id,)).fetchone()
    if not grow:
        raise HTTPException(404, "缺口不存在")
    summary = instance_summary(conn, grow["app_id"], grow["env_id"])
    if summary["实例数"] == 0 or not summary["同版本"]:
        raise HTTPException(400, "实例版本未收敛，禁止以实例册盖写名册，请先收敛或确认缺口")
    target = summary["汇总版本"]
    # 纠正走名册唯一写口：真源更新后 recompute 会先把缺口正常闭合，
    # 再把该缺口行标记为「已纠正」作为人工纠正凭证。
    write_registry_version(
        conn, grow["app_id"], grow["env_id"], target, None,
        audit_kind="缺口", audit_ref=gap_id, audit_action="按实例汇总纠正名册",
    )
    conn.execute(
        "UPDATE version_gaps SET status='已纠正', closed_at=?, note=? WHERE app_id=? AND env_id=?",
        (now_str(), note or f"名册纠正为实例汇总 {target}", grow["app_id"], grow["env_id"]),
    )
    return cell_read_model(conn, grow["app_id"], grow["env_id"])
