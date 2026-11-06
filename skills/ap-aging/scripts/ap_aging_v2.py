"""应付账龄分析 v2 · 确定性计算脚本
用法: python ap_aging_v2.py <数据目录> [基准日 YYYY-MM-DD] [out.json]
数据目录需包含：accounts_payable.csv / payments.csv / supplier_master.csv / gl_balance.csv
判据：主数据、到期日独立推算、已付对付款明细、付款对象与应付供应商一致（不一致的付款不冲减应付）、余额恒等式、多付单列、
重复入账/付款、无应付付款、台账与总账一致；分桶守恒与笔数守恒；数值无法解析的行剔除并登记 data_issues，all_ok=False。
"""
import sys, os, csv, json, hashlib, re, math
from collections import defaultdict, Counter
from datetime import date, datetime, timedelta

BASE_DEFAULT = "2026-09-30"
TOL = 0.5
BUCKETS = ["未到期", "1-30天", "31-60天", "61-90天", "90天以上"]


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "accounts_payable.csv": ("应付金额", "已付金额", "未付余额", "账期天数"),
    "payments.csv": ("付款金额",),
    "supplier_master.csv": ("账期天数",),
    "gl_balance.csv": ("总账余额",),
}
BLANK_OK = set()
DQ = []  # 本次运行的数据质量问题；入口函数开头清空


def load(p):
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
        if not bad:
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


def bucket(d):
    if d <= 0:
        return "未到期"
    if d <= 30:
        return "1-30天"
    if d <= 60:
        return "31-60天"
    if d <= 90:
        return "61-90天"
    return "90天以上"


