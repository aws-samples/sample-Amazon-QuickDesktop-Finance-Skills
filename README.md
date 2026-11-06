> **Disclaimer:** This is sample code, for non-production usage. You should work with your security and legal teams to meet your organizational security, regulatory and compliance requirements before deployment.
>
> **免责声明：** 本仓库为示例代码，仅供非生产用途。部署前，请与贵组织的安全团队和法务团队合作，确保满足组织的安全、监管与合规要求。

# Amazon Quick Desktop Playbook

> 讲清 Amazon Quick on desktop 的能力底座、Skills 机制与企业接入方式，并在此之上沉淀可复用的业务场景方案与技能包。

**页面导航**

| 页面 | 说明 |
|------|------|
| **[Playbook 主页](index.html)** | 完整 Playbook：定位与价值 → 能力模型 → 端到端场景实践 → 落地四步 → 企业接入 → 资源导航 |
| [自讲解版](overview-guided.html) | 定位与价值部分的 10 段语音讲解形态，适合直接照着讲 |
| [技术参考手册](playbook.html) | 内置系统工具明细、权限档位、界面速览、Skills 机制 |
| [财务场景库](finance-scenarios.html) | 财务域 20 个可验证核对场景总览 |

---

## 这是什么

Amazon Quick on desktop 是 Amazon Quick 的原生桌面应用，对你的本地文件、已连接服务以及工作上下文有直接且持续的访问能力。

本 Playbook 分两层：

1. **平台层** —— 桌面端本身：能碰到什么数据、有哪些工具、权限怎么管、Skills 怎么运作、企业怎么接入
2. **场景层** —— 按业务域沉淀的落地方案，每个场景交付「讲解网页 + SKILL.md 技能包 + 脚本」三件套

