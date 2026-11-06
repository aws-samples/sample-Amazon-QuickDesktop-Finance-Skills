---
name: three-way-match
display_name: 三单匹配核对
description: "对采购订单(PO)、入库单(GRN)、采购发票三张单据做交叉一致核对，识别数量差异、价格差异、缺入库单、缺发票、重复发票、超额开票。当用户提到\"三单匹配\"\"三单核对\"\"采购发票核对\"\"PO-GRN-Invoice\"\"付款前核对\"时激活。"
icon: "🔗"
trigger: 三单匹配
integration:
depends-on: [financial-local-safety]
scripts: [three_way_match_v2.py, output_guard.py, self_check.py]
inputs:
  - name: data_dir
    description: "数据目录，含 purchase_orders.csv（po_id, line_no, supplier_id, supplier, item, unit, qty, unit_price, amount, order_date）、goods_receipts.csv（grn_id, po_id, line_no, supplier_id, qty_received, recv_date）、invoices.csv（inv_no, po_id, line_no, supplier_id, qty_billed, unit_price, amount, inv_date）；支持多行订单、分批收货与分批开票"
    type: path
    required: true
tools: [load_skill, folder_list, list_outposts, run_python_on_device, run_python_on_device_with_write, file_copy]
id: 70c759cbaae14ec1b0b6b981ca57c553
version: "1.2.0"
---

# 三单匹配核对

## Overview
在付款前，对采购订单(PO)、入库单(GRN)、采购发票三张单据做交叉一致核对——只有三方数量、价格一致才放行付款。识别六类问题：数量差异、价格差异、缺入库单、缺发票、重复发票、超额开票。核心验收判据是**交叉一致**（同一笔业务在三个独立来源中的数值必须一致）。这是采购付款(P2P)风控的基石场景。

## 数据敏感性
采购与供应商数据属敏感财务数据。**执行前必须先加载 `financial-local-safety` skill**，遵守本地安全执行契约：数据只在本地读取、计算在设备端、结果只写本地。若源数据在本地授权文件夹，用 `run_python_on_device` 就地处理，不上云。

## 上层判据（不折旧 · 跨平台）
| 判据 | 规则 | 触发 |
|------|------|------|
| **交叉一致** ⭐ | GRN 数量 == PO 数量 | 不等 → difference |
| **交叉一致** ⭐ | 发票单价 == PO 单价（容差0.01）| 不等 → difference |
| **唯一性** | 同 PO 下发票 (数量,单价) 不重复 | 重复 → exception |
| **完整性** | 每个 PO 必有 GRN 和发票 | 缺失 → exception |
| **边界** | 开票数量 ≤ PO 数量 | 超额 → exception |
| **守恒（自检）** | Σ状态数 == 总 PO 数 | 不等 → 分析错误 |

## Workflow

### Step 1: 加载安全契约
- **Mode**: `deterministic`
- **Tool**: `load_skill`；随后按 financial-local-safety Step 1 用 `folder_list`、`list_outposts` 做前置校验
- **Input**: 无
- **Output**: 已加载 financial-local-safety
- **Validate**: 契约四铁律已知晓
- 采购/供应商数据敏感，先加载 `financial-local-safety`。

### Step 2: 读取三张单据
- **Mode**: `agentic`
- **Input**: `{{data_dir}}`
- **Output**: 三个 DataFrame / 记录列表
- **Validate**: 三表非空，关键字段（po_id 等）存在
- **On failure**: 字段名不符时提示用户核对表头；若文件大于 50MB 或在设备本地，改用 `run_python_on_device`。

### Step 3: 执行三单匹配
- **Mode**: `deterministic`
- **Tool**: `run_python_on_device`（计算只在设备端进行，见 financial-local-safety 铁律 2）
- **Input**: 三个路径，调用 `scripts/three_way_match_v2.py` 的 `check(data_dir)`
- **Output**: 逐 PO 匹配结果 + 汇总（匹配率、金额敞口）
- **Validate**: 守恒 assert 通过（Σ状态数==总数）
- **On failure**: assert 失败说明分类逻辑漏项，检查 issues 归类。

