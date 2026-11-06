# -*- coding: utf-8 -*-
"""资金周报 · 自检样例生成器（构造数据 + 按构造推导黄金答案）。

用法：python gen_samples.py        # 在 samples/ 下生成 clean/、error/ 与 expected.json

- 全部名称为虚构（示例集团、示例子公司A……），账户号为「示例账户-A1」这类占位，金额单位为万元。
- expected.json 由本脚本按 SKILL.md 描述的口径直接推导，不调用 treasury_pipeline.py，避免循环验证：
  每日余额 = 上周末余额 + 累计净流量；当日收入/支出 = 计划表【合计】实际数 × 当日流水占比。
- 外汇定价（IRP 远期、Garman-Kohlhagen 期权）用本文件里独立的实现（远期价格 + Black-76 形式，
  N(x) 直接用 math.erf）计算期望值，并校验看涨-看跌平价。参数为测试用构造值，不是市场报价。
- 植错版（error）在全平版基础上植入 W001/W003/W004/W005 预警；引擎尚未实现或会漏报的，写入 known_gaps，
  self_check 报告为 KNOWN GAP，不算通过。
"""
import json
import math
import os
from datetime import date

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DAYS = [date(2026, 6, d) for d in (8, 9, 10, 11, 12)]          # W24 周一至周五
WEEKDAY = ["周一", "周二", "周三", "周四", "周五"]
SUBS = ["示例子公司A", "示例子公司B", "示例子公司C", "示例子公司D"]
LIMIT = {"示例子公司A": 3000, "示例子公司B": 2000, "示例子公司C": 2000, "示例子公司D": 1500}   # 单笔支付上限(万)
ALERT_LINE = {"示例子公司A": 800, "示例子公司B": 600, "示例子公司C": 600, "示例子公司D": 500}  # 余额预警线(万)
LAST_WEEK_BALANCE_YI = 3.2                                     # 上周五总资金余额(亿)
PLAN = [  # 类别, 计划收入, 实际收入, 计划支出, 实际支出, 备注（单位：万）
    ("销售回款", 4000, 4000, None, None, "按期回款"),
    ("其他收入", 800, 1000, None, None, "利息与补贴"),
    ("采购付款", None, None, 2500, 2000, "部分采购延后"),
    ("工资税费", None, None, 1500, 1600, "含季度奖金"),
    ("理财申购", None, None, 500, 600, "闲置资金申购"),
]

# 流水：(日序, 时间, 子公司, 收付, 金额(万), 摘要, 对手方)
CLEAN_FLOW = [
    (0, "09:30:00", "示例子公司A", "收", 600, "销售回款", "示例客户01"),
    (0, "10:00:00", "示例子公司B", "付", 400, "采购付款", "示例供应商01"),
    (1, "09:45:00", "示例子公司A", "收", 1200, "销售回款", "示例客户02"),
    (1, "14:00:00", "示例子公司C", "付", 500, "采购付款", "示例供应商02"),
    (2, "10:30:00", "示例子公司B", "收", 1800, "销售回款", "示例客户03"),
    (2, "15:00:00", "示例子公司A", "付", 900, "工资发放", "员工001"),
    (3, "11:00:00", "示例子公司C", "收", 1200, "销售回款", "示例客户04"),
    (3, "16:00:00", "示例子公司D", "付", 600, "税费缴纳", "示例税务账户"),
    (4, "09:15:00", "示例子公司D", "收", 1200, "理财赎回", "示例银行理财"),
    (4, "13:30:00", "示例子公司B", "付", 600, "理财申购", "示例银行理财"),
]
ERROR_EXTRA_FLOW = [
    (1, "11:00:00", "示例子公司A", "付", 3500, "设备采购", "示例供应商03"),   # W003：超 A 单笔上限 3000
    (2, "10:00:00", "示例子公司D", "付", 1800, "工程款", "示例供应商04"),     # W003：超 D 单笔上限 1500（规则表名称多一个空格）
    (3, "10:20:00", "示例子公司B", "收", 1500, "", "示例客户05"),             # W004：>1000 万且摘要为空
    (4, "23:15:00", "示例子公司C", "付", 200, "内部转账", "示例子公司A"),     # W005：22:00 后交易
]
# 账户余额（万）：error 中 D 的账户余额 300 低于预警线 500 → W001
BALANCE = {"clean": {"示例子公司A": 9000, "示例子公司B": 7000, "示例子公司C": 8000, "示例子公司D": 8000},
           "error": {"示例子公司A": 9000, "示例子公司B": 7000, "示例子公司C": 8000, "示例子公司D": 300}}
