---
name: financial-local-safety
display_name: "🔒 本地安全执行契约"
icon: "🔒"
description: "财务/敏感数据处理的本地安全执行契约。任何处理银行流水、应收台账、资金明细、客户信息等敏感财务数据的 skill，执行前必须先加载本 skill 并遵守其铁律：数据只在本地读取、计算只在设备端进行、结果只写本地、stdout 只回无害确认。触发词：本地安全、安全契约、数据不上云、就地处理、financial local safety。"
trigger: "本地安全 安全契约 数据不上云 就地处理 financial local safety"
patterns:
  - {pattern: "本地安全", confidence: 0.9}
  - {pattern: "不上云", confidence: 0.85}
  - {pattern: "就地处理", confidence: 0.85}
version: "1.0.0"
inputs: []
tools: [folder_list, list_outposts, run_python_on_device, run_python_on_device_with_write]
id: aea7b880b5f24a608434fc4f4af1e43c
---

# 本地安全执行契约 (Financial Local Safety Contract)

## Overview

本 skill 是财务/敏感数据处理的**安全宪法**。它本身不处理数据，而是为所有财务类
skill（对账、资金周报、月末结账等）提供一套**统一的、强制的本地安全约束**。

核心目标：让「读取本地文件 → 运行本地代码 → 输出结果到本地」三步全程锁定在用户
本机（outpost）执行，**原始数据和敏感明细都不离开用户电脑**，云端 Agent 只看到
「跑完了、多少条」这类无害元信息。

任何财务 skill 应在 frontmatter 加 `depends-on: [financial-local-safety]`，
并在执行前第一步加载本 skill、遵守下述铁律。

## 关键前提：Agent 大脑在云端

必须诚实认识一个边界：**Agent 的推理/调度大脑运行在云端**，它靠设备端代码的
`stdout` 回传来决定下一步。因此：

- 原始文件、逐行明细：可以做到**完全不上云**（留在本机）。
- 设备端代码 `print` 的内容：**会回传云端**（上限约 60KB）。
- 所以本契约的灵魂是**铁律 3**——控制 stdout 只回无害确认，敏感明细一律写本地文件。

## 四条铁律（强制，不可绕过）

### 铁律 1：数据只在本地读取
- 只从用户授权的本地文件夹读取源文件（Desktop / Downloads / 企业内网映射盘）。
- **禁止**要求用户通过聊天框上传源文件——上传即意味着文件已进入云端会话工作区。
- 若源文件不在授权文件夹，先引导用户放入，**不得**改走云端沙箱。

### 铁律 2：计算只在设备端进行
- 一切含真实数据的处理必须用 `run_python_on_device` / `run_python_on_device_with_write`（在 outpost 本机执行）。
- **禁止**使用云端沙箱 `run_python` 处理任何含真实数据的步骤。
- 代码内路径一律用本机原生路径（如 `/Users/<user>/...`；Windows 用 `C:\Users\...`）。

### 铁律 3：结果只写本地，stdout 只回无害确认
- 所有敏感明细（逐笔金额、异常清单、对手方名称、账户号、客户信息、PII）必须由
  本机代码**直接写入本地文件**（xlsx / html / pdf / json），落到指定本地输出目录。
- 回传云端的 `print` / `stdout` **只允许无害元信息**：
  - ✅ 允许：完成状态、处理条数、匹配/异常笔数、校验 PASS/FAIL、输出文件路径。
  - ❌ 禁止：逐行明细、具体金额、账户号、对手方名称、客户名称、任何 PII。
- 违反示例：`print(未匹配流水明细DataFrame)`
- 正确示例：`print("对账完成：匹配48912笔，异常1013笔 → ~/输出/对账报告.xlsx")`
- 本仓库各引擎的命令行入口已在代码中执行受控输出目录与 stdout 只回状态（见各 Skill 的 `scripts/output_guard.py`）；其他代码路径（如 Agent 直接调用函数后自行写文件或打印）仍需 Agent 遵守本铁律。

### 铁律 4：设备离线则中止，不降级
- 若 outpost 设备离线，明确告知用户「设备离线，无法安全处理」。
- **禁止**为完成任务而自动改用云端路径。

## Workflow

### Step 1: 前置校验（执行任何财务处理前必做）
- **Mode**: `deterministic`
- **Tool**: `folder_list`, `list_outposts`
- **Input**: 目标源文件路径、outpost 设备
- **Output**: 校验通过 / 阻断原因
- **Validate**:
  1. `folder_list` 确认目标文件位于授权本地文件夹内（非云端会话工作区 `backend/.../workspace/`）
  2. `list_outposts` 确认设备在线
  3. 二者任一不满足 → 停止并提示用户，不得继续
- **On failure**: 输出具体阻断原因（文件不在授权夹 / 设备离线），终止
- **权限**：依赖本契约的 skill 须在自己的 `tools:` 清单中声明 `folder_list`、`list_outposts`、`run_python_on_device`（需要写文件时再加 `run_python_on_device_with_write`），只列工作流实际调用的工具；未声明 `tools:` 的 skill 没有工具白名单约束（宿主的系统工具默认全部启用、Always Allow，见根目录 README）

### Step 2: 绑定设备端执行
- **Mode**: `deterministic`
- **Tool**: `run_python_on_device` / `run_python_on_device_with_write`
- **Input**: 本机原生路径的源文件
- **Output**: 计算结果写入本地文件；stdout 仅回无害确认
- **Validate**: 确认所用工具是 on-device 版本；确认 print 内容不含敏感明细
- **On failure**: 若误用云端 `run_python` 处理真实数据，立即中止并纠正

### Step 3: 输出与回执
- **Mode**: `agentic`
- **Input**: 本机生成的报告文件路径
- **Output**: 向用户回执「输出文件位置 + 无害统计摘要」
- **Validate**: 敏感明细均在本地文件内，对话中不出现逐行敏感数据
- **On failure**: 若发现敏感数据已进入对话，提示用户并说明

## Output

本 skill 不产生独立数据输出。它作为安全约束被财务类 skill 引用（`depends-on`），
确保这些 skill 的执行全程满足本地安全铁律。

## Lessons Learned

### Do
- 财务 skill 第一步就加载本契约，把安全前置校验做在数据读取之前。
- 明细写本地文件，stdout 只回「多少条、写到哪」。
- 数据分级：拿不准就默认按敏感处理、走本地路径。

### Don't
- 不要用聊天框附件上传敏感财务源文件。
- 不要用云端沙箱 `run_python` 跑真实数据。
- 不要在设备离线时为了完成任务偷偷降级到云端。
- 不要把逐行明细/金额/PII 打印到 stdout。

### Common Failures
- 误把附件上传当「本地文件」→ 实际已上云；用 `folder_list` 核实路径前缀。
- 设备离线导致 on-device 执行失败 → 明确报「设备离线」，不改走云端。
- stdout 超 60KB 被截断 → 本就不该 print 明细，改为写本地文件。

### When to Ask the User
- 数据敏感级别不明确时，确认是否按敏感处理。
- 源文件不在授权文件夹时，请用户放入或授权文件夹。
- 用户明确要求「快速出图表」而数据敏感时，说明「本地算聚合→脱敏结果再可视化」的两步法。
