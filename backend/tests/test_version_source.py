"""环境版本真源夹具。

覆盖：
1. 晋升后名册先改（实例缺失缺口→心跳追平→闭合）
2. 实例先到（心跳永不盖名册，未达版本缺口→纠正口闭环）
3. 晋升与心跳并发（同事务 + 写锁，无半截双账）
4. 半截双账（混杂缺口不可纠正，必须留标；确认口可挂账）
5. 名册页/实例册/健康/总览/对账/审计六面对读
6. 同事务失败回滚、审计筛选
"""
from __future__ import annotations

import importlib
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "shipgate.db"
    os.environ["SHIPGATE_DB"] = str(db_path)
    # 配置在导入时固化 DB_PATH，整套模块重载以指向临时库
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.config import DB_PATH  # noqa: F401
    from app.main import app
    from app.db import connect
    with TestClient(app) as c:
        c._connect = connect
        yield c


# ---------- 夹具辅助 ----------

def ids(client) -> tuple[int, int, int, int, int]:
    apps = client.get("/api/应用").json()
    app_id = next(a["编号"] for a in apps if a["名称"] == "结算网关")
    envs = {e["代号"]: e["编号"] for e in client.get("/api/环境").json()}
    rel = next(r for r in client.get("/api/发版单").json() if r["应用"] == "结算网关")
    return app_id, envs["测"], envs["预"], envs["产"], rel["编号"]


def raw_conn(client):
    return client._connect()


def cell(client, app_id: int, env_id: int) -> dict:
    return client.get(f"/api/应用/{app_id}/环境/{env_id}/版本读模型").json()


def heartbeat(client, app_id, env_id, version, key="主实例", health="探针绿", release_id=None):
    return client.post("/api/实例/心跳", json={
        "应用编号": app_id, "环境编号": env_id, "版本": version,
        "实例键": key, "健康": health, "发版单编号": release_id,
    })


def promote(client, release_id, target):
    return client.post(f"/api/发版单/{release_id}/晋升", json={"目标环境": target})


def surfaces(client, app_id, env_id):
    """六面对读：名册页/实例册/总览/对账/发版单/读模型 各自报出的名册版本必须相同。"""
    read = cell(client, app_id, env_id)
    apps = client.get("/api/应用").json()
    roster = next(x for x in apps if x["编号"] == app_id)
    roster_ver = next(e["名册版本"] for e in roster["环境版本"] if e["环境编号"] == env_id)
    ov = client.get("/api/总览").json()
    ov_row = next(v for v in ov["环境版本"] if v["应用"] == roster["名称"]
                  and v["环境编号"] == env_id)
    rec = client.get("/api/对账").json()
    rec_row = next(r for r in rec["名册与实例"] if r["应用"] == roster["名称"]
                   and r["环境编号"] == env_id)
    rel_rows = client.get("/api/发版单").json()
    return {
        "读模型": read["名册版本"],
        "名册页": roster_ver,
        "总览": ov_row["名册版本"],
        "对账": rec_row["名册版本"],
        "发版单": {r["编号"]: r["当前环境名册版本"] for r in rel_rows},
        "缺口类型": read["缺口类型"],
        "缺口状态": read["缺口状态"],
    }


# ---------- 夹具一：晋升后名册先改，实例后到 ----------

def test_注册表先改_实例追平_缺口自动闭合(client):
    app_id, test_id, pre_id, _, rel_id = ids(client)

    # 晋升到预：同一事务里名册先到 1.3.0、实例也登记 1.3.0，先验证账实相符
    r = promote(client, rel_id, "预")
    assert r.status_code == 200, r.text
    c0 = cell(client, app_id, pre_id)
    assert c0["名册版本"] == "1.3.0"
    assert c0["实例汇总版本"] == "1.3.0"
    assert c0["缺口类型"] == "" and c0["缺口状态"] is None

    # 故障注入：删掉实例心跳，制造「名册已是新版、实例册全旧/缺失」的半截状态
    conn = raw_conn(client)
    conn.execute("DELETE FROM instances WHERE app_id=? AND env_id=?", (app_id, pre_id))
    conn.execute("DELETE FROM instance_heartbeats WHERE app_id=? AND env_id=?", (app_id, pre_id))
    conn.commit()
    conn.close()
    c1 = client.post("/api/对账/重算", json={"应用编号": app_id, "环境编号": pre_id}).json()
    assert c1["名册版本"] == "1.3.0"            # 缺口不得盖名册
    assert c1["实例数"] == 0
    assert c1["缺口类型"] == "实例缺失" and c1["缺口状态"] == "未确认"

    # 各读面对读：版本仍听名册，缺口标一致
    s = surfaces(client, app_id, pre_id)
    assert s["读模型"] == s["名册页"] == s["总览"] == s["对账"] == "1.3.0"
    assert s["缺口类型"] == "实例缺失"

    # 实例心跳追平 → 重算 → 缺口闭合，双账消除
    heartbeat(client, app_id, pre_id, "1.3.0")
    c2 = cell(client, app_id, pre_id)
    assert c2["名册版本"] == "1.3.0" and c2["实例汇总版本"] == "1.3.0"
    assert c2["缺口状态"] is None
    gaps = client.get("/api/缺口").json()
    g = next(g for g in gaps if g["应用"] == "结算网关" and g["环境"] == "预")
    assert g["缺口状态"] == "已闭合"

    # 审计可查缺口发现与闭合
    audit = client.get("/api/审计", params={"类别": "缺口"}).json()
    actions = [a["动作"] for a in audit]
    assert "发现缺口" in actions and "缺口闭合" in actions