LAST_KPI = [("总资金余额(亿)", 3.2), ("本周收入(万)", 4800), ("本周支出(万)", 4000), ("理财余额(万)", 6000),
            ("授信可用(万)", 20000), ("预警条数", 0), ("计划执行偏差项", 1)]

# 外汇定价测试参数（构造值）：USD/CNY 即期 7.10，行权 7.20，半年期，人民币利率 2%，美元利率 4.5%，波动率 5%
FX = {"S": 7.10, "K": 7.20, "T": 0.5, "rd": 0.02, "rf": 0.045, "sigma": 0.05}


def fx_expected(S, K, T, rd, rf, sigma):
    """独立实现：Black-76 形式 C = e^{-rd·T}[F·N(d1) − K·N(d2)]，F = S·e^{(rd−rf)T}；N 直接用 math.erf。"""
    N = lambda x: 0.5 * math.erfc(-x / math.sqrt(2.0))
    F = S * math.exp((rd - rf) * T)
    v = sigma * math.sqrt(T)
    d1 = math.log(F / K) / v + v / 2
    d2 = d1 - v
    df = math.exp(-rd * T)
    call = df * (F * N(d1) - K * N(d2))
    put = df * (K * N(-d2) - F * N(-d1))
    parity_gap = (call - put) - (S * math.exp(-rf * T) - K * math.exp(-rd * T))
    return {"call": round(call, 10), "put": round(put, 10), "parity_gap_abs": abs(parity_gap),
            "irp_forward_simple": round(S * (1 + rd * T) / (1 + rf * T), 10)}


