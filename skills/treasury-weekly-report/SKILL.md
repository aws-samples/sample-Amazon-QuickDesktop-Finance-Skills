---
name: treasury-weekly-report
display_name: 资金周报生成器
description: "生成企业集团资金管理周报——当用户提到'资金周报'、'treasury report'、'周报生成'、'资金分析'、'资金管理报告'时激活。支持从上传文件或OneDrive自动拉取数据，输出16维度分析的MD+HTML仪表盘+PDF三件套。"
icon: "💰"
trigger: 资金周报 treasury weekly report 资金管理 周报生成 资金分析
patterns:
  - {pattern: "资金周报", confidence: 0.95}
  - {pattern: "treasury.*report", confidence: 0.90}
  - {pattern: "资金管理.*报告", confidence: 0.85}
depends-on: [financial-local-safety]
version: "1.1.0"
inputs:
  - name: bank_flow_file
    description: "银行流水明细Excel（含'流水明细'和'账户信息'两个sheet）。本地安全模式下须位于授权本地文件夹内"
    type: path
    required: true
  - name: investment_ledger_file
    description: "理财台账Excel（含'在投理财'和'本周到期'两个sheet）"
    type: path
    required: true
  - name: capital_plan_file
    description: "资金计划表Excel（含'本周资金计划'sheet，包含计划vs实际对比）"
    type: path
    required: true
  - name: last_week_summary_file
    description: "上周资金汇总Excel（含'上周每日汇总'和'上周关键指标'sheet）"
    type: path
    required: true
  - name: alert_rules_file
    description: "预警规则表Excel（含'账户预警规则'sheet）。首次提供后自动存入Agent Files复用"
    type: path
    required: false
    default: "agent_files/treasury/预警规则表.xlsx"
  - name: report_week
    description: "报告周次，如'W24'或'2026W24'，自动从数据中推断"
    type: string
    required: false
  - name: company_name
    description: "集团名称，用于报告标题"
    type: string
    required: false
    default: "示例公司02"
  - name: local_safety_mode
    description: "本地安全模式：true=数据全程设备端处理不上云（敏感数据推荐）；false=云端沙箱处理（一般数据/需复合工具）"
    type: string
    required: false
    default: "true"
tools: [load_skill, folder_list, list_outposts, file_read, run_python, run_python_with_write, run_python_on_device, run_python_on_device_with_write, file_write, open_in_session_tab, file_copy]
scripts: [treasury_pipeline.py, output_guard.py, self_check.py]
id: 5128ee2f08354cf6b7936ee413656a48
---

## Overview

端到端资金管理周报生成技能。从5份Excel数据源（银行流水、理财台账、资金计划、预警规则、上周汇总）自动完成数据校验→16维度指标计算→风险热力图→可视化仪表盘→PDF正式版的全流程，并输出分级管理建议。适用于集团型企业（多子公司、多银行账户、多币种）的资金管理部周报场景。

**安全定位**：银行流水/资金明细属敏感财务数据。本 skill 默认启用 `local_safety_mode=true`，遵守 `financial-local-safety` 本地安全契约——数据在设备端（outpost）读取与计算，敏感明细写本地文件，stdout 只回无害确认。仅当用户明确处理非敏感数据、或需要边算边用云端复合工具时，才切换到 `local_safety_mode=false`（云端沙箱）。

## Workflow

### Step 0: 本地安全模式判定（强制第一步）
- **Mode**: `deterministic`
- **Tool**: `load_skill`, `folder_list`, `list_outposts`
- **Input**: 5份Excel路径, `{{local_safety_mode}}`
- **Output**: 执行模式确认（本地/云端）+ 校验结果
- **Validate**:
  - 若 `local_safety_mode=true`：加载 `financial-local-safety` 并遵守四条铁律；用 `folder_list` 确认数据文件在**授权本地文件夹**内（非云端会话工作区 `backend/.../workspace/attached_files/`）；用 `list_outposts` 确认设备**在线**
  - 计算与写文件步骤（Step 2-10）统一使用 `run_python_on_device` / `run_python_on_device_with_write`
  - 若 `local_safety_mode=false`：如实告知用户「数据将由云端沙箱处理」，沿用 `run_python`