> Amazon Quick on desktop 已于 2026 年 9 月正式发布（GA），支持 macOS 与 Windows。对话、记忆与知识图谱保存在你的 Amazon Quick 账户中，Web、移动端与桌面端通用；桌面端在此之上加入授权文件夹、本地代码执行、浏览器自动化、本地 MCP 与编码 Agent 等本机能力。套餐与价格见[官方定价页](https://aws.amazon.com/quick/pricing/)，可用区域见[官方区域页](https://docs.aws.amazon.com/quick/latest/userguide/regions.html)，安装包见[官方下载页](https://aws.amazon.com/quick/download/)。

---

## 定位：参考实现

- 这 20 个 Skill 是财务核对场景的参考实现，可按自身流程修改、扩展和优化。
- 仓库中的样例数据、企业名称和金额均为构造，非真实数据。
- 本仓库是示例代码，不直接用于生产（见文首免责声明）。投入生产前，需按自身会计政策和数据校验其中的规则、阈值与容差，并与安全、法务团队一起完成安全、监管与合规评审。威胁与缓解措施见 [THREAT_MODEL.md](THREAT_MODEL.md)，已知局限见[已知局限与生产化建议](#已知局限与生产化建议)。

---

## 平台层：能力底座

下表按落地时的关注点重新组织，不是官方文档的编排 —— 原始目录见[用户指南](https://docs.aws.amazon.com/quick/latest/userguide/amazon-quick-desktop.html)。

| 模块 | 说明 |
|------|------|
| **本地文件访问** | 在 `Customize → Knowledge` 按文件夹授权。添加文件夹只授予访问；全文检索索引（Allow full file context）与知识图谱抽取（Always remember file information）按文件夹分别打开；文件夹访问级别可设 Full Access / Read Only / Ask Each Time |
| **连接器与 MCP** | 内置 Slack / Outlook / Teams / Gmail / Google Calendar / OneDrive 等；MCP Server 支持 Local、Remote、从配置文件 Import |
| **系统工具与权限** | 在 `Customize → Connectors` 逐项启停；每个工具可设 Always Allow / Ask Each Time / Always Deny，并可按单个读写操作细化；默认全部启用、Always Allow |
| **Skills** | 以文件夹为单位、根目录含 `SKILL.md` 的指令集，可附脚本、模板等参考文件；在 `Customize → Skills` 管理，草稿经 Try it 测试后 Publish 生效；系统技能可开关、不可编辑 |
| **定时任务与 Mission Control** | 定时任务（routine）在云端按计划运行，在 Mission Control 统一查看运行状态、待办输入、KPI 与历史产出 |
| **委派编码 Agent** | 通过 ACP 协议把开发类任务派给 Kiro 或 Claude Code |
| **知识图谱与记忆** | 自动抽取人、客户、项目、事件、行动项等实体及关系，保存在你的 Amazon Quick 账户中（按用户隔离）；在 `My context` 查看与管理 |
| **响应模式** | 对话与定时任务可选 Fast / Balanced / Smart，按复杂度取舍速度与质量 |

内置系统工具：Web Search、File operations、Browser Automation、Image Generation、Code Execution、Engram Builder、Knowledge and memory、Agent management、Task management、Chat and notifications、Transcription、Explainability、File indexing and search、Working with your data。

几个容易踩的点：

- **Code Execution 在沙箱中运行 Python** —— 使用沙箱预装的包；同一对话内变量保留；文件访问遵循文件夹授权，另可访问系统临时目录
- **Browser Automation** 默认使用独立的浏览器实例和你的配置文件副本；要直接用你自己的浏览器，在 `Settings → General` 开启 `Use my existing browser`（需开启远程调试，本机应用因此能访问已打开的页面和已登录的会话，不用时建议关闭）
- **定时任务在云端运行** —— 应用关闭或电脑关机也照常执行；但读本机文件、浏览器自动化、本地代码执行这类步骤要求电脑开机、应用运行并保持连接，否则该步骤不可用（本库财务技能都依赖本地文件和本地脚本）

---

## 场景层：业务场景

每个场景交付三件套：讲解网页、`SKILL.md` 技能包、确定性计算脚本。

### 财务域 ✅ 已就绪 · 20 个实践

全部 20 个场景按五个流程域排列，总览见 [财务场景库](finance-scenarios.html)。判据列取自各 `SKILL.md`「判据覆盖声明与已知盲区」一节声明的判据，共六类（守恒、差异归因、恒等式、白名单、交叉一致、唯一性）；#1、#2 的 `SKILL.md` 同样有这份声明。

**R2R 记录到报告**

| # | 实践 | 技能包 | 讲解网页 | 判据 |
|---|------|--------|----------|------|
| #1 | 资金周报生成器 | `treasury-weekly-report` | [方案讲解](skills/treasury-weekly-report/solution.html) | 守恒 · 恒等式 |
| #2 | 月末银行对账 | `finance-monthly-reconciliation` | [自讲解版](skills/finance-monthly-reconciliation/solution.html) | 守恒 · 差异归因 · 交叉一致 |
| #3 | 报表勾稽自检 | `report-tieout-check` | [方案讲解](skills/report-tieout-check/solution.html) | 守恒 · 恒等式 |
| #4 | 总账凭证合规 | `gl-voucher-compliance` | [方案讲解](skills/gl-voucher-compliance/solution.html) | 恒等式 · 白名单 |
| #5 | 关联方往来对账 | `intercompany-reconciliation` | [方案讲解](skills/intercompany-reconciliation/solution.html) | 守恒 · 交叉一致 · 唯一性 |
| #6 | 合并抵消检查 | `consolidation-elimination-check` | [方案讲解](skills/consolidation-elimination-check/solution.html) | 守恒 · 恒等式 · 白名单 · 交叉一致 |
| #7 | 期间费用分摊复核 | `expense-allocation-check` | [方案讲解](skills/expense-allocation-check/solution.html) | 守恒 · 恒等式 · 交叉一致 |

**P2P 采购到付款**

| # | 实践 | 技能包 | 讲解网页 | 判据 |
|---|------|--------|----------|------|
| #8 | 三单匹配核对 | `three-way-match` | [方案讲解](skills/three-way-match/solution.html) | 守恒 · 交叉一致 · 唯一性 |
| #9 | 发票查验与重复报销 | `invoice-verification-dedup` | [方案讲解](skills/invoice-verification-dedup/solution.html) | 白名单 · 唯一性 |
| #10 | 应付账龄分析 | `ap-aging` | [方案讲解](skills/ap-aging/solution.html) | 守恒 · 恒等式 · 交叉一致 · 唯一性 |
| #11 | 供应商对账 | `supplier-reconciliation` | [方案讲解](skills/supplier-reconciliation/solution.html) | 差异归因 · 恒等式 · 交叉一致 · 唯一性 |

**O2C 订单到收款**

| # | 实践 | 技能包 | 讲解网页 | 判据 |
|---|------|--------|----------|------|
| #12 | 应收账龄与坏账 | `ar-aging-baddebt` | [方案讲解](skills/ar-aging-baddebt/solution.html) | 守恒 · 恒等式 · 白名单 · 交叉一致 |
| #13 | 客户对账单生成 | `customer-statement` | [方案讲解](skills/customer-statement/solution.html) | 守恒 · 恒等式 · 白名单 · 交叉一致 · 唯一性 |
| #14 | 收款核销匹配 | `ar-settlement-matching` | [方案讲解](skills/ar-settlement-matching/solution.html) | 守恒 · 白名单 · 交叉一致 · 唯一性 |

**TAX 税务与薪酬**

| # | 实践 | 技能包 | 讲解网页 | 判据 |
|---|------|--------|----------|------|
| #15 | 增值税进销项匹配 | `vat-input-output-matching` | [方案讲解](skills/vat-input-output-matching/solution.html) | 恒等式 · 白名单 · 唯一性 |
| #16 | 增值税申报一致性 | `vat-filing-consistency` | [方案讲解](skills/vat-filing-consistency/solution.html) | 恒等式 · 白名单 · 交叉一致 |
| #17 | 社保公积金基数核定 | `social-insurance-base-check` | [方案讲解](skills/social-insurance-base-check/solution.html) | 恒等式 · 白名单 · 交叉一致 |
| #18 | 薪酬个税复核 | `payroll-iit-check` | [方案讲解](skills/payroll-iit-check/solution.html) | 守恒 · 恒等式 · 白名单 |

**ASSET 资产管理**

| # | 实践 | 技能包 | 讲解网页 | 判据 |
|---|------|--------|----------|------|
| #19 | 固定资产卡片与实物核对 | `fixed-asset-verification` | [方案讲解](skills/fixed-asset-verification/solution.html) | 恒等式 · 白名单 · 交叉一致 |
| #20 | 存货盘点差异处理 | `inventory-count-variance` | [方案讲解](skills/inventory-count-variance/solution.html) | 守恒 · 白名单 · 交叉一致 |

**💰 资金周报生成** —— 从多份 Excel 数据源自动完成校验 → 多维度指标计算 → 异常检测 → 风险热力图 → 管理建议 → MD/HTML/PDF 三件套 → 沉淀本期汇总供下期环比。面向多子公司、多银行账户、多币种的集团型场景。

**📊 月末财务对账** —— 从 OneDrive 拉取 ERP 数据包，从 Outlook 提取银行回单 PDF，执行三维度自动对账（参考号精确匹配 → 日期+金额模糊匹配 → 摘要相似度匹配），生成仪表盘、结果 Excel 以及报告邮件与通知草稿，由使用者确认后自行分发（本 Skill 不发送任何内容）。

**🧾 报表勾稽自检** —— 在财报报出前执行表内小计、会计恒等式与三表间勾稽等确定性校验，逐项定位差异并输出可回溯证据；敏感报表可走本地模式：原始文件留在本机，由本机脚本完成计算，只把结果摘要回传给云端模型。

### 其他业务域 · 待建

同一套方法论可直接迁移的方向，目前尚未产出技能包：

| 业务域 | 典型场景 | 所需能力 |
|--------|----------|----------|
| 📈 销售与客户经营 | 客户健康度周报、商机跟进提醒、会前背景整合 | CRM 连接器 · 邮件/日历 · 知识图谱 |
| 🔬 研究与竞品追踪 | 竞品动态周报、行业资料归集与引用整理 | Web Search · Browser Automation · 定时任务 |
| 📋 项目管理 | 项目周报、会议纪要与行动项跟踪 | IM/邮件连接器 · Activity feed · Task Management |
| 👥 人力与组织 | 招聘进展汇总、面试反馈归集、入职材料准备 | 邮件/日历 · 文档创建 · 本地文件 |
| ⚖️ 合规与法务 | 合同要点抽取、版本条款比对、检查清单核对 | 本地文件 · PDF 解析 · Code Execution |
| 🛠️ IT 与运维 | 值班日报、告警归集、变更记录整理 | MCP Server · 编码 Agent（ACP）· 定时任务 |

---

## 导入技能

```bash
git clone https://github.com/aws-samples/sample-Amazon-QuickDesktop-Finance-Skills.git
cd sample-Amazon-QuickDesktop-Finance-Skills
```

在 Quick Desktop 中：

1. 打开 **Customize** → **Skills**，选择 **Create** → **From folder**
2. 选择技能文件夹（如 `skills/ap-aging/`），`SKILL.md` 与 `scripts/`、`samples/` 等参考文件一起导入；只有单个 `.md` 或 `.zip` 时用 **From file**
3. 财务技能还要一并导入共享依赖 `skills/financial-local-safety/`
4. 在技能详情面板点 **Try it** 测试，确认无误后 **Publish**

配齐依赖：

- 在 **Customize → Connectors** 连接技能声明的连接器，并确认所需系统工具已启用
- 在 **Customize → Knowledge** 添加技能要读写的本地文件夹

调用方式有三种：对话里点名、Quick 按技能描述自动识别、或在技能详情面板点 **Try it**。

建议先用样例数据手动跑通、核对产出，再挂定时任务。

---

## 写技能的原则

已就绪技能遵循同一取舍：**确定性计算下沉到脚本，判断类工作留给模型**。

- 20 个 Skill 的 Workflow 都把步骤分成两类：指标计算、数据匹配、校验 → 标 `Mode: deterministic`，走 `run_python_on_device`（依赖 `financial-local-safety` 的 Skill）或 `run_python` 执行脚本，结果可复现、可审计；字段映射确认、建议生成、异常升级判断 → 标 `Mode: agentic`，由模型处理（#3 报表勾稽只有确定性步骤）
- #4–#20 在 Workflow 之上写有 **上层判据**（不折旧 · 跨平台）；#3 写成 上层（业务逻辑层·契约与验收判据）/ 下层（平台适配层·确定性脚本）两层
- 关键步骤写明 `Validate`（成功标准）与 `On failure`（降级路径），数据不完整时仍能产出有价值结果
- 20 个场景都带 `scripts/self_check.py` 与 `samples/`（全平版 + 植错版 + 黄金答案），改动脚本后先跑自检；#1、#2 的样例与黄金答案由 `samples/gen_samples.py` 按构造生成，自检退出码 2 表示存在 KNOWN GAP（已知未修复缺陷，见[已知局限与生产化建议](#已知局限与生产化建议)）；#1 讲解页的样例数字可用 `python3 skills/treasury-weekly-report/samples/gen_samples.py --demo` 复现

除 #3 外，各 Skill 的 `SKILL.md` 都有 **Lessons Learned** 章节，记录了实际调试中踩到的坑。复用或改写前建议先读一遍。

---

## 已知局限与生产化建议

以下问题来自对本仓库的安全扫描（AWS Security Agent）和 #1、#2 的自检，目前尚未修复：修复会改变检查结果，或需要按使用方自身流程和部署环境决定。两次扫描（全量 a182d3e5、增量 d1dcef30）中的 HIGH 级问题已经修复或缓解（数值严格解析与 `data_issues`、#4 逐行检查、#6 来源数值无法解析时判不通过、#10 收款方比对、#13 按对账期间取上期、#14 重复银行流水号报出、#2 回单来源核验、各 Skill 声明 `tools:`、#2 去除外发与建定时任务工具并改为生成草稿由人工分发）。全量扫描 0ccfa21f 的 HIGH 级问题也已修复或缓解：各引擎命令行入口只写受控输出目录并拒绝越界路径与符号链接、stdout 只回状态、写出时追加本地哈希链审计日志；#2 未提供正文的回单不作为凭证；#10 应付台账与总账勾稽计入 `all_ok`；#5 关联方范围完备性计入 `all_ok`；#13 往来单位全户出单；#11、#19、#20 重复键或重复余额行记问题，盘点基准日、重大差异金额阈值与合理损耗率改由引擎常量或调用方参数给出；#3 容差设上限并在结果中披露；#17 社保基数上下限与社平工资 60%/300% 核对；#15 红字对应已作废蓝字时剔除。各项实现文件与剩余风险见 [THREAT_MODEL.md](THREAT_MODEL.md)。投入生产前，建议按下列方向改造。

**(a) 输入处理默认放行**

- 白名单对未列出的类别放行：#15 增值税进销项（商品服务类别×税率、不可抵扣用途）、#17 社保公积金（未列险种按申报费率计）、#18 薪酬个税（未列专项附加扣除项目不设上限）。
- 部分检查只看第一行：#9 拆分报销的限额取首行费用类别。
- 检查范围只取单一来源或放过缺失行：#11 供应商清单只来自本方余额表；#19 账面净值不为 0 的已处置资产未出现在盘点表时不报；#18 月份完整性按行数而非月份数统计；#3 不在必需项目清单中的报表项目缺失时仍按 0 计；#2 账龄(天) 为空或为负的行被丢弃。
- 规则文件缺失时跳过检查：#4 缺少 `business_rules.csv` 时跳过业务类型×科目交叉白名单，只在结果中以 `cross_whitelist: false` 标明。
- 6 个场景随附的 `blindspot_patches.py` 辅助库（入口脚本不调用）有缺陷：`cross_whitelist` 对未登记的类型不报，模糊指纹会改写金额，`unit_consistency` 实际不做换算校验。
- 参数未设边界：#12 单户坏账计提比例不经校验即覆盖账龄矩阵；#1 工作簿读取失败只记入 data_issues，后续步骤可能报错中断，或静默产出零条预警；#1 子公司热力图在名称对不上时，把预警规则表中子公司名称的前 4 个字当作正则表达式传给 `str.contains`。
- #3–#20 的 `self_check.py` 在 `expected.json` 为空或缺项时会判为通过。
- 以下情况下 `all_ok` 仍可能为 true，或运行直接报错而不给出结论（增量扫描 d1dcef30、572b9b10 的 MEDIUM/LOW 项）：#9 费用类别未列入 `expense_policy.csv` 时，不做该类别的超标与拆分报销检查（单笔上限留空、为 0 或负数时已登记数据质量问题并逐笔标待核实）；#12 单项计提比例没有取值范围校验；#3 只在输入文件使用标准文件名（如 `balance_sheet.csv`）时才执行必需项目校验；#14 核销引用了不存在的收款单号时，该核销不计入有效核销，但 `all_ok` 不变为 false；#15 `period_info.csv` 的项目名与「上期留抵税额」「本期进项转出」不完全一致时，该值按 0 计且不报；#16 输入文件缺失或改名时按空表处理，不记 `data_issues`；#8 既无入库单也无发票的订单行判为完全匹配；某个输入的全部行都因数值无法解析被剔除时，计算通过率的除法报 `ZeroDivisionError`（如 #10、#19）；日期列不做严格校验，日期格式错误时直接抛错中断。
- 建议：白名单默认拒绝；逐行校验；双向比对检查范围。

**(b) 匹配与预警规则需收紧**

- #14 收款核销：未通过银行锚点或付款方校验的收款仍计入有效核销。
- #2 月末银行对账：匹配率只以银行行数为分母，仅在 ERP 的记录不进入分母，银行侧行数偏少时匹配率会偏高（自检植错版按银行流水计 7/9 = 77.8%；若分母改为去重交易数，即 7 笔匹配 ÷（ERP 9 + 银行 9 − 7）= 11 笔，为 63.6%）；参考号匹配与摘要匹配不校验金额差；SKILL.md 规定的匹配率 <90% / <70% 和单笔差异 >50 万告警未写进代码；解析出的回单不参与对账。
- #1 资金周报：W001 余额低于预警线与连续低余额未实现；规则表与流水的子公司名称不一致时 W003 单笔超限被漏报（自检植错版：单笔 1,800 万超过上限 1,500 万未报警）；W004、W005 的阈值写死在代码中，未从规则表读取；早先文档声明的支付时段热力图（10h×5d）没有实现，已从 SKILL.md 和讲解页移除。`treasury_pipeline.py` 只计算余额趋势、收支结构、计划执行、W003–W005 预警和子公司热力图，理财动态、环比解读和 8 个扩展维度由 Agent 生成。
- 其中 #1 的 W001、W003 漏报，以及 #2 的匹配率分母、告警门槛、回单未参与对账，在 `scripts/self_check.py` 中报告为 KNOWN GAP（退出码 2）。
- 建议：匹配时同时比对金额；把文档中的告警门槛写进代码，并在结果中返回判定。

**(c) Agent 工具权限**：20 个 Skill 和 `financial-local-safety` 都已声明只含工作流所用工具的 `tools:` 列表。宿主是否按该列表强制限制工具，需使用方在自己的 Quick Desktop 环境中确认；#3 报表勾稽未依赖 `financial-local-safety`，计算走云端沙箱 `run_python`；#1 同时持有云端与设备端执行工具（按 `local_safety_mode` 二选一）；#2 云上模式从 OneDrive 取数所用的工具 ID 本仓库没有列出，需按使用方实际连接器补入。#2 读取外部邮件和回单 PDF，因此不持有任何外发或建定时任务的工具，也不持有 `load_skill`（`email_send`、`send_chat_message`、`create_scheduled_agent`、`load_skill` 已移除）：Step 6 只在本地生成报告邮件与通知草稿，由使用者核对后自行分发；每月定时运行由使用者在 Mission Control 自行配置。规则：读取不可信外部内容的 Skill 不得同时持有外发或持久化工具，见 THREAT_MODEL.md T16。

**(d) 审计日志**：各引擎命令行入口（#1 `run_pipeline` 传入 `out_path`、#2 `run_full_pipeline` 及单独调用的 `generate_excel_report()`（后者无输入哈希）、#3 底稿渲染传入 `out_path` 时同样，底稿需另传 `input_paths` 才记录四表哈希）每次写出结果，都在输出目录的 `audit_log.jsonl` 追加一条记录：时间、操作者、引擎文件名及其 SHA-256、非路径参数、输入文件完整 SHA-256、输出文件哈希、结果哈希、判定摘要、上一条记录的 SHA-256（哈希链），不含明细与个人信息；结果中的输入指纹已改为完整 64 位。剩余风险：日志是本机可写文件，哈希链只能发现中间行被改，不能防止整份重写或删除；操作者取自运行环境的用户名，可被伪造；直接调用 `check()` 等函数而不走命令行入口时不写文件也不记日志；#3 底稿中的输入指纹仍由调用方传入，底稿渲染未传 `input_paths` 时审计记录不含输入哈希；结果文件先写出、审计行后追加，若两步之间审计日志被换成符号链接，或追加时出现磁盘写满等 I/O 错误，追加会报错，但结果文件已写出且没有对应审计行。建议把日志同步到只追加的外部存储，并接入使用方的身份系统。

**(e) 敏感输出**：已实现受控输出目录（`--out-dir`，未给出时为当前目录下 `finance_outputs/`，目录 0700），结果文件名只能直接位于该目录内，越界路径、目录外绝对路径和符号链接一律拒绝，默认 `finance_outputs/` 本身是符号链接时同样拒绝，已存在的默认目录收紧为 0700；写任何结果之前先校验同目录 `audit_log.jsonl`（符号链接、非普通文件、多重硬链接时拒绝并退出码 2，不写结果）；结果文件权限 0600；#3–#20 命令行 stdout 只回一行状态（总判定、有记录总数时含条数、问题数与文件路径），#1、#2 只打印进度、笔数、匹配率与文件路径，不再打印金额汇总、个税数字、数据问题原文和含收款方的文件名。剩余风险：结果文件仍为明文，未加密，也没有保留期限；`data_issues` 仍原样写入无法解析的单元格文字；显式 `--out-dir` 由调用方决定，代码只保证文件不逃出该目录；Agent 直接调用函数后自行写文件或打印时不经这些约束。建议按使用方的数据分级加密结果文件并设定保留期限。

**(f) 依赖与完整性**：没有依赖清单和版本锁定，解析外部表格的 pandas、openpyxl 未做漏洞扫描；Skill 脚本载入前没有签名或哈希校验。CDN 上的 ECharts 与 Font Awesome 已加 SRI 哈希，但页面没有设置 Content-Security-Policy；#2 仪表盘模板优先加载的 `/vendor` 本地副本不在仓库中，实际会回退到 CDN。建议锁定依赖并定期扫描，前端资源改为本地托管或保留 SRI，并加 CSP。

**(g) 不可信输入**：#2 只接受 `trusted_senders` 清单内发件人的回单，并核对 PDF 正文中的交易日期和金额；未提供正文的回单不作为对账凭证，列入 `receipt_check.未提供正文`；发件人地址本身可被伪造，脚本不校验 DKIM/SPF 或银行电子签章；邮件、PDF 和表格里的文字会进入模型上下文，可能携带提示注入或伪造凭证。建议改为银行直连或网银导出对账单并核验回单签章，并把数据与指令分开处理。

---

## 目录结构

```
.
├── index.html                              # Playbook 主页 · 定位/能力/场景/落地/企业接入/资源
├── overview-guided.html                    # 扩展 · 自讲解版（10 段语音讲解）
├── playbook.html                           # 技术参考手册 · 系统工具明细 / 权限模型 / 界面
├── finance-scenarios.html                  # 财务场景库 · 20 个场景总览
├── LICENSE                                 # MIT-0
├── CODE_OF_CONDUCT.md
├── CONTRIBUTING.md
├── THIRD-PARTY-NOTICES.md                  # 第三方组件与许可证（ECharts、Font Awesome、Python 库）
├── THREAT_MODEL.md                         # 威胁模型（STRIDE）：信任边界、威胁、缓解措施与使用方责任
└── skills/
    ├── README.md
    ├── ap-aging/
    ├── ar-aging-baddebt/
    ├── ar-settlement-matching/
    ├── consolidation-elimination-check/
    ├── customer-statement/
    ├── expense-allocation-check/
    ├── finance-monthly-reconciliation/
    │   ├── SKILL.md
    │   ├── solution.html
    │   ├── README.md
    │   ├── scripts/
    │   │   ├── reconciliation_engine.py
    │   │   ├── blindspot_patches.py
    │   │   ├── self_check.py
    │   │   └── dashboard_template.html     # ECharts 5（Apache-2.0）
    │   └── samples/                        # gen_samples.py 生成的构造样例 + expected.json
    ├── financial-local-safety/             # 共享依赖 Skill（除 #3 外 19 个场景的 depends-on 引用），不是独立场景
    │   └── SKILL.md
    ├── fixed-asset-verification/
    ├── gl-voucher-compliance/
    ├── intercompany-reconciliation/
    ├── inventory-count-variance/
    ├── invoice-verification-dedup/
    ├── payroll-iit-check/
    ├── report-tieout-check/
    ├── social-insurance-base-check/
    ├── supplier-reconciliation/
    ├── three-way-match/
    ├── treasury-weekly-report/
    │   ├── SKILL.md
    │   ├── solution.html
    │   ├── scripts/treasury_pipeline.py · self_check.py
    │   └── samples/                        # gen_samples.py 生成的构造样例 + expected.json + demo_summary.json
    ├── vat-filing-consistency/
    └── vat-input-output-matching/
                                            # 其余 18 个：SKILL.md · solution.html · scripts/（入口脚本 + self_check.py，部分另有 blindspot_*.py / render_worksheet.py 辅助模块）· samples/
```

---

## 企业接入 · Quick SSO

企业版用户用 Quick Web 端已在使用的登录方式登录桌面端：默认不需要在 IdP 注册 OIDC 应用、不需要 Client ID 或 extension access，管理员一次性添加桌面连接器即可。登录时浏览器需已有 Quick Web 会话；一次登录最长 30 天。

**官方配置文档：** [Amazon Quick 桌面端企业部署指引](https://docs.aws.amazon.com/quick/latest/userguide/desktop-enterprise-setup.html)

开通四步：

1. 确认前提：Enterprise 版、home Region 支持 agentic 功能（见[官方区域页](https://docs.aws.amazon.com/quick/latest/userguide/regions.html)）、管理员权限、桌面端 1.1.4053 及以上
2. （可选）分批放开：添加连接器会立即对全员开放；要分批，先在 custom permissions 中限制 Quick desktop 能力
3. 添加桌面连接器：控制台头像 → **Manage account** → **Inbound connectors** → **Quick Desktop** → **Add desktop connector**，选 **Use Quick's existing configured sign-in method**
4. 连接器卡片显示 Quick Desktop / Enabled 后，再向用户分发客户端

默认路径沿用 Quick 已配置的登录方式，IAM Identity Center、IAM Federation、Quick 原生用户均可。需要在 IdP 中单独管理桌面登录时，可改用**专用身份提供商配置**（advanced，OIDC + PKCE）：官方为 Microsoft Entra ID、Google Workspace、Okta、Ping Identity 提供了分步文档，其他兼容 OIDC 的 IdP 未提供官方分步文档；配置方法见上面的官方企业部署指引。已用 desktop extension 配置的组织，原配置继续可用。

强制走企业 SSO —— 用 MDM 下发 `DisableSocialLogin`：

```
# macOS 偏好域
com.aws.QuickWork.mac → DisableSocialLogin = true

# Windows 注册表
HKLM\SOFTWARE\Policies\Amazon\Quick
  DisableSocialLogin (REG_DWORD) = 1
```

> IdP 中的邮箱地址必须与 Amazon Quick 里该用户的邮箱**精确匹配**，否则映射失败。应用会自动沿用操作系统代理配置，无需单独设置。

---

## 相关资源

- [Amazon Quick on desktop 用户指南](https://docs.aws.amazon.com/quick/latest/userguide/amazon-quick-desktop.html)
- [Skills](https://docs.aws.amazon.com/quick/latest/userguide/skills-desktop.html)
- [Mission Control（定时任务 routine）](https://docs.aws.amazon.com/quick/latest/userguide/mission-control-desktop.html)
- [企业部署指引](https://docs.aws.amazon.com/quick/latest/userguide/desktop-enterprise-setup.html)

---

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.

第三方依赖：页面与脚本用到的第三方组件及其许可证见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

---

## 说明

- 内容基于 [AWS 官方文档](https://docs.aws.amazon.com/quick/latest/userguide/amazon-quick-desktop.html) 整理
- **所有示例数据、企业名称、金额均为虚构**，不含任何真实业务数据
- 技能包中的工具名与连接器名以 Quick Desktop 实际版本为准，如有差异请在导入后于技能详情页调整
