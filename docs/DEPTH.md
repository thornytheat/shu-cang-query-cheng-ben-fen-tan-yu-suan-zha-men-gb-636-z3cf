# 深度登记 costsplit（数仓查询成本分摊与预算闸门）

- 规模：中型（7 个功能模块 / 7 个页面 / 91 条测试）
- 核心规则一：**单查询成本** = 扫描量折算 GB × 扫描单价 + 按小时桶切分的时长 × 时段单价 + 并发加价，按 (priority, id) 顺序作用四条规则后量化到分。
- 核心规则二：**共享查询分摊** = 部门时长(小时) × 部门扫描量(GB) 作权重，最大余额法分配到分，份额合计恒等于该查询成本；权重全零进未分摊。
- 核心规则三：**预算闸门** = 按 收益分/成本 降序放行，累计不超过部门预算（区间 (0, budget]），其余截断；部门缺预算整体截断。
- 核心规则四：**申诉改判** = 只改登记的几列（归属部门 / 开始时间 / 时长 / 扫描量 / 单位），改判后整批重算；同一查询多条申诉按改判内容去重，改判目标冲突时保留最早一条；结论写成新版本，回滚按旧快照再写一个新版本。
- 使用者与工作流：云格 SaaS 数据平台组 8 人，月账单 42 万，原先由会计把共享查询的成本手工拆给部门；现在导入查询账单即可得到「部门分摊 + 未分摊 + 预算内/被截断 + 逐行证据」。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 账单接入 | 选样例导入，看编码/分段/截断诊断 | POST /api/import、页面 / | `app/modules/m1_ingest/decode.py` `app/modules/m1_ingest/reader.py` `app/modules/m1_ingest/sniff.py` `app/modules/m1_ingest/service.py` | `tests/test_ingest.py` |
| M2 计费规则 | 看列契约、单价表、规则表，单条试算 | GET /api/contract、POST /api/billing/preview | `app/modules/m2_billing/contract.py` `app/modules/m2_billing/coerce.py` `app/modules/m2_billing/units.py` `app/modules/m2_billing/service.py` | `tests/test_billing.py` |
| M3 异常查询 | 看原因码分布与隔离证据行 | GET /api/quarantine、页面 /quarantine | `app/modules/m3_quality/rules.py` `app/modules/m3_quality/quarantine.py` `app/modules/m3_quality/metrics.py` | `tests/test_quality.py` |
| M4 分摊与尾差 | 按部门看份额、共享份额、未分摊 | GET /api/allocations、页面 /allocations | `app/modules/m4_alloc/weights.py` `app/modules/m4_alloc/remainder.py` `app/modules/m4_alloc/invariant.py` | `tests/test_alloc.py` |
| M5 预算闸门与批次 | 重算闸门、看排序决策、看批次清单 | POST /api/gates、GET /api/gates、GET /api/batches、页面 /gates 与 /batches | `app/modules/m5_gate/gate.py` `app/modules/m5_gate/batchkey.py` `app/modules/m5_gate/state.py` `app/modules/m5_gate/service.py` | `tests/test_gate.py` |
| M6 账单导出 | 导出账单/隔离/份额/决策/运行清单/收据 | GET /api/export、页面 /export | `app/modules/m6_export/csv_out.py` `app/modules/m6_export/json_out.py` `app/modules/m6_export/receipt.py` `app/modules/m6_export/service.py` | `tests/test_export.py` |
| M7 预算申诉与回滚 | 提交申诉改判归属、看重算差异、按时段复核、回滚到旧版本 | POST /api/appeals、POST /api/appeals/rollback、GET /api/appeals、页面 /appeals | `app/modules/m7_appeal/appeal.py` `app/modules/m7_appeal/buckets.py` `app/modules/m7_appeal/rollback.py` `app/modules/m7_appeal/service.py` `app/modules/m7_appeal/handler.py` | `tests/test_m7_appeal.py` `tests/test_m7_rollback.py` |