def build(case):
    flow_t = CLEAN_FLOW + (ERROR_EXTRA_FLOW if case == "error" else [])
    flow = []
    for i, (di, tm, sub, dirn, amt, memo, cp) in enumerate(flow_t, 1):
        flow.append({"流水号": f"LS-{i:03d}", "日期": DAYS[di].isoformat(), "时间": tm, "子公司名称": sub,
                     "账户号": f"示例账户-{sub[-1]}1", "收付方向": dirn, "金额": amt, "摘要/用途": memo or None,
                     "对手方名称": cp})
    accounts = [{"账户号": f"示例账户-{s[-1]}1", "子公司": s, "开户行": "示例银行", "余额(万)": BALANCE[case][s]} for s in SUBS]
    rule_name = {s: s for s in SUBS}
    if case == "error":
        rule_name["示例子公司D"] = "示例子公司D "      # 规则表名称带尾随空格（常见录入差异）
    alerts = [{"子公司": rule_name[s], "单笔支付上限(万)": LIMIT[s], "余额预警线(万)": ALERT_LINE[s]} for s in SUBS]
    plan = [{"类别": c, "计划收入(万)": pi, "实际收入(万)": ai, "计划支出(万)": pe, "实际支出(万)": ae,
             "执行率": f"{round((ai if ai is not None else ae) / (pi if pi is not None else pe) * 100)}%", "备注": n}
            for c, pi, ai, pe, ae, n in PLAN]
    tot_in = sum(r[2] for r in PLAN if r[2] is not None)
    tot_out = sum(r[4] for r in PLAN if r[4] is not None)
    plan.append({"类别": "【合计】", "计划收入(万)": sum(r[1] for r in PLAN if r[1]), "实际收入(万)": tot_in,
                 "计划支出(万)": sum(r[3] for r in PLAN if r[3]), "实际支出(万)": tot_out, "执行率": "", "备注": ""})
    last_daily = [{"日期": f"2026-06-0{d}", "总资金余额(亿)": b} for d, b in ((1, 3.0), (2, 3.05), (3, 3.1), (4, 3.15), (5, LAST_WEEK_BALANCE_YI))]
    invest = [{"产品": "示例理财01", "金额(万)": 3000, "年化收益率": "2.1%"}, {"产品": "示例理财02", "金额(万)": 3000, "年化收益率": "2.4%"}]
    maturity = [{"产品": "示例理财03", "到期日": "2026-06-12", "金额(万)": 1200}]

    d = os.path.join(HERE, case)
    os.makedirs(d, exist_ok=True)
    files = {"flow": "银行流水明细.xlsx", "invest": "理财台账.xlsx", "plan": "资金计划表.xlsx",
             "alert": "预警规则表.xlsx", "last_week": "上周资金汇总.xlsx"}
    sheets = {"flow": {"流水明细": flow, "账户信息": accounts}, "invest": {"在投理财": invest, "本周到期": maturity},
              "plan": {"本周资金计划": plan}, "alert": {"账户预警规则": alerts},
              "last_week": {"上周每日汇总": last_daily, "上周关键指标": [{"指标": k, "上周(W23)值": v} for k, v in LAST_KPI]}}
    for key, fn in files.items():
        with pd.ExcelWriter(os.path.join(d, fn), engine="openpyxl") as w:
            for sh, rows in sheets[key].items():
                pd.DataFrame(rows).to_excel(w, sheet_name=sh, index=False)

    # ---------------- 黄金答案（按 SKILL.md 口径推导）
    inc_day = [sum(f[4] for f in flow_t if f[0] == i and f[3] == "收") for i in range(5)]
    exp_day = [sum(f[4] for f in flow_t if f[0] == i and f[3] == "付") for i in range(5)]
    bal = LAST_WEEK_BALANCE_YI * 10000
    trend = []
    for i in range(5):
        inc = tot_in * inc_day[i] / sum(inc_day)
        out = tot_out * exp_day[i] / sum(exp_day)
        bal += inc - out
        trend.append({"日期": DAYS[i].isoformat(), "星期": WEEKDAY[i], "收入_万": round(inc, 2), "支出_万": round(out, 2),
                      "净流量_万": round(inc - out, 2), "余额_万": round(bal, 2)})
    deviations = sorted(c for c, pi, ai, pe, ae, _ in PLAN
                        if abs((ai if ai is not None else ae) / (pi if pi is not None else pe) - 1) > 0.15)
    w003 = sorted(f"{DAYS[f[0]].isoformat()} {f[2]} {f[4]}" for f in flow_t if f[3] == "付" and f[4] > LIMIT[f[2]])
    w004 = sorted(f"{DAYS[f[0]].isoformat()} {f[2]} {f[4]}" for f in flow_t if f[4] > 1000 and not f[5])
    w005 = sorted(f"{DAYS[f[0]].isoformat()} {f[2]} {f[4]}" for f in flow_t if not (6 <= int(f[1][:2]) < 22))
    w001 = sorted(s for s in SUBS if BALANCE[case][s] < ALERT_LINE[s])
    n_heat = min(15, len(SUBS))
    exp = {
        "transaction_count": len(flow_t), "subsidiary_count": len(SUBS), "account_count": len(SUBS),
        "actual_income": tot_in, "actual_expense": tot_out, "last_balance_wan": LAST_WEEK_BALANCE_YI * 10000,
        "daily_trend": trend,
        "income_categories": sorted(r[0] for r in PLAN if r[2] is not None),
        "expense_categories": sorted(r[0] for r in PLAN if r[4] is not None),
        "plan_deviations": deviations,
        "W003": w003, "W004": w004, "W005": w005,
        "heatmap_shape": [n_heat, len(DAYS)],
        "fx": dict(FX, **fx_expected(**FX)),
        "known_gaps": [],
    }
    if case == "error":
        # 按 SKILL.md 应检出、但引擎漏报的预警
        # 规则表名称与流水一致的那部分 W003 必须检出；名称不一致的那条按 SKILL.md 也应检出，列为 KNOWN GAP
        exp["W003_must_include"] = sorted(x for x in w003 if rule_name[x.split()[1]] == x.split()[1])
        exp["W001"] = w001
        exp["known_gaps"] += [
            {"id": "W001_not_implemented", "key": "W001", "finding": "f-80c6f979（Treasury alert coverage gap）",
             "说明": "SKILL.md Step 3 声明 W001 余额低于预警线；detect_anomalies 未实现", "expected": w001},
            {"id": "W003_name_mismatch", "key": "W003", "finding": "f-80c6f979（Treasury alert coverage gap）",
             "说明": "规则表子公司名称与流水不完全一致（尾随空格）时，单笔上限为空，W003 被静默漏报",
             "expected": w003},
        ]
    return exp