- **On failure**: 本地模式下文件不在授权夹或设备离线 → 停止并提示，不得静默降级到云端

### Step 1: 数据获取与校验
- **Mode**: `agentic`
- **Tool**: `file_read`, `run_python`（本地模式用 `run_python_on_device`）
- **Input**: 用户上传的5份Excel文件路径，或OneDrive/SharePoint路径
- **Output**: 数据加载确认 + 质量报告（行数/列数/缺失值/日期范围）
- **Validate**: 5份文件全部成功读取，关键列无缺失
- **On failure**:
  - 文件缺失：提示用户补充
  - 列名不匹配：尝试模糊匹配，失败则列出期望列名让用户确认
  - 预警规则文件未提供：从 `agent_files/treasury/预警规则表.xlsx` 读取缓存版本

> ⚠️ 本地安全模式提醒：OneDrive/SharePoint 属云端连接器，从其拉取的数据已过云端。若要求「绝不上云」，须使用授权本地文件夹内的文件，不要从云端连接器或聊天框附件取数。

**预警规则持久化逻辑：**
1. 首次运行：用户提供预警规则表 → 处理完毕后 `file_copy` 到 `agent_files/treasury/预警规则表.xlsx`
2. 后续运行：若用户未提供该文件，自动从 Agent Files 读取
3. 若用户提供新版本，覆盖旧版本

**自动推断周次：** 从银行流水的日期范围推断 `report_week`（如流水日期为6/8-6/12 → W24）。

### Step 2: 核心指标计算
- **Mode**: `deterministic`
- **Tool**: `run_python`（本地模式用 `run_python_on_device`）执行 `treasury_pipeline.py` 的 `compute_core_metrics`
- **Input**: Step 1 加载的数据
- **Output**: 每日余额趋势、收付结构汇总、环比对比数据
- **Validate**: 日终余额合理（正数、在历史范围内），收付合计与计划表一致；**stdout 只回汇总指标，不 print 逐笔明细**
- **On failure**: 若余额出现不合理值（负数/极端值），切换为仅使用计划表的宏观数据

关键计算逻辑：
- 每日余额 = 上周五收盘余额 + 累计净流量（使用计划表实际合计×流水日比例分配）
- 收付分类：按资金计划表的类别汇总实际收入/支出（脚本不按摘要/用途自动归类）
- 环比：自动读取上周关键指标表对比

### Step 3: 异常检测
- **Mode**: `deterministic`
- **Tool**: `run_python`（本地模式用 `run_python_on_device`）执行 `detect_anomalies`
- **Input**: 流水数据 + 预警规则表
- **Output**: 触发预警列表。参考实现产出 W003单笔超限 / W004大额摘要缺失（>1000万且摘要为空）/ W005异常时间（22:00–6:00）；W001余额低于预警线与连续低余额尚未实现（见已知局限）
- **Validate**: 每条预警包含完整字段（日期/子公司/金额/规则类型），**明细写本地文件，stdout 只回预警条数统计**
- **On failure**: 若预警规则表结构不匹配，降级为仅检测摘要缺失和大额交易

### Step 4: 风险矩阵与热力图数据
- **Mode**: `deterministic`
- **Tool**: `run_python`（本地模式用 `run_python_on_device`）执行 `compute_risk_matrix`
- **Input**: 流水数据 + 预警规则
- **Output**: 子公司×日期百分位风险矩阵（Top15×5日）
- **Validate**: 热力图数据点完整（15×5=75个）
- **On failure**: 若子公司数不足15家，使用全部子公司

### Step 5: 扩展维度分析
- **Mode**: `agentic`
- **Tool**: `run_python`（本地模式用 `run_python_on_device`）
- **Input**: 全部数据源 + Step 2-4 计算结果
- **Output**: 8个扩展维度的分析数据（应收应付账龄/外汇敞口/授信额度/资金池/滚动预测/融资利差/合规内控/套保监控）
- **Validate**: 每个维度有对应数据表或摘要
- **On failure**: 若某维度数据不足，标注"数据待补充"并跳过