# ---------- 夹具二：实例先到，名册未改 ----------

def test_实例先到_心跳不盖名册_纠正口闭环(client):
    app_id, test_id, pre_id, _, rel_id = ids(client)

    # 实例先以新版心跳登记到预，名册仍停在 1.1.0
    r = heartbeat(client, app_id, pre_id, "1.3.0")
    assert r.status_code == 200, r.text
    c1 = cell(client, app_id, pre_id)
    assert c1["名册版本"] == "1.1.0"            # 真源不被附件盖写
    assert c1["实例汇总版本"] == "1.3.0"
    assert c1["缺口类型"] == "实例未达版本"
    assert c1["可纠正"] is True

    # 六面对读：所有版本列仍报名册 1.1.0
    s = surfaces(client, app_id, pre_id)
    assert s["读模型"] == s["名册页"] == s["总览"] == s["对账"] == "1.1.0"
    rel = next(x for x in client.get("/api/发版单").json() if x["编号"] == rel_id)
    assert rel["当前环境名册版本"] == "1.3.0"   # 发版单当前环境在测，读测名册

    # 走纠正口：名册按已收敛的实例汇总纠正，双账消除
    gap_id = c1["缺口编号"]
    r = client.post(f"/api/缺口/{gap_id}/纠正", json={"备注": "实例已全量，纠正名册"})
    assert r.status_code == 200, r.text
    c2 = cell(client, app_id, pre_id)
    assert c2["名册版本"] == "1.3.0" and c2["实例汇总版本"] == "1.3.0"
    assert c2["缺口状态"] is None
    gap = next(g for g in client.get("/api/缺口").json()
               if g["应用"] == "结算网关" and g["环境"] == "预")
    assert gap["缺口状态"] == "已纠正"
    audit = client.get("/api/审计", params={"类别": "缺口"}).json()
    assert any(a["动作"] == "按实例汇总纠正名册" for a in audit)


# ---------- 夹具三：晋升与心跳并发，不允许半截双账 ----------

def test_晋升与心跳并发_只有改前或改后两态(client):
    app_id, test_id, pre_id, _, rel_id = ids(client)
    barrier = threading.Barrier(2)
    outcomes: list[dict] = []

    def do_promote():
        barrier.wait()
        outcomes.append({"kind": "promote",
                         "r": promote(client, rel_id, "预").status_code})

    def do_heartbeat():
        barrier.wait()
        outcomes.append({"kind": "hb",
                         "r": heartbeat(client, app_id, pre_id, "9.9.9",
                                        key="灰度实例").status_code})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(do_promote), pool.submit(do_heartbeat)]
        for f in futs:
            f.result()
    assert all(o["r"] == 200 for o in outcomes)

    c = cell(client, app_id, pre_id)
    # 写锁串行化：名册只可能是改前 1.1.0 或改后 1.3.0，绝不出现第三种值
    assert c["名册版本"] in ("1.1.0", "1.3.0")
    if c["名册版本"] == "1.3.0":
        # 晋升已落账：主实例 1.3.0；灰度 9.9.9 只作为附件，产生混杂缺口而非盖名册
        assert c["实例版本计数"].get("1.3.0") == 1
        assert c["缺口类型"] in ("实例版本混杂", "")
    else:
        # 心跳先落、晋升未进：名册仍是旧版，灰度实例构成未达版本缺口
        assert c["缺口类型"] in ("实例未达版本", "实例版本混杂")
    # 无论谁先，缺口台账与读模型口径一致，不存在「无标双账」
    rec = client.get("/api/对账").json()
    row = next(r for r in rec["名册与实例"]
               if r["应用"] == "结算网关" and r["环境"] == "预")
    assert row["缺口类型"] == c["缺口类型"]