# ---------------------------------------------------------------- 演示规模数据（--demo）
# 讲解页 solution.html 上的样例数字全部来自这里：固定随机种子生成约 5 万笔流水的 5 份工作簿，
# 再用 scripts/treasury_pipeline.py 实跑，结果摘要写入 samples/demo_summary.json。工作簿较大，不入库。
DEMO_SEED = 20260612
DEMO_N = 50000
DEMO_SUBS = [f"示例子公司{i:02d}" for i in range(1, 33)]
DEMO_INCOME = [  # 类别, 计划收入(万), 实际收入(万)
    ("销售回款", 30000, 31200), ("预收货款", 6000, 5400), ("理财赎回", 12000, 12000), ("理财收益", 300, 310),
    ("利息收入", 200, 190), ("融资流入", 8000, 10000), ("政府补助", 500, 300), ("投资分红", 1000, 1000),
    ("保证金退回", 800, 650), ("租金收入", 400, 400), ("其他收入", 600, 750),
]
DEMO_EXPENSE = [  # 类别, 计划支出(万), 实际支出(万)
    ("采购付款", 25000, 23800), ("工资薪金", 6000, 6050), ("社保公积金", 1800, 1800), ("税费缴纳", 3500, 3600),
    ("理财申购", 9000, 11000), ("偿还借款", 6000, 6000), ("支付利息", 700, 700), ("物流运输", 1500, 1400),
    ("能源动力", 900, 950), ("设备采购", 3000, 2200), ("工程款", 2500, 2900), ("营销费用", 1200, 1150),
    ("租金支出", 500, 500), ("保证金支出", 600, 450), ("其他支出", 800, 760),
]
DEMO_LAST_BALANCE_YI = 3.6