扩展维度说明：
- 外汇定价使用利率平价(IRP) + Garman-Kohlhagen模型（sandbox无scipy，`norm_cdf` 用标准库 `math.erf` 实现：N(x)=0.5·(1+erf(x/√2))）
- 账龄/授信/套保等维度需要额外数据源，首次运行时使用模拟数据并标注
- 后续可接入ERP/期货系统真实数据

### Step 6: 管理建议生成
- **Mode**: `agentic`
- **Input**: Step 2-5 全部分析结果
- **Output**: 5-8条分级管理建议（🔴紧急/🟡关注/🟢优化）
- **Validate**: 每条建议有具体数据支撑、明确责任方和时限
- **On failure**: 至少输出3条基于核心指标的建议

建议生成逻辑：
- 🔴紧急：预警事项（参考实现产出 W003–W005）、余额逼近安全线、授信即将到期
- 🟡关注：执行偏差>15%、外汇敞口未覆盖、套保率低于目标
- 🟢优化：理财收益提升空间、闲置资金压缩、期限结构优化

### Step 7: 生成Markdown周报
- **Mode**: `deterministic`
- **Tool**: `file_write`（本地模式用 `run_python_on_device_with_write` 写本地目录）, `open_in_session_tab`
- **Input**: 全部计算结果
- **Output**: `{company}_W{week}_资金周报.md` — 16章完整版（本地模式写授权本地目录）
- **Validate**: MD文件包含16个二级标题
- **On failure**: 检查是否有XML特殊字符（`<`/`>`/`&`）未转义

### Step 8: 生成HTML可视化仪表盘
- **Mode**: `deterministic`
- **Tool**: `file_write`（本地模式用 `run_python_on_device_with_write`）, `open_in_session_tab`
- **Input**: 全部计算结果（JSON格式嵌入HTML）
- **Output**: `{company}_W{week}_仪表盘.html` — 多标签页交互仪表盘（含子公司风险热力图）
- **Validate**: HTML文件包含 ECharts 初始化代码（echarts.init），所有图表容器div存在
- **On failure**: 逐图表排查数据绑定

> 本地安全模式变通：若敏感明细需可视化，先在设备端算出**聚合/脱敏结果**，再由聚合结果驱动图表；图表数据不含逐笔敏感明细。

HTML技术栈：
- ECharts 5（Apache-2.0；优先从 `/vendor/echarts/echarts.min.js` 加载，缺失时回退 jsDelivr CDN；热力图 heatmap、仪表盘 gauge 为内置图表类型，无需额外模块；每个图表容器需显式设置高度，并在窗口 resize 时调用 chart.resize()）
- 使用 `var(--color-*)` 主题变量适配深浅色模式
- Tab切换使用原生JS，无需框架

### Step 9: 生成PDF正式版
- **Mode**: `deterministic`
- **Tool**: `run_python`（本地模式用 `run_python_on_device_with_write`）(ReportLab)
- **Input**: 全部计算结果 + matplotlib图表PNG
- **Output**: `{company}_W{week}_资金周报.pdf` — 含图表的打印版
- **Validate**: PDF页数合理（8-12页），包含页眉页脚和密级标识
- **On failure**:
  - LayoutError → 检查Paragraph文本中的`<`未转义（用`re.sub(r'<(?![/a-zA-Z])', '&lt;', text)`）
  - 图表缺失 → 确认matplotlib chart PNG已生成

PDF技术要点：
- 中文字体：`STSong-Light`（CID字体，无需安装）
- matplotlib图表先存为PNG（dpi=150），再用`Image()`嵌入
- 使用`KeepTogether`防止内容跨页断裂
- 页眉页脚通过`onLaterPages`回调实现

### Step 10: 沉淀本周汇总（供下期环比）
- **Mode**: `deterministic`
- **Tool**: `run_python_with_write`（本地模式用 `run_python_on_device_with_write`）, `file_copy`
- **Input**: Step 2 核心指标
- **Output**: `上周汇总_W{week}.xlsx`（含聚合指标，非逐笔明细）
- **Validate**: 文件包含'每日汇总'和'关键指标'两个sheet
- **On failure**: 保存为JSON备份

## Output

每次运行产出5个文件（本地安全模式下写入授权本地目录，敏感明细不上云）：

