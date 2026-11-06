"""客户对账单生成 v2 · 确定性计算脚本
用法: python customer_statement_v2.py <数据目录> [期间 YYYY-MM] [out.json]
数据目录需包含（金额口径：正 = 增加应收，负 = 减少应收）：
  prior_statements.csv   上期对账单（客户编码, 对账期间, 期末余额, 客户确认, 确认日期）
  opening_balance.csv    本期期初（客户编码, 期初余额）
  period_transactions.csv本期流水（流水号, 客户编码, 日期, 业务类型, 单据号, 发生额, 摘要）
  subledger_balance.csv  应收明细账期末（客户编码, 明细账期末余额）
  gl_balance.csv         总账（科目, 期间, 期末余额）：1122 应收账款；2203 合同负债(预收)
  customer_master.csv    客户主数据（客户编码, 客户, 对账邮箱）
  txn_types.csv          业务类型白名单（业务类型, 方向 +/-）

判据：
  ① 跨期衔接   本期期初 == 上期对账单期末；上期客户未回签 / 有异议 → 待核实
              上期对账单按（客户编码, 对账期间）取紧邻上一期的行，其他期间的行不参与判断；
              同一客户上一期有多行 → 上期对账单重复（fail），且每一行都要满足衔接与回签
  数据质量     某户有行因数值无法解析被剔除（见 data_issues）→ 含无法解析数据，该户至少待核实，不判可发送
  ② 流水准入   客户已登记；业务类型 ∈ 白名单；金额方向 == 类型方向；日期 ∈ 本期；流水号唯一
              不准入的流水不进对账单，逐笔列明原因
  ③ 恒等式     期末 == 期初 + 销售 − 收款 − 退货 − 折让（逐户）
  ④ 交叉一致   对账单期末 == 应收明细账期末；差额 = 被剔除流水影响 + 未解释差额
  ⑤ 总量守恒   Σ借方期末 == 1122 总账；Σ贷方期末（预收）== 2203 总账；借贷方分列，不相互抵消
输出：逐户对账单（期初、各类发生额、期末、流水明细、确认状态）+ 剔除流水清单 + 守恒。
可回溯：输入 SHA-256 指纹 + 期间 + 时间戳。
"""
import sys, os, csv, json, hashlib, re, math
from collections import defaultdict, Counter
from datetime import datetime

PERIOD_DEFAULT = "2026-09"
TOL = 0.5


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "prior_statements.csv": ("期末余额",),
    "opening_balance.csv": ("期初余额",),
    "period_transactions.csv": ("发生额",),
    "subledger_balance.csv": ("明细账期末余额",),
    "gl_balance.csv": ("期末余额",),
}
BLANK_OK = set()
DQ = []  # 本次运行的数据质量问题；入口函数开头清空
QUAR = []  # 被剔除的原始行（文件名, 行号, 行），用于把问题归到所在客户