def build_demo(out_dir):
    import random
    rnd = random.Random(DEMO_SEED)
    os.makedirs(out_dir, exist_ok=True)
    limits = {s: rnd.choice([2000, 3000, 5000, 8000]) for s in DEMO_SUBS}
    accounts = []
    for s in DEMO_SUBS:
        for j in range(1, rnd.randint(3, 5) + 1):
            accounts.append({"账户号": f"示例账户-{s[-2:]}-{j}", "子公司": s, "开户行": f"示例银行{rnd.randint(1, 6)}",
                             "余额(万)": round(rnd.uniform(500, 4000), 2)})
    acc_of = {}
    for a in accounts:
        acc_of.setdefault(a["子公司"], []).append(a["账户号"])
    sub_weight = [rnd.uniform(0.3, 3.0) for _ in DEMO_SUBS]
    day_weight = [1.0, 1.15, 1.05, 0.95, 1.3]
    inc_cats = [c for c, _, _ in DEMO_INCOME]
    exp_cats = [c for c, _, _ in DEMO_EXPENSE]
    flow = []
    for i in range(DEMO_N):
        sub = rnd.choices(DEMO_SUBS, sub_weight)[0]
        di = rnd.choices(range(5), day_weight)[0]
        kind = rnd.random()
        if kind < 0.30:
            dirn, memo, amt, cp = "收", rnd.choice(inc_cats), rnd.lognormvariate(math.log(30), 1.1), f"示例客户{rnd.randint(1, 400):03d}"
        elif kind < 0.70:
            dirn, memo, amt, cp = "付", rnd.choice(exp_cats), rnd.lognormvariate(math.log(25), 1.1), f"示例供应商{rnd.randint(1, 600):03d}"
        else:  # 内部归集/调拨：金额大，直接汇总会把收支放大
            dirn = rnd.choice(["收", "付"])
            memo, amt = rnd.choice(["资金归集", "内部调拨"]), rnd.lognormvariate(math.log(400), 0.9)
            cp = rnd.choice([x for x in DEMO_SUBS if x != sub])
        if dirn == "付":
            amt = min(amt, limits[sub] * 0.95)
        h, m, sec = rnd.randint(8, 17), rnd.randint(0, 59), rnd.randint(0, 59)
        flow.append([di, f"{h:02d}:{m:02d}:{sec:02d}", sub, dirn, round(amt, 2), memo, cp])
    # 植入预警：单笔超限（W003）、大额摘要缺失（W004）、22:00 后/6:00 前交易（W005）
    picks = rnd.sample(range(DEMO_N), 19)
    for k in picks[:6]:
        r = flow[k]; r[3] = "付"; r[4] = round(limits[r[2]] * rnd.uniform(1.05, 1.6), 2); r[5] = "设备采购"
    for k in picks[6:10]:
        r = flow[k]; r[4] = round(rnd.uniform(1100, 3000), 2); r[5] = ""
        if r[3] == "付":
            r[4] = min(r[4], limits[r[2]] * 0.95)
            if r[4] <= 1000:
                r[3] = "收"; r[4] = round(rnd.uniform(1100, 3000), 2)
    for k in picks[10:19]:
        r = flow[k]; r[1] = f"{rnd.choice([22, 23, 0, 1, 2, 5]):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}"
        r[4] = round(min(r[4], 200), 2)
    flow.sort(key=lambda r: (r[0], r[1]))
    rows = [{"流水号": f"LS-{i:06d}", "日期": DAYS[r[0]].isoformat(), "时间": r[1], "子公司名称": r[2],
             "账户号": rnd.choice(acc_of[r[2]]), "收付方向": r[3], "金额": r[4], "摘要/用途": r[5] or None,
             "对手方名称": r[6]} for i, r in enumerate(flow, 1)]
    plan = [{"类别": c, "计划收入(万)": p, "实际收入(万)": a, "计划支出(万)": None, "实际支出(万)": None,
             "执行率": f"{round(a / p * 100)}%", "备注": ""} for c, p, a in DEMO_INCOME]
    plan += [{"类别": c, "计划收入(万)": None, "实际收入(万)": None, "计划支出(万)": p, "实际支出(万)": a,
              "执行率": f"{round(a / p * 100)}%", "备注": ""} for c, p, a in DEMO_EXPENSE]
    plan.append({"类别": "【合计】", "计划收入(万)": sum(p for _, p, _ in DEMO_INCOME), "实际收入(万)": sum(a for _, _, a in DEMO_INCOME),
                 "计划支出(万)": sum(p for _, p, _ in DEMO_EXPENSE), "实际支出(万)": sum(a for _, _, a in DEMO_EXPENSE),
                 "执行率": "", "备注": ""})
    alerts = [{"子公司": s, "单笔支付上限(万)": limits[s], "余额预警线(万)": 300} for s in DEMO_SUBS]
    last_daily = [{"日期": f"2026-06-0{d}", "总资金余额(亿)": b} for d, b in ((1, 3.42), (2, 3.48), (3, 3.51), (4, 3.55), (5, DEMO_LAST_BALANCE_YI))]
    last_kpi = [("总资金余额(亿)", DEMO_LAST_BALANCE_YI), ("本周收入(万)", 59000), ("本周支出(万)", 61500), ("理财余额(万)", 42000),
                ("授信可用(万)", 150000), ("预警条数", 21), ("计划执行偏差项", 5)]
    invest = [{"产品": f"示例理财{i:02d}", "金额(万)": a, "年化收益率": f"{y}%"} for i, (a, y) in enumerate(
        [(12000, 2.1), (9000, 2.4), (8000, 1.9), (7000, 2.6), (6000, 2.2)], 1)]
    maturity = [{"产品": "示例理财06", "到期日": "2026-06-10", "金额(万)": 6000}, {"产品": "示例理财07", "到期日": "2026-06-12", "金额(万)": 6000}]
    files = {"flow": "银行流水明细.xlsx", "invest": "理财台账.xlsx", "plan": "资金计划表.xlsx",
             "alert": "预警规则表.xlsx", "last_week": "上周资金汇总.xlsx"}
    sheets = {"flow": {"流水明细": rows, "账户信息": accounts}, "invest": {"在投理财": invest, "本周到期": maturity},
              "plan": {"本周资金计划": plan}, "alert": {"账户预警规则": alerts},
              "last_week": {"上周每日汇总": last_daily, "上周关键指标": [{"指标": k, "上周(W23)值": v} for k, v in last_kpi]}}
    for key, fn in files.items():
        with pd.ExcelWriter(os.path.join(out_dir, fn), engine="openpyxl") as w:
            for sh, data in sheets[key].items():
                pd.DataFrame(data).to_excel(w, sheet_name=sh, index=False)
    return {k: os.path.join(out_dir, v) for k, v in files.items()}, rows