| 文件 | 格式 | 说明 |
|------|------|------|
| `{company}_W{week}_资金周报.md` | Markdown | 16章完整版文字报告 |
| `{company}_W{week}_仪表盘.html` | HTML | 交互式可视化（多标签页·含子公司风险热力图） |
| `{company}_W{week}_资金周报.pdf` | PDF | 正式打印版（8-12页·页眉页脚·密级标识） |
| `上周汇总_W{week}.xlsx` | Excel | 沉淀供下期环比（聚合指标） |
| `{company}_W{week}_审计留痕.json` | JSON | **过程可回溯**：时间戳+五步指标+异常明细+数据问题，供审计追溯 |

审计留痕 JSON 由 `run_pipeline(file_paths, out_path=..., out_dir=...)` 写出：只写入 `out_dir`（未给出时为当前目录下 `finance_outputs/`），`out_path` 只能是该目录内的 `.json` 文件名，含 `..`、子目录、目录外绝对路径或目标为符号链接时抛 `OutputPathError`，不写任何文件；文件权限 0600；控制台只打印数据问题条数与结果路径，不打印问题原文；每次写出在同目录 `audit_log.jsonl` 追加一条记录（时间、操作者、引擎及其 SHA-256、`file_paths` 中各输入文件的 SHA-256、结果哈希、上一条记录哈希），不含明细与个人信息。不传 `out_path` 时不写文件也不记日志。

## Lessons Learned

### Do
- **过程可回溯（查得清）**：调用 `run_pipeline(file_paths, out_path=..., out_dir=...)` 传入 `out_path`（`out_dir` 只能是用户授权的本地输出目录），把完整过程记录（时间戳+五步骤指标+异常明细+数据问题）落盘为审计留痕 JSON——回溯的前提是留痕落盘，不落盘则跑完即散。
- **执行前先过 Step 0**：判定本地/云端模式；本地模式确认文件在授权夹、设备在线。
- **先用计划表宏观数据校准**：流水文件可能包含大量内部循环交易（归集/调拨），直接汇总会严重偏离实际。以计划表的`【合计】`行作为 ground truth，流水数据仅用于日内比例分配和异常检测。
- **XML转义**：所有传入ReportLab `Paragraph()` 的文本必须转义`<`为`&lt;`。使用`re.sub(r'<(?![/a-zA-Z])', '&lt;', text)`全局处理。
- **matplotlib中文**：设置`plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'Heiti SC', 'PingFang SC']`。
- **热力图百分位法**：用相对排名（百分位）而非绝对值，避免模拟数据的极端值导致热力图饱和。
- 本地模式下明细写本地文件，stdout 只回「多少条、写到哪」。

### Don't
- **不要用`scipy`**：sandbox中不可用；`norm_cdf` 用标准库 `math.erf` 实现（N(x)=0.5·(1+erf(x/√2))）。不要手写 erf 近似多项式——漏掉 x/√2 换元会让期权价格出现系统性偏差：平值附近明显偏高，深度实值或虚值时则可能偏低。
- **不要修补已创建的Paragraph对象**：ReportLab内部有解析缓存，遇到问题必须重建整个story列表。
- **不要直接汇总流水金额作为宏观指标**：内部归集/调拨会让收支被重复计算。以演示数据集（`gen_samples.py --demo`）为例，50,000 笔流水直接汇总，收方合计约 531 亿元，而计划表实际收入只有 6.22 亿元。
- **本地安全模式下：不要用云端沙箱 `run_python` 处理真实流水、不要 print 逐笔明细、不要从聊天框附件/云端连接器取敏感数据、设备离线不降级。**

### Common Failures
| 错误 | 原因 | 修复 |
|------|------|------|
| `LayoutError: Flowable too large` | Paragraph含未转义`<` | 全局`re.sub`转义 |
| `FileNotFoundError` | 分支workspace路径不同 | 使用`WORKSPACE_DIR`或本机原生路径拼接 |
| 热力图全部100/饱和 | 使用绝对值而非百分位 | 改用日内跨公司百分位排名 |
| matplotlib中文方块 | 字体配置缺失 | 设置`font.sans-serif`列表 |
| 误把附件/云端文件当本地 | 路径实为云端 | Step 0 用 `folder_list` 核实路径前缀 |
| on-device 执行不能再调其他工具 | 设备端限制 | 把计算+写文件合并到 on-device 代码里 |