# ---------- 夹具四：半截双账（版本混杂）必须留标，且禁止盖真源 ----------

def test_半截双账_混杂缺口不可纠正_只能确认(client):
    app_id, _, pre_id, _, rel_id = ids(client)
    # 名册晋升到 1.3.0（主实例随晋升登记）
    assert promote(client, rel_id, "预").status_code == 200
    # 故障注入：一台实例还停在旧版 1.1.0，形成金丝雀半截
    assert heartbeat(client, app_id, pre_id, "1.1.0", key="残留实例").status_code == 200

    c = cell(client, app_id, pre_id)
    assert c["名册版本"] == "1.3.0"
    assert c["实例同版本"] is False
    assert c["缺口类型"] == "实例版本混杂"
    assert c["可纠正"] is False                      # 无统一汇总，禁止附件盖真源
    assert c["缺口状态"] == "未确认"

    # 纠正口必须拒绝
    r = client.post(f"/api/缺口/{c['缺口编号']}/纠正", json={})
    assert r.status_code == 400

    # 确认口可挂账，但名册版本不被改动
    r = client.post(f"/api/缺口/{c['缺口编号']}/确认", json={"备注": "灰度中，待残留实例收敛"})
    assert r.status_code == 200
    c2 = cell(client, app_id, pre_id)
    assert c2["名册版本"] == "1.3.0"
    assert c2["缺口状态"] == "已确认" and c2["缺口类型"] == "实例版本混杂"

    # 已确认缺口在普通心跳刷新下不打回未确认
    heartbeat(client, app_id, pre_id, "1.1.0", key="残留实例", health="探针红")
    c3 = cell(client, app_id, pre_id)
    assert c3["缺口状态"] == "已确认"

    # 残留实例收敛到新版后，重算自动闭合
    heartbeat(client, app_id, pre_id, "1.3.0", key="残留实例")
    c4 = cell(client, app_id, pre_id)
    assert c4["实例同版本"] is True and c4["缺口状态"] is None
    assert c4["名册版本"] == "1.3.0"


# ---------- 夹具五：六面对读 + 健康探针不盖真源 ----------

def test_六面对读_探针只作附件(client):
    app_id, _, pre_id, _, rel_id = ids(client)
    assert promote(client, rel_id, "预").status_code == 200

    # 健康页探针标红，只影响红灯数，不影响版本与缺口口径
    inst = client.get("/api/实例").json()
    target = next(i for i in inst if i["应用"] == "结算网关" and i["环境"] == "预")
    assert target["名册版本"] == "1.3.0"           # 实例册行也展示名册真源
    r = client.post(f"/api/实例/{target['编号']}/健康", json={"健康": "探针红"})
    assert r.status_code == 200
    c = cell(client, app_id, pre_id)
    assert c["名册版本"] == "1.3.0" and c["红灯数"] == 1
    assert c["缺口状态"] is None                    # 探针红不等于版本缺口

    s = surfaces(client, app_id, pre_id)
    assert s["读模型"] == s["名册页"] == s["总览"] == s["对账"] == "1.3.0"
    # 审计跟名册：晋升审计记录的是名册版本变更
    audit = client.get("/api/审计", params={"类别": "晋升"}).json()
    assert any("晋升到预" in a["动作"] for a in audit)
    hb_audit = client.get("/api/审计", params={"类别": "心跳"}).json()
    assert hb_audit and all("心跳登记" in a["动作"] for a in hb_audit)


# ---------- 夹具六：同事务失败回滚，不留半成品 ----------

def test_晋升事务失败_名册实例同回滚(client):
    app_id, _, pre_id, _, rel_id = ids(client)
    before = cell(client, app_id, pre_id)
    conn = raw_conn(client)
    # 人为破坏外键目标：让实例登记触发约束失败，验证整笔晋升回滚
    conn.execute("PRAGMA foreign_keys=ON")
    conn.close()

    # 用 monkeypatch 让 register_heartbeat 抛错，模拟事务中途失败
    import app.routers.promote as promote_mod
    original = promote_mod.register_heartbeat

    def boom(*a, **k):
        raise RuntimeError("实例登记失败")

    promote_mod.register_heartbeat = boom
    try:
        with pytest.raises(RuntimeError):
            promote(client, rel_id, "预")
    finally:
        promote_mod.register_heartbeat = original

    after = cell(client, app_id, pre_id)
    assert after["名册版本"] == before["名册版本"] == "1.1.0"
    assert after["实例汇总版本"] == "1.1.0"
    assert after["缺口状态"] is None
    # 发版单状态也回滚，仍停在测
    rel = next(r for r in client.get("/api/发版单").json() if r["编号"] == rel_id)
    assert rel["当前环境"] == "测"


