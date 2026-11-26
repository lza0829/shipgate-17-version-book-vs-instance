# 发版闸（Shipgate）

骨架：发版申请入闸 → 环境晋升裁决 → 放行/回滚窗 → 发布结果对账

- 前端 http://127.0.0.1:6800
- API http://127.0.0.1:11800
- 数据库环境变量 `SHIPGATE_DB`（容器内默认 `/data/shipgate.db`）

```bash
docker compose up -d --build
```

## 环境版本真源（全局唯一一套账）

- **真源：应用名册 `app_env_versions.version`**。名册页、实例册、健康页、总览、发版单当前环境、对账、审计的"环境当前版本"全部经 `app/services/versioning.py` 的同一读模型（`cell_read_model`）重放，禁止各认一本账。
- **实例册（`instances` + `instance_heartbeats`）只作探活附件**。心跳登记（`POST /api/实例/心跳`）只写实例侧并追加不可变流水，**永不盖写名册**。
- **缺口标 `version_gaps`**：名册与实例心跳汇总不一致时按统一口径（`classify_gap`）立账，每应用×环境至多一条：
  - `实例缺失`：名册有版本、实例册无心跳；
  - `实例未达版本`：实例全员同版本但落后名册（唯一可"纠正名册"的情形）；
  - `实例版本混杂`：实例存在多版本、无统一汇总，禁止以附件盖真源，只能确认挂账或等收敛。
  - 半截双账不允许无标：重算（`POST /api/对账/重算`）、确认（`/api/缺口/{id}/确认`）、纠正（`/api/缺口/{id}/纠正`）三个入口都在对账页；实例追平后缺口自动闭合。
- **晋升与实例登记同命运**：`POST /api/发版单/{id}/晋升` 在同一事务内先写名册真源、再登记实例附件，任一失败整体回滚；进程内写锁串行化晋升与心跳，并发交错只能看到改前或改后两态。
- 回滚残留治理另立专题；本闸只保证日常版本真源唯一、缺口可核。

## 夹具

`backend/tests/test_version_source.py` 覆盖：晋升后名册先改、实例先到、晋升×心跳并发、半截混杂双账、六面对读、事务失败回滚、旧库迁移、空名册、回滚、混合并发压力。

```bash
cd backend
pip install -r requirements.txt
SHIPGATE_DB=/tmp/test.db python -m pytest tests/ -q
```