### Step 4: 生成讲解页/仪表盘（可选）
- **Mode**: `agentic`
- **Tool**: `run_python_on_device_with_write`（写入授权本地目录）
- **Input**: Step 3 的分析 JSON
- **Output**: 纯 SVG/CSS 自讲解 HTML（甜甜圈/横条/异常表 + 底部 TTS 控制栏）
- **Validate**: 单屏不溢出、script 花括号配平、无 lookbehind 正则
- **On failure**: 见 Lessons Learned 的渲染陷阱。

### Step 5: 归档
- **Mode**: `deterministic`
- **Tool**: `file_copy`
- **Input**: 全部产物
- **Output**: 存入 `agent_files/artifacts/finance_scenarios/08_three_way_match/`

## Output
- 数据质量：`data_issues` 列出无法解析或不应留空却留空的数值单元格（文件、行号、字段、原值），该行不参与计算、不按 0 计；`all_ok` 为总判定，有任何数据质量问题或问题标签时为 `False`
- 输出与留痕：命令行 `python scripts/three_way_match_v2.py <数据目录> [结果文件名.json] [--out-dir <授权本地输出目录>]`；结果只写入 `--out-dir`，未给出时为当前目录下 `finance_outputs/`（默认文件名 `analysis.json`）；结果文件名只能是该目录内的 `.json` 文件名，含 `..`、子目录、目录外绝对路径或目标为符号链接时一律拒绝并以退出码 2 结束，未知选项同样退出码 2；文件权限 0600；stdout 只回一行状态（总判定、条数、数据质量问题数、问题标签类数、结果文件与审计日志路径），不回金额与明细；每次写出在同目录 `audit_log.jsonl` 追加一条记录（时间、操作者、引擎及其 SHA-256、非路径参数、输入文件 SHA-256、结果哈希、上一条记录哈希），不含明细与个人信息；直接调用函数不写文件也不记日志，需要归档的结果必须走命令行入口
- 逐 PO 三单匹配清单（matched / difference / exception + 问题标签）
- 验收报告：匹配率、差异/异常笔数、金额敞口、供应商风险排名
- 可选：自讲解 HTML 仪表盘

## Lessons Learned
### Do
- 三方数值比对必须用容差（0.01）判等，避免浮点误差误报价格差异。
- 状态分三档（matched/difference/exception），差异与异常分开——差异可协商，异常须拦截。
- 用守恒 assert（Σ状态数==总数）做独立自检，保证无漏项。

### Don't
- 不要只比对金额——必须分别比数量和单价，金额相等可能是"量增价减"的巧合。
- 不要把超额开票降级为普通差异——超额直接判 exception。

### Common Failures
- 排序 `dict` 类型报 `'<' not supported`：`.sort()` 时用 `key=lambda x: x[0]` 只按可比字段排序。
- CSV 中文表头：用 `encoding="utf-8-sig"` 读取，避免 BOM 污染首列名。

### When to Ask the User
- 表头字段名与契约不一致时，确认映射关系。
- 容差阈值、是否将"缺发票"视为异常等业务口径，按客户规则确认。

## 判据（按订单行）
| 判据 | 规则 | 结果 |
|------|------|------|
| 完整性 | 有发票无入库 → 缺入库单；有入库无发票 → 待开票（正常） | 异常 / 待开票 |
| 供应商一致 | 入库单、发票的供应商 == 订单供应商 | 异常 |
| 数量 | 收货 vs 订单（短收/超收）；**开票 ≤ 收货**；开票 ≤ 订单 | 差异 / 异常 |
| 单价 | 发票单价 == 订单单价 | 差异 |
| 金额恒等式 | 发票金额 == 开票数量 × 单价 | 异常 |
| 时间 | 发票日期不早于首次收货 | 待核实 |
| 发票唯一性 | 发票号归一化后唯一；供应商+金额+日期指纹唯一（抓换号重开） | 异常 |
| 范围完备 | 入库单、发票引用的订单行必须存在 | 异常 |
| 数据质量 | 引用该订单行的入库单或发票因数值无法解析被剔除（见 `data_issues`）；该行可付金额为 0、发票金额全部暂缓；被剔除行的行号本身无法确定时，挂到同一订单的全部行 | 待核实（含无法解析单据） |
| 付款守恒 | Σ发票 == Σ可付 + Σ暂缓；可付 = min(开票, 收货, 订单) × 订单单价 | 自检 |