def load(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8-sig") as f:
        return screen(p, list(csv.DictReader(f)))


def screen(p, rows):
    """按 NUMERIC 严格校验数值字段；不合格的行登记到 DQ 并剔除，返回可用行。行号按 CSV 文件行计（表头为第 1 行）。"""
    fn, keep = os.path.basename(p), []
    for i, r in enumerate(rows, 2):
        bad = []
        for k in NUMERIC.get(fn, ()):
            if k not in r:
                continue                      # 可选列整列缺失：由调用方按默认值处理
            v = (r[k] or "").strip()
            if not v:
                if k not in BLANK_OK:
                    bad.append((k, "数值缺失"))
                continue
            try:
                ok = math.isfinite(float(v))
            except ValueError:
                ok = False
            if not ok:
                bad.append((k, "数值无法解析"))
        for k, why in bad:
            DQ.append({"文件": fn, "行号": i, "字段": k, "原值": r[k], "问题": why})
        if bad:
            QUAR.append((fn, i, r))
        else:
            keep.append(r)
    return keep


def num(v):
    """严格数值：空白按 0（只出现在 BLANK_OK 字段与代码内默认值）；其余无法解析的值抛 ValueError，
    不静默按 0 计。输入文件的数值字段已由 screen() 预先校验。"""
    s = "" if v is None else str(v).strip()
    if not s:
        return 0.0
    x = float(s)
    if not math.isfinite(x):
        raise ValueError(f"非有限数值: {v!r}")
    return x


def fp(p):
    if not os.path.exists(p):
        return None
    return {"file": os.path.basename(p), "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(),
            "rows": max(sum(1 for _ in open(p, encoding="utf-8-sig")) - 1, 0)}


def check(data_dir, period=PERIOD_DEFAULT):
    DQ.clear(); QUAR.clear()
    P = lambda f: os.path.join(data_dir, f)
    # 上期对账单按（客户编码, 对账期间）取值：只认紧邻的上一期；其他期间的行不参与判断，
    # 同一客户上一期有多行时报「上期对账单重复」，并逐行检查衔接与回签
    y, m = map(int, period.split("-"))
    prev_period = f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"
    prior_all = defaultdict(list)
    for r in load(P("prior_statements.csv")):
        if r.get("对账期间", "") == prev_period:
            prior_all[r["客户编码"]].append(r)
    prior = {k: v[0] for k, v in prior_all.items()}
    opening = {r["客户编码"]: num(r["期初余额"]) for r in load(P("opening_balance.csv"))}
    txs = load(P("period_transactions.csv"))
    subl = {r["客户编码"]: num(r["明细账期末余额"]) for r in load(P("subledger_balance.csv"))}
    gl = {r["科目"][:4]: num(r["期末余额"]) for r in load(P("gl_balance.csv"))}
    master = {r["客户编码"]: r for r in load(P("customer_master.csv"))}
    types = {r["业务类型"]: r["方向"] for r in load(P("txn_types.csv"))} or {"销售": "+", "收款": "-", "退货": "-", "折让": "-"}
    seen = Counter(t["流水号"] for t in txs)
    first = set()
    kept, rejected = [], []
    for t in txs:
        a = num(t["发生额"])
        t["发生额"] = a
        why = []
        if t["客户编码"] not in master:
            why.append(f"客户不在主数据({t['客户编码']})")
        if t["业务类型"] not in types:
            why.append(f"业务类型不在白名单({t['业务类型']})")
        elif (types[t["业务类型"]] == "+") != (a > 0):
            why.append(f"方向错误({t['业务类型']}应为{types[t['业务类型']]}，实为{a:+,.2f})")
        if not t["日期"].startswith(period):
            why.append(f"跨期流水({t['日期']})")
        if seen[t["流水号"]] > 1:
            if t["流水号"] in first:
                why.append("流水重复(同一流水号第二次出现)")
            first.add(t["流水号"])
        (rejected if why else kept).append({**t, "原因": why})
    by = defaultdict(list)
    for t in kept:
        by[t["客户编码"]].append(t)
    # 被剔除的行归到所在客户：该户对账单少了这一行，不能判「可发送」
    quar_c = defaultdict(list)
    for fn, i, r in QUAR:
        quar_c[r.get("客户编码", "")].append(f"{fn}第{i}行")
    rej_by = defaultdict(float)
    for t in rejected:
        rej_by[t["客户编码"]] += t["发生额"]
    stmts = []
    # 往来单位全集：期初、上期对账单、本期流水、明细账、被剔除行任一出现即出对账单，不丢户
    for cid in sorted(set(opening) | set(prior) | set(by) | set(subl) | (set(quar_c) - {""})):
        name = master.get(cid, {}).get("客户", cid)
        ob = opening.get(cid, 0.0)
        rows_p = prior_all.get(cid, [])
        pe = num(rows_p[0]["期末余额"]) if rows_p else None
        T = by.get(cid, [])
        s = lambda k: round(sum(x["发生额"] for x in T if x["业务类型"] == k), 2)
        sale, rcv, ret, dsc = s("销售"), -s("收款"), -s("退货"), -s("折让")
        end = round(ob + sale - rcv - ret - dsc, 2)
        assert abs(end - round(ob + sum(x["发生额"] for x in T), 2)) < 0.05, f"{cid} 恒等式失败"
        sl = subl.get(cid)
        diff = round((sl if sl is not None else end) - end, 2)
        explained = round(rej_by.get(cid, 0.0), 2)
        unexplained = round(diff - explained, 2)
        issues, st = [], "pass"
        if cid not in opening:
            issues.append("无期初余额记录")
            if st == "pass":
                st = "review"
        if sl is None:
            issues.append("无明细账余额记录")
            if st == "pass":
                st = "review"
        if not rows_p:
            issues.append(f"无上期对账单({prev_period})")
            st = "review"
        elif len(rows_p) > 1:
            issues.append(f"上期对账单重复({prev_period}共{len(rows_p)}行)")
            st = "fail"
        for pr in rows_p:      # 每一行上期对账单都要满足衔接与回签，任何一行不满足即报出
            pv = num(pr["期末余额"])
            msg = f"期初与上期期末不符(期初{ob:,.2f}/上期{pv:,.2f})"
            if abs(ob - pv) > TOL and msg not in issues:
                issues.append(msg)
                st = "fail"
        for pr in rows_p:
            msg = f"上期对账{pr['客户确认']}"
            if pr["客户确认"] != "已回签" and msg not in issues:
                issues.append(msg)
                if st == "pass":
                    st = "review"
        if abs(explained) > TOL:
            issues.append(f"含被剔除流水({explained:+,.2f}，见剔除清单)")
            if st == "pass":
                st = "review"
        if abs(unexplained) > TOL:
            issues.append(f"明细账与对账单不符(未解释差{unexplained:+,.2f})")
            st = "fail"
        if cid in quar_c:
            issues.append(f"含无法解析数据({'、'.join(quar_c[cid])}已剔除，见 data_issues)")
            if st == "pass":
                st = "review"
        stmts.append({"客户编码": cid, "客户": name, "对账邮箱": master.get(cid, {}).get("对账邮箱", ""), "上期期间": prev_period, "上期期末": pe, "期初": ob,
                      "销售": sale, "收款": rcv, "退货": ret, "折让": dsc, "期末": end, "余额方向": "应收" if end >= 0 else "预收(我方欠贵方)",
                      "明细账期末": sl, "差额": diff, "剔除流水影响": explained, "未解释差额": unexplained, "笔数": len(T),
                      "status": st, "issues": issues or ["可发送"],
                      "明细": [{"日期": x["日期"], "流水号": x["流水号"], "业务类型": x["业务类型"], "单据号": x["单据号"], "发生额": x["发生额"], "摘要": x["摘要"]} for x in sorted(T, key=lambda x: x["日期"])]})
    cnt = Counter(s["status"] for s in stmts)
    assert sum(cnt.values()) == len(stmts)
    assert len(kept) + len(rejected) == len(txs), "流水笔数守恒失败"
    dr = round(sum(s["期末"] for s in stmts if s["期末"] > 0), 2)
    cr = round(-sum(s["期末"] for s in stmts if s["期末"] < 0), 2)
    tot = {k: round(sum(s[k] for s in stmts), 2) for k in ("期初", "销售", "收款", "退货", "折让", "期末")}
    assert abs(tot["期末"] - (tot["期初"] + tot["销售"] - tot["收款"] - tot["退货"] - tot["折让"])) < 0.05, "总量守恒失败"
    gl_issues = []
    if abs(dr - gl.get("1122", dr)) > TOL:
        gl_issues.append(f"应收合计与1122总账不符(对账单{dr:,.2f}/总账{gl.get('1122', 0):,.2f})")
    if abs(cr - gl.get("2203", cr)) > TOL:
        gl_issues.append(f"预收合计与2203总账不符(对账单{cr:,.2f}/总账{gl.get('2203', 0):,.2f})")
    issue_types = Counter(re.sub(r"\(.*", "", i) for s in stmts for i in s["issues"])
    for t in rejected:
        for w in t["原因"]:
            issue_types["流水剔除·" + re.sub(r"\(.*", "", w)] += 1
    for g in gl_issues:
        issue_types[re.sub(r"\(.*", "", g)] += 1
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「可发送」
    all_ok = not DQ and not (set(issue_types) - {"可发送"})
    return {"period": period, "prior_period": prev_period, "summary": dict(cnt), "total": len(stmts), "sendable": cnt.get("pass", 0),
            "all_ok": all_ok, "data_issues": list(DQ),
            "issue_types": dict(issue_types), "gl_issues": gl_issues,
            "rejected": [{"流水号": t["流水号"], "客户编码": t["客户编码"], "日期": t["日期"], "业务类型": t["业务类型"], "发生额": t["发生额"], "原因": "；".join(t["原因"])} for t in rejected],
            "conservation": {**{f"合计{k}": v for k, v in tot.items()}, "借方期末(应收)": dr, "贷方期末(预收)": cr,
                             "1122总账": gl.get("1122"), "2203总账": gl.get("2203"), "流水总笔数": len(txs), "进入对账单": len(kept), "剔除": len(rejected)},
            "inputs": [x for x in (fp(P(f)) for f in ["prior_statements.csv", "opening_balance.csv", "period_transactions.csv", "subledger_balance.csv", "gl_balance.csv", "customer_master.csv", "txn_types.csv"]) if x],
            "timestamp": datetime.now().isoformat(timespec="seconds"), "statements": stmts}


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv)
        per = argv[2] if len(argv) > 2 and re.match(r"\d{4}-\d{2}$", argv[2]) else PERIOD_DEFAULT
        out = check(argv[1], per)
        dest = argv[-1] if argv[-1].endswith(".json") else "statements.json"
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "statements.json", {"period": per}, out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
