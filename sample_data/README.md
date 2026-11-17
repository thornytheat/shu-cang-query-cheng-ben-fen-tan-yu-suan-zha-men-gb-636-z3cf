# 样例说明

| 文件 | 编码 | 埋了什么 |
|---|---|---|
| `bills_2026_11_dirty.csv` | UTF-8 + BOM，CRLF | 千分位、全角数字、MB/GB/TB 混写、小写 gb、`N/A` 哨兵、重复 query_id 不同扫描量、完全相同重复行、空行、字段全空行、分段重复表头、坏时间、坏单位、多一列、末行截断、跨零点夜间查询、共享查询（含权重全零） |
| `bills_2026_11_full.csv` | UTF-8 + BOM | 28 行干净数据，4 个部门，用于端到端与守恒断言 |
| `bills_2026_11_gb18030.csv` | GB18030 | 中文部门名与项目名，用于编码回退（声明 utf-8 也能读） |
| `bills_bignum.csv` | UTF-8 | 超过 2^53 的字节数、科学计数法扫描量，用于大整数保形 |
| `bills_empty_header.csv` | UTF-8 | 只有表头（空态一） |
| `bills_all_empty.csv` | UTF-8 | 表头 + 两行字段全空（空态二） |
| `bills_blank.csv` | UTF-8 | 只有空行，没有表头（空态三，会被拒绝） |
| `budgets.csv` | UTF-8 | 全角预算、千分位预算、空预算行 |

导入用 `POST /api/import {"source": "文件名"}`，只接受本目录下的 csv，不能带路径。