## 样本数据（演示 · 全部虚构）
示例集团（虚构）8 家虚构供应商、16 种物料，90 张采购订单 158 个订单行；188 张入库单（含分批收货）、169 张发票（含分批开票）、16 行已收货待开票。
- `samples/clean/`：142 行完全匹配 + 16 行待开票，发票全部可付
- `samples/error/`：植入 13 处错误（开票超过收货、单价上浮、发票金额≠数量×单价、缺入库单、发票号变形重复、换号重开、供应商不一致、超量收货、先票后货、超订单开票、无订单发票、无订单入库、发票金额写成千分位无法解析且所引用订单行判待核实），全部命中、无误报


## 自检 vs 正式运行（边界 · 必读）

本 skill 捆绑了 `samples/` 夹具与 `self_check.py`，**仅用于验证脚本本身是否健康，绝不参与正式业务运行**。两者必须严格分开：

| | 正式运行 | 自检（回归测试） |
|---|---|---|
| 输入 | **用户当期数据**，路径由调用者显式提供 | 内置 `samples/{clean,error}/` 夹具 |
| 入口 | `check(PO+GRN+发票目录)` | `python scripts/self_check.py` |
| 目的 | 核对这套业务数据 | 验证脚本行为与黄金答案一致 |
| 何时跑 | 每次业务核对 | 脚本改动后 / 定期体检 |

**铁律**：
- 校验函数**不设指向 samples 的默认路径**——数据路径必填，调用者给什么就核什么，绝不会在漏传时静默去核样本、假报"全部通过"。
- `samples/` 是隔离夹具（体检用），不是"默认数据集"，正式运行永不读它。
- 自检是独立命令（`self_check.py`），和业务运行不共用入口。

> **自检怎么用**：`python scripts/self_check.py` → 对 `samples/clean/` 应全部通过（全平），对 `samples/error/` 应低于 100% 并正好抓出植入的错误，与 `samples/expected.json` 黄金答案逐项比对；一致则退出码 0（脚本健康），不一致则非 0（脚本可能已退化）。植错版是最强验证器——有确定的已知错误数与期望值，脚本一旦退化立刻暴露。

## 判据覆盖声明与已知盲区

> 本场景判据可捕获：**守恒、交叉一致、唯一性** 类错误。但每类自动校验都有其数学上的固有盲区——以下显式列出「判据抓不到什么」及对应处置。**「知道自己抓不到什么」比「能抓到什么」更是可信交付的证明。**

### 已知盲区（判据抓不到的错误类型）

| 盲区 | 关联判据 | 判据为何抓不到 | 处置 | 可闭合性 |
|------|:--:|------|------|:--:|
| 同源错误 | 交叉一致 | 两个「独立」来源实际同源，一起错则互证成功 | 标注每个来源的真实独立性；关键项引外部锚点（工商/银行/金税/合同） | 🟡需外部锚点 |
| 范围盲区 | 交叉一致 | 只核对「已进入两表」的记录，单边未登记项不进对比 | 双向名单比对：A有B无 / B有A无 分别列示 | 🟢可自补 |

> **已接入** `scripts/three_way_match_v2.py`：订单行级匹配（多行、分批）、开票≤收货的付款口径与付款守恒、发票号归一化 + 组合指纹查重、供应商一致、先票后货、无订单单据。

### 可闭合性三档
- 🟢 **可自补**：加一段确定性检查即可闭合，零外部依赖——应直接实现。
- 🟡 **需外部锚点**：缺的是「第二个独立源」而非代码，须接入权威源（工商股权/银行/金税/合同）后方能校验——对客户标注为「需接入 X 源」。
- 🔴 **不可闭合**：结构上无验证基准（如员工自报项、需实物确认项），只能显式声明并交人工。

### 通用盲区补丁位（据本场景判据勾选）
- ☑ **范围完备性**：双向名单比对（A有B无 / B有A无 分别列示）——防「未进检查范围」的记录永远全绿
- ☑ **笔数守恒**：金额守恒之外加笔数守恒——抓成对反向错误
- ☐ **分向列示+分向限额**：正/负、双向单边分别设限——防净额掩盖
- ☐ **交叉白名单**：业务类型×允许值，白名单版本化+生效日——防「合法但不适用」
- ☐ **口径三元组**：时点+主体+准则声明 + 跨期衔接（本期期初=上期期末）——防口径错
- ☑ **归一化+模糊指纹**：去空格/全半角/前导零 + 组合键指纹——防唯一性绕过
- ☐ **外部锚点**：关键项引第三方独立源交叉验证——防同源错误
