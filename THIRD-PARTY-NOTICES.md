# Third-Party Notices

本仓库自身以 MIT-0 授权（见 [LICENSE](LICENSE)）。下列第三方组件不随仓库分发源码，而是由页面从公共 CDN 加载，或由技能脚本在运行时导入；它们各自遵循自己的许可证。

清单基于对全部已跟踪 `*.html` 中外部 `<script src>` / `<link href>` 的检索，以及对 `skills/*/scripts/*.py` 导入语句和 `SKILL.md` 的检索。

---

## Apache ECharts 5.5.1

| 项目 | 内容 |
|------|------|
| 组件 | Apache ECharts 5.5.1（The Apache Software Foundation） |
| 许可证 | Apache License 2.0 |
| 来源 | [apache/echarts LICENSE](https://github.com/apache/echarts/blob/5.5.1/LICENSE) · [NOTICE](https://github.com/apache/echarts/blob/5.5.1/NOTICE) · [npm: echarts](https://www.npmjs.com/package/echarts) |

**使用位置**

| 文件 | 方式 |
|------|------|
| `index.html` | CDN：`https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js` |
| `overview-guided.html` | CDN：同上 |
| `skills/treasury-weekly-report/solution.html` | CDN：同上（资金流向桑基图） |
| `skills/finance-monthly-reconciliation/scripts/dashboard_template.html` | 运行时模板：若运行环境提供 `/vendor/echarts/echarts.min.js` 则优先加载（不在本仓库中），否则使用上述 jsDelivr CDN |
| `skills/treasury-weekly-report/SKILL.md`、`skills/finance-monthly-reconciliation/SKILL.md`、`README.md` | 指示运行时生成的 HTML 仪表盘使用 ECharts 5 |

离线部署时需自行放置 `echarts.min.js`，并随附 Apache-2.0 的 LICENSE 与 NOTICE。

---

## Font Awesome Free 6.5.0

| 项目 | 内容 |
|------|------|
| 组件 | Font Awesome Free 6.5.0（Fonticons, Inc.），经 cdnjs 加载 `https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css` |
| 许可证 | 图标：CC BY 4.0；字体文件：SIL OFL 1.1；代码（CSS/JS 等非字体、非图标文件）：MIT |
| 来源 | [Font Awesome 6.5.0 LICENSE.txt](https://github.com/FortAwesome/Font-Awesome/blob/6.5.0/LICENSE.txt) · [Font Awesome Free License](https://fontawesome.com/license/free) |
| 使用位置 | `index.html`、`overview-guided.html`、`skills/treasury-weekly-report/solution.html` |

CC BY 4.0 要求署名；本文件即为署名声明。

---

## Python 库

场景 #3–#20 的脚本只使用 Python 标准库。下列第三方库仅与 #1、#2 相关。

| 库 | 许可证 | 使用位置 | 来源 |
|----|--------|----------|------|
| pandas | BSD-3-Clause | 导入：`skills/treasury-weekly-report/scripts/treasury_pipeline.py`、`skills/finance-monthly-reconciliation/scripts/reconciliation_engine.py` | [PyPI](https://pypi.org/project/pandas/) |
| numpy | BSD-3-Clause（发行包另含 0BSD、MIT、Zlib、CC0-1.0 授权的组件） | 导入：同上 | [PyPI](https://pypi.org/project/numpy/) |
| XlsxWriter | BSD-2-Clause | `reconciliation_engine.py` 通过 `pd.ExcelWriter(..., engine='xlsxwriter')` 使用，不直接 import | [PyPI](https://pypi.org/project/XlsxWriter/) |
| openpyxl | MIT | 不直接 import；`pandas.read_excel` 读取 `.xlsx` 时默认使用，见 `treasury_pipeline.py`、`reconciliation_engine.py` | [PyPI](https://pypi.org/project/openpyxl/) |
| matplotlib | Matplotlib License（基于 PSF 许可） | 未被随附脚本导入；`skills/treasury-weekly-report/SKILL.md` 指示运行时生成的代码使用它 | [PyPI](https://pypi.org/project/matplotlib/) |
| ReportLab | BSD 许可（开源工具包） | 未被随附脚本导入；`skills/treasury-weekly-report/SKILL.md` 指示运行时用它生成 PDF | [PyPI](https://pypi.org/project/reportlab/) |

许可证信息于本仓库整理时核对自各库的 PyPI 元数据（`license_expression` 或 `license` / classifier 字段）。