def check(data_dir, base=BASE_DEFAULT):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    B = date.fromisoformat(base)
    ap = load(P("accounts_payable.csv"))
    pays = load(P("payments.csv"))
    master = {r["供应商编码"]: r for r in load(P("supplier_master.csv"))}
    gl_bal = sum(num(r["总账余额"]) for r in load(P("gl_balance.csv")))
    ap_ids = {r["应付单号"] for r in ap}
    ap_sup = {r["应付单号"]: r["供应商编码"] for r in ap}
    pay_by = defaultdict(float)
    pay_cnt = defaultdict(list)
    payee_bad = defaultdict(list)   # 付款对象与应付供应商不一致：不冲减该应付
    orphan = []
    for p in pays:
        if date.fromisoformat(p["付款日期"]) > B:
            continue
        if p["应付单号"] in ap_ids:
            if p["供应商编码"] != ap_sup[p["应付单号"]]:
                payee_bad[p["应付单号"]].append(f"{p['付款单号']} 付{p['供应商编码']}/应付{ap_sup[p['应付单号']]}")
                continue
            pay_by[p["应付单号"]] += num(p["付款金额"])
            pay_cnt[p["应付单号"]].append(p["付款单号"])
        else:
            orphan.append({"付款单号": p["付款单号"], "应付单号": p["应付单号"], "供应商编码": p["供应商编码"], "付款金额": num(p["付款金额"])})
    dupkey = defaultdict(list)
    for r in ap:
        dupkey[(r["供应商编码"], r["发票日期"], round(num(r["应付金额"]), 2))].append(r["应付单号"])
    results = []
    for r in ap:
        amt, paid, bal = num(r["应付金额"]), num(r["已付金额"]), num(r["未付余额"])
        m = master.get(r["供应商编码"])
        terms = int(num(m["账期天数"])) if m else int(num(r["账期天数"]))
        due = date.fromisoformat(r["发票日期"]) + timedelta(days=terms)
        days = (B - due).days
        paid_real = round(pay_by.get(r["应付单号"], 0.0), 2)
        issues, status = [], "pass"

        def bad(msg):
            nonlocal status
            issues.append(msg)
            status = "fail"

        if r["应付单号"] in payee_bad:
            bad(f"付款对象与应付供应商不一致({'；'.join(payee_bad[r['应付单号']])})")
        if not m:
            bad(f"供应商不在主数据({r['供应商编码']})")
        elif int(num(r["账期天数"])) != terms:
            bad(f"账期与主数据不符(台账{int(num(r['账期天数']))}天/主数据{terms}天)")
        if r["到期日"] != due.isoformat():
            bad(f"到期日与账期不符(台账{r['到期日']}/推算{due.isoformat()})")
        if abs(paid - paid_real) > TOL:
            bad(f"已付与付款明细不符(台账{paid:,.2f}/明细{paid_real:,.2f})")
        if abs(bal - (amt - paid)) > TOL:
            bad(f"余额≠应付−已付(差{bal - (amt - paid):+,.2f})")
        if paid_real > amt + TOL:
            bad(f"付款超过应付(多付{paid_real - amt:,.2f})")
        if len(pay_cnt.get(r["应付单号"], [])) > 1 and paid_real > amt + TOL:
            bad(f"疑似重复付款({'/'.join(pay_cnt[r['应付单号']])})")
        g = dupkey[(r["供应商编码"], r["发票日期"], round(amt, 2))]
        if len(g) > 1:
            bad(f"疑似重复入账({'/'.join(x for x in g if x != r['应付单号'])})")
        true_bal = round(amt - paid_real, 2)
        results.append({"应付单号": r["应付单号"], "供应商编码": r["供应商编码"], "供应商": r["供应商"], "发票日期": r["发票日期"],
                        "推算到期日": due.isoformat(), "账龄天数": days, "账龄段": bucket(days) if true_bal > TOL else ("借方余额" if true_bal < -TOL else "已结清"),
                        "应付金额": amt, "台账已付": paid, "明细已付": paid_real, "台账未付": bal, "核实未付": true_bal,
                        "status": status, "issues": issues or ["正确"]})
    cnt = Counter(r["status"] for r in results)
    assert sum(cnt.values()) == len(ap), "笔数守恒失败"
    bk_amt = {b: 0.0 for b in BUCKETS}
    bk_cnt = {b: 0 for b in BUCKETS}
    cr, dr = 0.0, 0.0
    sup = defaultdict(lambda: {"未付": 0.0, "逾期": 0.0, "借方余额": 0.0})
    for r in results:
        v = r["核实未付"]
        if v > TOL:
            bk_amt[r["账龄段"]] += v
            bk_cnt[r["账龄段"]] += 1
            cr += v
            sup[r["供应商"]]["未付"] += v
            if r["账龄段"] != "未到期":
                sup[r["供应商"]]["逾期"] += v
        elif v < -TOL:
            dr += -v
            sup[r["供应商"]]["借方余额"] += -v
    assert abs(sum(bk_amt.values()) - cr) < 0.05, "分桶守恒失败"
    ledger_bal = round(sum(num(r["未付余额"]) for r in ap), 2)
    verified = round(cr - dr, 2)
    overdue = round(cr - bk_amt["未到期"], 2)
    due30 = round(sum(r["核实未付"] for r in results if r["核实未付"] > TOL and -30 <= r["账龄天数"] <= 0), 2)
    # 子账-总账勾稽：台账未付合计、核实净额均应与总账应付余额一致，不一致计入问题标签
    gl_issues = []
    if abs(ledger_bal - gl_bal) > TOL:
        gl_issues.append(f"应付台账与总账不符(台账{ledger_bal:,.2f}/总账{gl_bal:,.2f})")
    if abs(verified - gl_bal) > TOL:
        gl_issues.append(f"核实未付与总账不符(核实{verified:,.2f}/总账{gl_bal:,.2f})")
    issue_types = Counter(re.sub(r"\(.*", "", i) for r in results for i in r["issues"])
    for g in gl_issues:
        issue_types[re.sub(r"\(.*", "", g)] += 1
    if orphan:
        issue_types["无应付单的付款"] = len(orphan)
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「正确」
    all_ok = not DQ and not (set(issue_types) - {"正确"})
    return {
        "base_date": base, "summary": dict(cnt), "total": len(results), "all_ok": all_ok, "data_issues": list(DQ),
        "pass_rate": round(cnt.get("pass", 0) / len(results) * 100, 1),
        "issue_types": dict(issue_types),
        "bucket_amt": {k: round(v, 2) for k, v in bk_amt.items()}, "bucket_cnt": bk_cnt,
        "overdue_total": overdue, "overdue_rate": round(overdue / cr * 100, 1) if cr else 0, "due_next_30": due30,
        "debit_balance_total": round(dr, 2), "orphan_payments": orphan, "gl_issues": gl_issues,
        "conservation": {"贷方余额(核实)": round(cr, 2), "借方余额(核实)": round(dr, 2), "核实净额": verified,
                         "台账未付合计": ledger_bal, "总账余额": round(gl_bal, 2),
                         "台账−总账": round(ledger_bal - gl_bal, 2), "核实−总账": round(verified - gl_bal, 2),
                         "各桶合计": round(sum(bk_amt.values()), 2), "笔数": len(ap), "付款笔数": len(pays)},
        "supplier": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in sorted(sup.items(), key=lambda x: -x[1]["逾期"])},
        "inputs": [x for x in (fp(P(f)) for f in ["accounts_payable.csv", "payments.csv", "supplier_master.csv", "gl_balance.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv)
        base = argv[2] if len(argv) > 2 and re.match(r"\d{4}-\d{2}-\d{2}$", argv[2]) else BASE_DEFAULT
        out = check(argv[1], base)
        dest = argv[-1] if argv[-1].endswith(".json") else "analysis.json"
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", {"base": base}, out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