端到端：`tests/test_e2e.py`（脏样例 → 隔离 → 计费 → 分摊 → 闸门 → 导出，含失败输入与幂等重跑）。
v2 的申诉链路：`tests/test_m7_appeal.py`（改判 → 重算 → 时段复核）与 `tests/test_m7_rollback.py`（版本快照 → 差异 → 回滚）。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 账单导出成 GB18030，头部却写 utf-8 | `open()` 崩或中文部门名乱码，归集错部门 | 声明只作首个候选，严格解码失败回退 gb18030，并记 `Q_CHARSET_FALLBACK`；全部失败报首个坏字节所在行 | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_trap01_charset_fallback_decodes_gb18030` |
| TRAP-02 | 文件头带 UTF-8 BOM（本系统导出也带） | 首列名变 `\ufeffquery_id`，按列名取值全空 | 解码前剥离 BOM 并留 `Q_BOM_STRIPPED` 记录 | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_trap02_bom_stripped_before_header` |
| TRAP-04 | 分批导出，中间又出现一次表头 | 表头被当数据行，多出一条查询 | 识别重复表头为分段起点，`segments` 计数并记 `Q_DUP_HEADER` | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_trap04_duplicate_header_starts_new_segment` |
| TRAP-05 | 抓到的行多一列或少一列 | 静默补空或错列，成本算到别的字段上 | 严格列数校验，不一致整行进隔离并带原值与期望列数 | `app/modules/m3_quality/rules.py` | `tests/test_ingest.py::test_trap05_extra_column_quarantined_with_reason` |
| TRAP-06 | `1,024.0`、`１２８．０` | `float()` 抛错，或全角数字被当字符串跳过 | 全角转半角 + 去千分位 + 单位后缀归一，再 Decimal 化 | `app/modules/m2_billing/coerce.py` | `tests/test_billing.py::test_trap06_thousands_and_fullwidth_are_normalised` |
| TRAP-07 | 扫描量写成 `N/A`、`-`、`未知` | 被当 0 参与合计，总额虚低 | 哨兵清单集中在 `app/config.py`，返回 None 三态，整行隔离 | `app/modules/m2_billing/coerce.py` | `tests/test_quality.py::test_trap07_sentinel_amount_is_rejected_not_zeroed` |
| TRAP-09 | 扫描量 `9007199254740993` 字节、`9.007E+15` | 过 float 后丢位，导出变科学计数法 | 全程 Decimal，字节数字符串保形，JSON 导出禁指数 | `app/modules/m2_billing/units.py` | `tests/test_billing.py::test_trap09_big_integer_keeps_every_digit` |
| TRAP-10 | 多行金额相加差 1 分 | float 累加出现 0.434999… | 分量为 Decimal、最终量化到整数分 | `app/modules/m2_billing/service.py` | `tests/test_billing.py::test_trap10_float_accumulation_loses_a_cent` |
| TRAP-14 | 同一 query_id 两个不同扫描量 | 后写覆盖，账单里悄悄换个数 | 同号不同值整组隔离并列出两条证据行 | `app/modules/m3_quality/rules.py` | `tests/test_quality.py::test_trap14_duplicate_query_conflict_isolates_both` |
| TRAP-15 | 一张账单里 MB / GB / TB 混写 | 合计差 1024 倍 | 单位表按 1024 进制统一折算 GB，原始单位随行保留 | `app/modules/m2_billing/units.py` | `tests/test_billing.py::test_trap15_units_mb_gb_tb_share_one_basis` |
| TRAP-16 | 导出中断，末行只有 6 列且无换行 | 半行被当有效查询，凭空多一条成本 | 末行且列数不足按 `Q_TRUNCATED_ROW` 拒绝 | `app/modules/m3_quality/rules.py` | `tests/test_ingest.py::test_trap16_truncated_last_row_isolated` |
| TRAP-19 | 夜间价、大客户折扣、并发加价同时命中 | 按代码书写顺序作用，结果随版本漂移 | 规则表带 priority，按 (priority, id) 排序后作用 | `app/modules/m2_billing/contract.py` | `tests/test_billing.py::test_trap19_rule_order_independent_of_registration` |
| TRAP-23 | 22:40 起 90 分钟、23:30 起 1 小时跨零点 | 整段按起始时段定价，或把 06:00 算进夜间 | 小时桶左闭右开 `[桶, 桶+1h)` 逐个计价，跨日续切 | `app/modules/m2_billing/units.py` | `tests/test_billing.py::test_trap23_six_o_clock_bucket_is_day_rate` |
| TRAP-24 | 300 分按 3 份平分，逐份四舍五入只剩 299 | 分摊前后合计不等，尾差找不到 | 最大余额法分配，余额相同按部门名序定序，守恒断言 | `app/modules/m4_alloc/remainder.py` | `tests/test_alloc.py::test_trap24_naive_rounding_breaks_conservation` |
| TRAP-26 | 两个人同时改预算，旧页面后提交 | 后提交无条件覆盖新结论 | 批次 version 乐观锁，`expected_version` 落后返回 409 | `app/modules/m5_gate/batchkey.py` | `tests/test_gate.py::test_trap26_stale_version_is_rejected` |
| TRAP-27 | 申诉把查询改到 23:30 起 1.5 小时，零点后那 1 小时被丢掉 | 只按起始小时或截到当天 24:00 计价，跨零点部分少算 | 左闭右开逐桶切、跨日续切，并用独立分桶路径与计费金额对账（`Q_APPEAL_BUCKET`） | `app/modules/m7_appeal/buckets.py` | `tests/test_m7_appeal.py::test_trap27_hours_after_midnight_are_not_dropped` |
| TRAP-28 | 申诉把一条查询从 A 部门改到 B 部门 | 只往新部门加、不从旧部门减，总额被算重一遍 | 改判后整批重算份额与部门账，落库前校验 总额 = 分摊 + 未分摊、分摊 = 保留 + 截断 | `app/modules/m7_appeal/service.py` | `tests/test_m7_appeal.py::test_trap28_rejudgement_keeps_total_conserved` |

## 对账规则

- 行账：数据行 = 有效行 + 去重行 + 隔离行 + 空行
- 金额账：总额 = 分摊合计 + 未分摊
- 部门账：分摊合计 = 预算内保留 + 被截断
- 尾差账：每条共享查询的份额合计 = 该查询成本（差额恒为 0）
- 版本账：改判后 部门分摊合计 = 各部门行合计 = 总额 − 未分摊；回滚写入新版本，旧版本快照不被修改
- 四账在页面 `/` 与 `/allocations` 上显示，导出中带同一批整数分。

## 反例与不变量

- 反例（朴素实现会挂）：`test_trap24_naive_rounding_breaks_conservation`（逐份四舍五入合计 99 分）、`test_trap10_float_accumulation_loses_a_cent`（float 累加少 1 分）、`test_trap16_truncated_last_row_isolated`（把半行当有效数据）。
- 不变量（守恒）：`tests/test_alloc.py::test_trap24_largest_remainder_conserves_query_cost`、`tests/test_alloc.py::test_every_query_shares_sum_to_its_cost`。
- 不变量（幂等）：`tests/test_e2e.py::test_rerun_same_source_returns_same_batch`（同源重跑返回同一批次，行数与合计不变）。
- 不变量（行账）：`tests/test_quality.py::test_row_account_is_balanced`。
- 反例（v2）：`test_trap27_appeal_reprice_uses_each_hour_bucket`（整段按起始时段算得 84 分，逐桶算 53 分）、`test_trap27_hours_after_midnight_are_not_dropped`（只算首小时得 11 分，续切得 32 分）、`test_trap28_rejudgement_keeps_total_conserved`（只加不减把总额算成 7182 + 358）。
- 不变量（改判守恒）：`tests/test_m7_appeal.py::test_trap28_rejudgement_keeps_total_conserved`、`tests/test_m7_rollback.py::test_rollback_keeps_amount_account_balanced`。
- 不变量（重放幂等）：`tests/test_m7_appeal.py::test_replaying_same_appeal_keeps_dept_totals`（同一条申诉再提交，部门分摊不变，只升版本）。
- 端到端：`tests/test_e2e.py::test_dirty_sample_runs_from_import_to_export`。

## 已知不覆盖

- 只支持单个 CSV 账单文件；不做多文件拼接与跨月合并，月份归属靠文件名与记录时间人工对齐。
- 预算只按部门一档，没有项目级、月度滚动与跨期结转。
- 共享查询的权重取「部门时长 × 部门扫描量」，不区分读写类型，也不做按列裁剪的细粒度计量。
- 闸门只做一次性排序截断，没有分阶段配额（先保生产再保探索）；申诉是逐条改判，不支持成批合并成一版。
- 申诉只能改登记的几列；改原始字段（并发、收益分）要重新导入。版本快照只保留在当前批次，重新导入会重建。
- 时间只处理带偏移与 `+08:00` 本地两种写法，历史数据里的纯 UTC 秒级时间戳需要先转 ISO。