# ---------- 夹具七：审计筛选与缺口查询可单独复现 ----------

def test_审计筛选_缺口查询(client):
    app_id, _, pre_id, _, _ = ids(client)
    heartbeat(client, app_id, pre_id, "7.0.0")
    c = cell(client, app_id, pre_id)
    gap_id = c["缺口编号"]
    assert gap_id

    all_audit = client.get("/api/审计").json()
    gap_audit = client.get("/api/审计", params={"类别": "缺口"}).json()
    assert gap_audit and all(a["类别"] == "缺口" for a in gap_audit)
    assert len(gap_audit) <= len(all_audit)

    gaps = client.get("/api/缺口").json()
    assert any(g["缺口编号"] == gap_id for g in gaps)
    one = next(g for g in gaps if g["缺口编号"] == gap_id)
    assert one["名册版本"] == "1.1.0" and "7.0.0" in one["实例版本们"]
    assert one["缺口类型"] == "实例未达版本"


# ---------- 夹具八：旧库结构迁移，老数据自动纳入真源读模型 ----------

LEGACY_SCHEMA = """
CREATE TABLE apps (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
  owner TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '启用'
);
CREATE TABLE environments (
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL, sort_order INTEGER NOT NULL
);
CREATE TABLE app_env_versions (
  app_id INTEGER NOT NULL, env_id INTEGER NOT NULL,
  version TEXT NOT NULL DEFAULT '', updated_at TEXT,
  PRIMARY KEY(app_id, env_id)
);
CREATE TABLE releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, serial TEXT NOT NULL UNIQUE,
  app_id INTEGER NOT NULL, target_version TEXT NOT NULL,
  artifact_digest TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '待入闸',
  pipeline_status TEXT NOT NULL DEFAULT '未回执', current_env_id INTEGER,
  canary_ratio INTEGER NOT NULL DEFAULT 10, note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, admitted_at TEXT, released_at TEXT, rolled_back_at TEXT
);
CREATE TABLE promote_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, release_id INTEGER NOT NULL,
  from_env_id INTEGER, to_env_id INTEGER NOT NULL, decision TEXT NOT NULL,
  created_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE release_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT, env_id INTEGER NOT NULL,
  open_at TEXT NOT NULL, close_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT '窗开'
);
CREATE TABLE rollback_windows (
  id INTEGER PRIMARY KEY AUTOINCREMENT, env_id INTEGER NOT NULL,
  open_at TEXT NOT NULL, close_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT '窗开'
);
CREATE TABLE instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT, app_id INTEGER NOT NULL, env_id INTEGER NOT NULL,
  version TEXT NOT NULL, health TEXT NOT NULL DEFAULT '探针绿',
  release_id INTEGER, updated_at TEXT NOT NULL
);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, ref_id INTEGER,
  action TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
);
"""


def test_旧库迁移_老实例按主实例并入_缺口自动立账(tmp_path):
    import sqlite3
    db_path = tmp_path / "legacy.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(LEGACY_SCHEMA)
    raw.execute("INSERT INTO environments(id, code, name, sort_order) VALUES (1,'预','预发',2)")
    raw.execute("INSERT INTO apps(id, name, owner, status) VALUES (1,'老应用','负责人','启用')")
    # 双账：名册 2.0.0，老实例还在 1.9.0
    raw.execute("INSERT INTO app_env_versions(app_id, env_id, version, updated_at) VALUES (1,1,'2.0.0','t')")
    raw.execute("INSERT INTO instances(app_id, env_id, version, health, updated_at) VALUES (1,1,'1.9.0','探针绿','t')")
    raw.commit()
    raw.close()

    os.environ["SHIPGATE_DB"] = str(db_path)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.main import app  # 启动即迁移
    with TestClient(app) as c:
        c0 = c.get("/api/应用/1/环境/1/版本读模型").json()
        assert c0["名册版本"] == "2.0.0"
        assert c0["实例汇总版本"] == "1.9.0"          # 老实例默认实例键=主实例
        assert c0["缺口类型"] == "实例未达版本"        # 老双账自动立缺口，不留无标双账
        inst = c.get("/api/实例").json()
        assert inst[0]["实例键"] == "主实例"