def run_demo(out_dir):
    """生成演示规模工作簿，用 treasury_pipeline.py 实跑，并写出 demo_summary.json（讲解页数字的来源）。"""
    import importlib.util, sys, contextlib, io
    paths, rows = build_demo(out_dir)
    scripts = os.path.join(os.path.dirname(HERE), "scripts")
    spec = importlib.util.spec_from_file_location("_demo_pipeline", os.path.join(scripts, "treasury_pipeline.py"))
    eng = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eng)
    with contextlib.redirect_stdout(io.StringIO()):
        res = eng.run_pipeline(paths)
    core = res["core"]
    by_type = {}
    for a in res["anomalies"]:
        by_type[a["type"]] = by_type.get(a["type"], 0) + 1
    num = lambda v: round(float(v), 2)
    summary = {
        "generator": "python3 skills/treasury-weekly-report/samples/gen_samples.py --demo",
        "seed": DEMO_SEED, "unit": "万元",
        "transaction_count": res["transaction_count"], "subsidiary_count": int(res["subsidiary_count"]),
        "account_count": int(res["account_count"]), "plan_categories": len(DEMO_INCOME) + len(DEMO_EXPENSE),
        "last_week_kpi_rows": len(core["last_kpi"]), "data_issues": res["data_issues"],
        "last_balance_wan": num(core["last_balance_wan"]),
        "actual_income": num(core["actual_income"]), "actual_expense": num(core["actual_expense"]),
        "daily_trend": [{k: (num(v) if isinstance(v, (int, float)) else v) for k, v in r.items()} for r in core["daily_trend"]],
        "income_items": [{"类别": r["类别"], "实际收入(万)": num(r["实际收入(万)"])} for r in core["income_items"]],
        "expense_items": [{"类别": r["类别"], "实际支出(万)": num(r["实际支出(万)"])} for r in core["expense_items"]],
        "plan_deviations": [{k: d[k] for k in ("类别", "执行率", "方向")} for d in res["plan_execution"]["deviations"]],
        "anomaly_count": len(res["anomalies"]), "anomaly_by_type": dict(sorted(by_type.items())),
        "subsidiary_heatmap": [{"full_name": h["full_name"], "data": [num(x) for x in h["data"]], "avg": num(h["avg"])}
                               for h in res["risk"]["subsidiary_heatmap"]],
        "flow_raw_sum": {"收": num(sum(r["金额"] for r in rows if r["收付方向"] == "收")),
                         "付": num(sum(r["金额"] for r in rows if r["收付方向"] == "付"))},
    }
    with open(os.path.join(HERE, "demo_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1, allow_nan=False)
        f.write("\n")
    print(f"demo: {summary['transaction_count']} 笔流水 → {out_dir}；摘要 → samples/demo_summary.json")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="生成演示规模数据（约 5 万笔）并实跑管线，写出 demo_summary.json")
    ap.add_argument("--out", default=os.path.join(HERE, "demo"), help="--demo 工作簿输出目录（默认 samples/demo/，不入库）")
    args = ap.parse_args()
    if args.demo:
        run_demo(args.out)
        return
    out = {case: build(case) for case in ("clean", "error")}
    with open(os.path.join(HERE, "expected.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("samples generated:", ", ".join(sorted(out)))


if __name__ == "__main__":
    main()
