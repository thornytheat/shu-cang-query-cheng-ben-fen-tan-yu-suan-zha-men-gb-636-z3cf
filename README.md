# costsplit 数仓查询成本分摊与预算闸门

把一份查询账单（部门 / 项目 / 扫描量 / 时长 / 并发 / 收益分）算成：单条查询成本、部门分摊金额、
未分摊金额、预算内与被截断的查询，以及每条结论的证据行（行号 + 原因码 + 原值）。

## 能力

- 采信：编码回退（GB18030）、BOM 剥离、重复表头分段、列数校验、末行截断识别。
- 计费：MB/GB/TB/PB 归一 GB；按小时桶切分时长，白天/夜间不同单价；并发加价；大客户折扣；最低计费。
- 质量：空值哨兵、坏数值、坏单位、坏时间、同号不同值、完全重复行，全部带稳定原因码。
- 分摊：共享查询按「部门时长 × 部门扫描量」加权，最大余额法分配到分，未分摊单独列出。
- 闸门：按 收益分/成本 降序放行，累计不超过部门预算，其余截断；批次版本乐观锁拒绝陈旧提交。
- 申诉：按查询改判归属或改开始时间/时长/扫描量，改判后整批重算；同一查询多条申诉去重，目标冲突保留最早一条。
- 回滚：每次结论留一份版本快照，可回到旧版本；回滚写成新版本，差异按部门与合计列出。
- 导出：账单明细、隔离证据、份额、决策、运行清单（run_id + 输入 sha256）、收据联（批次号 + 校验位 + 骑缝线）。

## 七个功能模块

| 模块 | 动作 | 入口 |
|---|---|---|
| M1 账单接入 | 导入样例、看编码与结构诊断 | `POST /api/import`、页面 `/` |
| M2 计费规则 | 看契约与单价、单条试算 | `GET /api/contract`、`POST /api/billing/preview` |
| M3 异常查询 | 看原因码分布与隔离证据行 | `GET /api/quarantine`、页面 `/quarantine` |
| M4 分摊与尾差 | 按部门看份额与未分摊 | `GET /api/allocations`、页面 `/allocations` |
| M5 预算闸门与批次 | 重算闸门、看批次清单 | `POST /api/gates`、`GET /api/batches`、页面 `/gates` `/batches` |
| M6 账单导出 | 六种导出物 | `GET /api/export`、页面 `/export` |
| M7 预算申诉与回滚 | 改判归属、时段复核、版本回滚与差异 | `POST /api/appeals`、`POST /api/appeals/rollback`、页面 `/appeals` |

模块表与陷阱登记见 `docs/DEPTH.md`，字段定义见 `docs/DATA-DICTIONARY.md`，接口见 `docs/API.md`。

## 核心规则

1. 单查询成本 = 扫描量(GB) × 0.0280 + Σ 小时桶时长 × 时段单价 + (并发 − 8) × 时长 × 0.1500；
   部门本批扫描量 ≥ 1024 GB 时扫描部分 ×0.90；低于 0.01 元按 0.01 元计。规则按 (priority, id) 顺序作用。
2. 共享查询：权重 = 部门时长(小时) × 部门扫描量(GB)，最大余额法分到分，份额合计 = 该查询成本。
3. 闸门：累计成本 + 本条成本 ≤ 部门预算则保留（`(0, budget]`），否则截断；部门账恒满足 分摊 = 保留 + 截断。
4. 申诉改判：只改登记的几列（归属部门 / 开始时间 / 时长 / 扫描量 / 单位）；改判后重新计价、重新分摊、重新跑闸门，
   写成一个新版本；回滚按旧快照再写一个新版本，版本号只增不减。

对账四账：行账、金额账、部门账、尾差账，全部在页面与导出里显示，差额非 0 会显式为「不平」。
版本账：改判后 部门分摊合计 = 各部门行合计 = 总额 − 未分摊。

## 启动

```bash
docker compose up -d --build          # http://127.0.0.1:18205
curl -s -X POST http://127.0.0.1:18205/api/import \
     -H 'Content-Type: application/json' -d '{"source":"bills_2026_11_dirty.csv"}'
```

本地直接跑：

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
COSTSPLIT_DB=var/costsplit.sqlite uvicorn app.main:create_app --factory --port 18205
pytest -q
```

重建数据库：删除命名卷 `docker compose down -v` 后重新 `up`。

## 演示步骤

1. 打开 `/`，选 `bills_2026_11_dirty.csv` 导入，看行账与原因码分布。
2. `/quarantine` 看 7 条隔离证据行（含同号不同值、哨兵、坏时间、坏单位、多一列、末行截断）。
3. `/allocations` 看部门份额（数据平台 9.23 元、风控 62.21 元、营销 0.38 元）与未分摊 1.33 元。
4. `/gates` 看排序截断，改预算后提交会升版本；用旧版本号再提交返回 409。
5. `/appeals` 选一条查询改判到别的部门（或改开始时间到 21:30、时长 7200 秒），看逐桶时段与差异；
   再按版本回滚，看部门差额与四账。
6. `/batches` 打印清单（打印版只有清单与骑缝线），`/export` 下载六种导出物。

## 目录

```text
app/{main,config,errors,reasons,manifest,store,web}.py
app/modules/m1_ingest … m7_appeal            七个功能模块
app/templates/  app/static/                  原生模板与手写样式、脚本
docs/{DEPTH,DATA-DICTIONARY,API}.md
sample_data/                                 八份样例（含三份空态）
tests/                                       conftest + 每模块一组 + e2e + M7 申诉与回滚
```

## 已知不覆盖

- 单文件导入，不做多账单拼接与跨月合并。
- 预算只到部门一档，没有项目级配额与跨期结转。
- 共享权重不区分读类型，不做列级计量。
- 闸门一次性截断，没有分阶段配额；申诉逐条改判，不支持成批合并成一版。