# ---------- 夹具九：空名册不立缺口；回滚后仍听名册 ----------

def test_空名册不立缺口_回滚仍听名册(client):
    # 新建应用：名册为空，无实例，不允许凭空立缺口
    r = client.post("/api/应用", json={"名称": "全新应用", "负责人": "新人", "状态": "启用"})
    assert r.status_code == 200
    new_id = r.json()["编号"]
    envs = {e["代号"]: e["编号"] for e in client.get("/api/环境").json()}
    c0 = cell(client, new_id, envs["预"])
    assert c0["名册版本"] == "" and c0["实例数"] == 0 and c0["缺口类型"] == ""
    assert c0["一致"] is False

    # 空名册下来心跳：附件登记但仍无真源可对，不立缺口、不盖名册
    heartbeat(client, new_id, envs["预"], "0.1.0")
    c1 = cell(client, new_id, envs["预"])
    assert c1["名册版本"] == "" and c1["实例汇总版本"] == "0.1.0" and c1["缺口类型"] == ""

    # 回滚路径：晋升到产后回滚，名册版本听回滚目标，缺口机制照旧
    app_id, _, pre_id, prod_id, rel_id = ids(client)
    assert promote(client, rel_id, "预").status_code == 200
    assert promote(client, rel_id, "产").status_code == 200
    rb = client.post(f"/api/发版单/{rel_id}/回滚", json={"目标版本": "1.0.0"})
    assert rb.status_code == 200, rb.text
    c2 = cell(client, app_id, prod_id)
    assert c2["名册版本"] == "1.0.0" and c2["实例汇总版本"] == "1.0.0"
    rel = next(x for x in client.get("/api/发版单").json() if x["编号"] == rel_id)
    assert rel["状态"] == "已回滚" and rel["当前环境名册版本"] == "1.0.0"


# ---------- 夹具十一：已确认缺口的生命周期 ----------

def test_已确认缺口_事实不变保持_事实变化重开(client):
    app_id, _, pre_id, _, _ = ids(client)
    heartbeat(client, app_id, pre_id, "1.4.0")
    c = cell(client, app_id, pre_id)
    gid = c["缺口编号"]
    client.post(f"/api/缺口/{gid}/确认", json={})
    assert cell(client, app_id, pre_id)["缺口状态"] == "已确认"

    # 同事实重复心跳：保持已确认
    heartbeat(client, app_id, pre_id, "1.4.0")
    assert cell(client, app_id, pre_id)["缺口状态"] == "已确认"

    # 事实变化（汇总版本变了）：打回未确认重新处置
    heartbeat(client, app_id, pre_id, "1.5.0")
    c2 = cell(client, app_id, pre_id)
    assert c2["缺口状态"] == "未确认" and c2["缺口类型"] == "实例未达版本"
    audit = client.get("/api/审计", params={"类别": "缺口"}).json()
    assert any(a["动作"] == "缺口重开" for a in audit)


# ---------- 夹具十：晋升 × 心跳混合并发压力，全程无无标双账 ----------

def test_混合并发压力_任意时刻缺口口径自洽(client):
    app_id, _, pre_id, _, rel_id = ids(client)
    errors: list[str] = []

    def promote_job():
        try:
            r = promote(client, rel_id, "预")
            if r.status_code not in (200, 400):  # 400=状态已不可晋升，属正常竞争失败
                errors.append(f"promote {r.status_code}")
        except Exception as exc:  # 事务回滚类异常允许，但必须不留半截
            errors.append(f"promote exc {exc}")

    def beat_job(i):
        try:
            r = heartbeat(client, app_id, pre_id, f"1.3.{i % 2}", key=f"压测实例{i % 3}")
            if r.status_code != 200:
                errors.append(f"hb {r.status_code}")
        except Exception as exc:
            errors.append(f"hb exc {exc}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(promote_job)]
        futs += [pool.submit(beat_job, i) for i in range(12)]
        for f in futs:
            f.result()
    assert not errors, errors

    c = cell(client, app_id, pre_id)
    rec = client.get("/api/对账").json()
    row = next(r for r in rec["名册与实例"]
               if r["应用"] == "结算网关" and r["环境"] == "预")
    # 读模型与对账永远同一口径；若双账则必有缺口标
    assert row["名册版本"] == c["名册版本"]
    assert row["缺口类型"] == c["缺口类型"]
    if c["名册版本"] != (c["实例汇总版本"] or ""):
        assert c["缺口类型"] != "" and c["缺口状态"] is not None
