# Skills

Amazon Quick on desktop 技能包。每个技能是一个文件夹，包含 `SKILL.md`（指令集）与脚本等参考文件。

财务域 20 个技能，按五个流程域分组（总览见 [财务场景库](../finance-scenarios.html)）。

> 定位：这些技能是财务核对场景的参考实现，可按自身流程修改、扩展和优化；样例数据均为构造。投入生产前，需按自身会计政策和数据校验规则、阈值与容差，并自行完成安全评审（详见根目录 README 的「定位：参考实现」与「已知局限与生产化建议」）。

**R2R 记录到报告**

| 技能 | 触发词 | 连接器依赖 | 产出 |
|------|--------|-----------|------|
| [`treasury-weekly-report`](treasury-weekly-report/solution.html) #1 资金周报生成器 | 资金周报 / treasury report / 资金分析 | 无（可纯本地） | MD 周报 · HTML 仪表盘 · PDF · 汇总 Excel |
| [`finance-monthly-reconciliation`](finance-monthly-reconciliation/solution.html) #2 月末银行对账 | 月末对账 / 银企对账 / reconciliation | OneDrive · Outlook（只读） | HTML 仪表盘 · 对账 Excel · 报告邮件与通知草稿（不发送，由使用者自行分发） |
| [`report-tieout-check`](report-tieout-check/solution.html) #3 报表勾稽自检 | 报表勾稽 / 三表勾稽 / report tieout | 无（本地 CSV / 系统导出） | 逐项勾稽清单 · 通过率与不平明细 · 全平/不平状态 |
| [`gl-voucher-compliance`](gl-voucher-compliance/solution.html) #4 总账凭证合规 | 凭证合规 / 凭证审核 / 借贷平衡检查 | 无（本地 CSV / 系统导出） | 逐凭证结果 · 验收报告（合规率、问题分布、制单人风险排名） |
| [`intercompany-reconciliation`](intercompany-reconciliation/solution.html) #5 关联方往来对账 | 关联方往来 / 内部往来对账 / intercompany | 无（本地 CSV / 系统导出；可选 MCP 对接合并报表系统） | 逐交易对账结果 · 抵消差额 · 待调整清单 |
| [`consolidation-elimination-check`](consolidation-elimination-check/solution.html) #6 合并抵消检查 | 合并抵消 / 抵消分录校验 / consolidation elimination | 无（本地 CSV / 系统导出） | 合并工作底稿 · 抵消检查报告 · 四类抵消金额分布 |
| [`expense-allocation-check`](expense-allocation-check/solution.html) #7 期间费用分摊复核 | 费用分摊 / 待摊费用 / expense allocation | 无（本地 CSV / 系统导出） | 逐笔分摊校验结果 · 应摊/累计/账面合计 · 部门分摊分布 |

**P2P 采购到付款**

| 技能 | 触发词 | 连接器依赖 | 产出 |
|------|--------|-----------|------|
| [`three-way-match`](three-way-match/solution.html) #8 三单匹配核对 | 三单匹配 / PO GRN 发票 / three-way match | 无（本地 CSV / 系统导出；可选 MCP 对接 ERP 采购模块） | 逐 PO 差异清单 · 验收报告 · 三档状态 |
| [`invoice-verification-dedup`](invoice-verification-dedup/solution.html) #9 发票查验与重复报销 | 发票查验 / 重复报销 / invoice dedup | 无（本地 CSV / 系统导出；可选 MCP 对接 OA 报销系统） | 逐单查验结果 · 验收报告（风险分布、风险金额） |
| [`ap-aging`](ap-aging/solution.html) #10 应付账龄分析 | 应付账龄 / 逾期应付 / AP aging | 无（本地 CSV / 系统导出；可选 MCP 对接应付模块 / ERP） | 账龄分桶表 · 逾期供应商排名 · 逾期率 |
| [`supplier-reconciliation`](supplier-reconciliation/solution.html) #11 供应商对账 | 供应商对账 / 应付对账 / supplier reconciliation | 无（本地 CSV；供应商对账单需先解析为结构化表） | 逐单据对账结果 · 余额差额归因报告 |

**O2C 订单到收款**

| 技能 | 触发词 | 连接器依赖 | 产出 |
|------|--------|-----------|------|
| [`ar-aging-baddebt`](ar-aging-baddebt/solution.html) #12 应收账龄与坏账 | 应收账龄 / 坏账计提 / 预期信用损失 | 无（本地 CSV / 系统导出） | 账龄分桶与计提表 · 坏账准备合计 · 高风险应收明细 |
| [`customer-statement`](customer-statement/solution.html) #13 客户对账单生成 | 客户对账单 / 往来对账单 / customer statement | 无（本地 CSV / 系统导出） | 逐户对账单 · 汇总表 · 客户余额排名 |
| [`ar-settlement-matching`](ar-settlement-matching/solution.html) #14 收款核销匹配 | 收款核销 / 核销匹配 / AR settlement | 无（本地 CSV / 系统导出；可选 MCP 对接应收 / 收款模块） | 收款单与发票核销状态 · 核销守恒与应收闭环报告 |

**TAX 税务与薪酬**