### When to Ask the User
- 数据文件结构发生变化（新增列/改名）时需确认映射。
- 首次运行外汇/套保等扩展维度时，需确认市场参数（汇率/利率/波动率）。
- 预警规则阈值需要调整时。
- 数据敏感级别不明确、或文件不在授权本地文件夹时，确认走本地还是云端模式。
- 管理建议中涉及具体业务判断（如是否催收某客户）时。

## 自检与演示数据

- 自检：`python3 scripts/self_check.py`。样例在 `samples/clean/`、`samples/error/`，黄金答案 `samples/expected.json` 由 `samples/gen_samples.py` 按构造推导。退出码 0 = 全部通过，1 = 失败，2 = 无失败但有 KNOWN GAP。当前 KNOWN GAP：W001 余额低于预警线未实现；规则表子公司名称与流水不一致（如尾随空格）时 W003 漏报。
- 演示数据：`python3 skills/treasury-weekly-report/samples/gen_samples.py --demo`（在仓库根目录运行）按固定随机种子生成约 5 万笔流水的 5 份工作簿（写入 `samples/demo/`，不入库），用 `treasury_pipeline.py` 实跑，并把结果摘要写入 `samples/demo_summary.json`。讲解页 solution.html 上的样例数字均来自该摘要。
- 脚本 `treasury_pipeline.py` 计算：每日余额趋势、收支结构（按计划表类别）、W003–W005 预警、子公司风险热力图、计划执行偏差，并提供 `forward_rate()` / `gk_option()` 定价函数；理财动态、环比解读、8 个扩展维度与管理建议由 Agent 按本 SKILL.md 生成。

## 判据覆盖声明与已知盲区

> 本场景判据可捕获：**守恒、恒等式** 类错误。但每类自动校验都有其数学上的固有盲区——以下显式列出「判据抓不到什么」及对应处置。**「知道自己抓不到什么」比「能抓到什么」更是可信交付的证明。**

### 已知盲区（判据抓不到的错误类型）

| 盲区 | 关联判据 | 判据为何抓不到 | 处置 | 可闭合性 |
|------|:--:|------|------|:--:|
| 净额掩盖 | 守恒 | 正负成对错误相互抵消，净差为0但双向各错（如银行多记100+企业多记100） | 分向列示+分向限额；加「笔数守恒」配合金额守恒 | 🟢可自补 |
| 口径与时点 | 恒等式 | 等式内部自洽，但取数口径（时点/主体/准则）错，等式照样成立 | 口径三元组声明（时点+主体+准则）；跨期衔接校验（本期期初=上期期末） | 🟢可自补 |

### 可闭合性三档
- 🟢 **可自补**：加一段确定性检查即可闭合，零外部依赖——应直接实现。
- 🟡 **需外部锚点**：缺的是「第二个独立源」而非代码，须接入权威源（工商股权/银行/金税/合同）后方能校验——对客户标注为「需接入 X 源」。
- 🔴 **不可闭合**：结构上无验证基准（如员工自报项、需实物确认项），只能显式声明并交人工。

### 通用盲区补丁位（据本场景判据勾选）
- ☐ **范围完备性**：双向名单比对（A有B无 / B有A无 分别列示）——防「未进检查范围」的记录永远全绿
- ☐ **笔数守恒**：金额守恒之外加笔数守恒——抓成对反向错误
- ☐ **分向列示+分向限额**：正/负、双向单边分别设限——防净额掩盖
- ☐ **交叉白名单**：业务类型×允许值，白名单版本化+生效日——防「合法但不适用」
- ☐ **口径三元组**：时点+主体+准则声明 + 跨期衔接（本期期初=上期期末）——防口径错
- ☐ **归一化+模糊指纹**：去空格/全半角/前导零 + 组合键指纹——防唯一性绕过
- ☐ **外部锚点**：关键项引第三方独立源交叉验证——防同源错误