| 技能 | 触发词 | 连接器依赖 | 产出 |
|------|--------|-----------|------|
| [`vat-input-output-matching`](vat-input-output-matching/solution.html) #15 增值税进销项匹配 | 增值税进销项 / 进销项匹配 / VAT matching | 无（本地 CSV / 系统导出） | 逐张校验结果 · 进销项勾稽与应纳税额 · 异常清单 |
| [`vat-filing-consistency`](vat-filing-consistency/solution.html) #16 增值税申报一致性 | 增值税申报 / 申报一致性 / VAT filing | 无（本地 CSV / 系统导出） | 逐栏核对 · 恒等式校验与正确应纳税额 · 不符栏次清单 |
| [`social-insurance-base-check`](social-insurance-base-check/solution.html) #17 社保公积金基数核定 | 社保基数 / 公积金基数 / 五险一金 | 无（本地 CSV / 系统导出） | 逐人核定结果 · 缴费汇总与实缴/应缴差异 · 异常清单 |
| [`payroll-iit-check`](payroll-iit-check/solution.html) #18 薪酬个税复核 | 个税复核 / 累计预扣 / payroll IIT | 无（本地 CSV / 系统导出） | 逐人复核结果 · 个税汇总（少缴/多缴）· 异常清单 |

**ASSET 资产管理**

| 技能 | 触发词 | 连接器依赖 | 产出 |
|------|--------|-----------|------|
| [`fixed-asset-verification`](fixed-asset-verification/solution.html) #19 固定资产卡片与实物核对 | 固定资产盘点 / 账实核对 / 资产盘盈盘亏 | 无（本地 CSV / 系统导出） | 逐资产核对结果 · 盘盈盘亏汇总 · 折旧异常 |
| [`inventory-count-variance`](inventory-count-variance/solution.html) #20 存货盘点差异处理 | 存货盘点 / 盘点差异 / inventory count | 无（本地 CSV / 系统导出） | 逐 SKU 核对结果 · 盘盈盘亏汇总与净差异 |

## 导入方式

Quick Desktop 中打开 **Customize** → **Skills** → **Create** → **From folder**，选择技能文件夹，`SKILL.md` 与 `scripts/`、`samples/` 一起导入；只有单个 `.md` 或 `.zip` 时用 **From file**。导入后在技能详情面板点 **Try it** 测试，再 **Publish**。财务技能还要一并导入共享依赖 [`financial-local-safety`](financial-local-safety/)。

导入前请确认：

- 技能声明的连接器已在 **Customize → Connectors** 中连接
- 技能要读写的本地文件夹已在 **Customize → Knowledge** 中添加
- 技能声明的系统工具（如 Code Execution）已在 **Customize → Connectors** 中启用

## 技能结构约定

```
<skill-name>/
├── SKILL.md          # 必需 —— frontmatter 元数据 + Overview + Workflow + Output + Lessons Learned
├── solution.html     # 必需 —— 与 Skill 配套的场景讲解网页（每个场景一个）
├── skill.yaml        # 可选 —— 结构化配置（连接器、默认参数、工作流步骤）
├── README.md         # 可选 —— 面向人的说明文档
├── scripts/          # SKILL.md frontmatter 中 scripts 字段引用的脚本（入口脚本、辅助模块、self_check.py）
└── samples/          # 可选 —— 自检夹具：clean/ · error/ · expected.json
```

`SKILL.md` frontmatter 关键字段：

| 字段 | 说明 |
|------|------|
| `name` | 技能标识符，与文件夹名一致 |
| `display_name` | 界面显示名 |
| `description` | 描述 + 激活条件，Quick 靠它做自动选择 |
| `trigger` / `patterns` | 触发词与正则匹配置信度 |
| `inputs` | 输入参数（名称、类型、是否必需、默认值） |
| `tools` | 技能加载时一并载入会话的工具列表 |
| `scripts` | 引用的脚本文件 |

## 编写要点

这些技能都遵循同一个设计原则：**确定性计算下沉到 Python，判断类工作留给模型**。

- 20 个技能的 Workflow 都把步骤分成两类：指标计算、数据匹配、校验 → 标记 `Mode: deterministic`，走 `run_python_on_device`（依赖 `financial-local-safety` 的技能）或 `run_python` 执行脚本，结果可复现、可审计；数据映射确认、管理建议、异常升级判断 → 标记 `Mode: agentic`，由模型处理（#3 报表勾稽只有确定性步骤）
- #4–#20 在 Workflow 之上写有 **上层判据**（不折旧 · 跨平台）；#3 写成 上层（业务逻辑层·契约与验收判据）/ 下层（平台适配层·确定性脚本）两层
- 关键步骤写明 `Validate`（成功标准）与 `On failure`（降级路径），让技能在数据不完整时仍能产出有价值的结果
- 20 个技能都带 `scripts/self_check.py` 与 `samples/`（全平版 + 植错版 + 黄金答案 `expected.json`）；改动脚本后运行 `python3 scripts/self_check.py`，退出码 0 即与黄金答案一致。#1、#2 的样例由 `samples/gen_samples.py` 按构造生成，退出码 2 表示存在 KNOWN GAP（已知未修复缺陷，见根目录 README 的「已知局限与生产化建议」）

除 #3 外，各技能的 `SKILL.md` 都有 **Lessons Learned** 章节，记录了实际调试中踩到的坑，复用或改写技能前建议先读一遍。

## 共享依赖：`financial-local-safety`

[`financial-local-safety`](financial-local-safety/) 是财务类技能共用的本地安全执行契约，不是独立场景，也不计入 20 个技能。除 #3 报表勾稽外，19 个技能的 frontmatter 都声明 `depends-on: [financial-local-safety]`，执行前先加载它。导入财务技能时请一并导入这个文件夹。
