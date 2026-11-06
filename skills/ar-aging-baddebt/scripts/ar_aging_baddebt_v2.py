"""应收账龄与坏账计提 v2 · 确定性计算脚本
用法: python ar_aging_baddebt_v2.py <数据目录> [基准日 YYYY-MM-DD] [out.json]
数据目录需包含：
  ar_ledger.csv             应收台账（发票号, 客户编码, 客户, 开票日期, 信用期, 到期日, 应收金额, 已收金额, 未收余额）
  receipts.csv              收款明细（收款单号, 发票号, 客户编码, 收款日期, 收款金额）
  customer_master.csv       客户主数据（客户编码, 客户, 信用期, 信用等级）
  provision_matrix.csv      账龄组合计提率（账龄段, 计提率）：未到期 / 1-30天 / 31-90天 / 91-180天 / 181-365天 / 365天以上
  individual_provision.csv  单项计提清单（客户编码, 单项计提比例, 原因）—— 可选
  gl_balance.csv            总账（科目, 期末余额）：1122 应收账款、1231 坏账准备
基准日必须显式给出（默认 2026-09-30）。

逐单判据：
  ① 主数据     客户已登记；台账信用期 == 主数据信用期
  ② 到期日     到期日 == 开票日期 + 主数据信用期（独立推算）
  ③ 已收核实   台账已收 == Σ收款明细（只计基准日及以前；期后收款不得提前冲减）
  ④ 余额恒等式 未收 == 应收 − 已收
  ⑤ 方向       收款超过应收 → 贷方余额（预收/多收）单列，不抵同客户应收、不计提
  ⑥ 唯一性     同客户 + 开票日期 + 金额只开一次
全表判据：
  ⑦ 计提率表   随账龄单调不减，且在 [0, 1] 内
  ⑧ 范围完备   收款必须对应存在的发票
  ⑨ 计提       单项计提客户按单项比例；其余按账龄组合；应计提 vs 账面坏账准备 → 本期应补提/冲回
  ⑩ 对总账     核实后应收明细 == 应收账款总账
守恒：Σ各账龄段 == 核实借方余额；Σ计提 == Σ(段余额×段率) + Σ单项计提；笔数守恒。
可回溯：输入 SHA-256 指纹 + 基准日 + 时间戳；逐单推算到期日、账龄、核实未收、计提方式与金额。
"""
import sys, os, csv, json, hashlib, re, math
from collections import defaultdict, Counter
from datetime import date, datetime, timedelta

BASE_DEFAULT = "2026-09-30"
TOL = 0.5
BUCKETS = ["未到期", "1-30天", "31-90天", "91-180天", "181-365天", "365天以上"]


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "ar_ledger.csv": ("应收金额", "已收金额", "未收余额", "信用期"),
    "receipts.csv": ("收款金额",),
    "customer_master.csv": ("信用期",),
    "provision_matrix.csv": ("计提率",),
    "individual_provision.csv": ("单项计提比例",),
    "gl_balance.csv": ("期末余额",),
}
BLANK_OK = set()
DQ = []  # 本次运行的数据质量问题；入口函数开头清空


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
    if d <= 90:
        return "31-90天"
    if d <= 180:
        return "91-180天"
    if d <= 365:
        return "181-365天"
    return "365天以上"


def check(data_dir, base=BASE_DEFAULT):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    BD = date.fromisoformat(base)
    ar = load(P("ar_ledger.csv"))
    rcs = load(P("receipts.csv"))
    master = {r["客户编码"]: r for r in load(P("customer_master.csv"))}
    rates = {r["账龄段"]: num(r["计提率"]) for r in load(P("provision_matrix.csv"))}
    ind = {r["客户编码"]: r for r in load(P("individual_provision.csv"))}
    gl = {r["科目"][:4]: num(r["期末余额"]) for r in load(P("gl_balance.csv"))}
    table_issues = []
    seq = [rates.get(b) for b in BUCKETS]
    if None in seq:
        table_issues.append(f"计提率表缺少账龄段({','.join(b for b, v in zip(BUCKETS, seq) if v is None)})")
    seq = [v or 0.0 for v in seq]
    for a, b, x, y in zip(BUCKETS, BUCKETS[1:], seq, seq[1:]):
        if y < x:
            table_issues.append(f"计提率不单调({b} {y:.0%} < {a} {x:.0%})")
    if any(v < 0 or v > 1 for v in seq):
        table_issues.append("计提率超出 0–100%")
    ids = {r["发票号"] for r in ar}
    got, got_after, rc_list, orphan = defaultdict(float), defaultdict(float), defaultdict(list), []
    for r in rcs:
        if r["发票号"] not in ids:
            orphan.append({"收款单号": r["收款单号"], "发票号": r["发票号"], "客户编码": r["客户编码"], "收款日期": r["收款日期"], "收款金额": num(r["收款金额"])})
            continue
        if date.fromisoformat(r["收款日期"]) <= BD:
            got[r["发票号"]] += num(r["收款金额"])
            rc_list[r["发票号"]].append(r["收款单号"])
        else:
            got_after[r["发票号"]] += num(r["收款金额"])
    dup = defaultdict(list)
    for r in ar:
        dup[(r["客户编码"], r["开票日期"], round(num(r["应收金额"]), 2))].append(r["发票号"])
    results = []
    for r in ar:
        amt, rec, bal = num(r["应收金额"]), num(r["已收金额"]), num(r["未收余额"])
        m = master.get(r["客户编码"])
        cp = int(num(m["信用期"])) if m else int(num(r["信用期"]))
        due = date.fromisoformat(r["开票日期"]) + timedelta(days=cp)
        days = (BD - due).days
        real = round(got.get(r["发票号"], 0.0), 2)
        issues, st = [], "pass"

        def bad(msg):
            nonlocal st
            issues.append(msg)
            st = "fail"

        if not m:
            bad(f"客户不在主数据({r['客户编码']})")
        elif int(num(r["信用期"])) != cp:
            bad(f"信用期与主数据不符(台账{int(num(r['信用期']))}天/主数据{cp}天)")
        if r["到期日"] != due.isoformat():
            bad(f"到期日与信用期不符(台账{r['到期日']}/推算{due.isoformat()})")
        if abs(rec - real) > TOL:
            after = got_after.get(r["发票号"], 0.0)
            if after and abs(rec - real - after) <= TOL:
                bad(f"期后收款提前冲减({after:,.2f} 收于基准日之后)")
            else:
                bad(f"已收与收款明细不符(台账{rec:,.2f}/明细{real:,.2f})")
        if abs(bal - (amt - rec)) > TOL:
            bad(f"余额≠应收−已收(差{bal - (amt - rec):+,.2f})")
        if real > amt + TOL:
            bad(f"收款超过应收(多收{real - amt:,.2f})")
        g = dup[(r["客户编码"], r["开票日期"], round(amt, 2))]
        if len(g) > 1:
            bad(f"疑似重复开票({'/'.join(x for x in g if x != r['发票号'])})")
        v = round(amt - real, 2)
        if v > TOL:
            seg = bucket(days)
            if r["客户编码"] in ind:
                rate, how = num(ind[r["客户编码"]]["单项计提比例"]), f"单项({ind[r['客户编码']]['原因']})"
            else:
                rate, how = rates.get(seg, 0.0), "账龄组合"
        else:
            seg = "贷方余额" if v < -TOL else "已结清"
            rate, how = 0.0, "—"
        results.append({"发票号": r["发票号"], "客户编码": r["客户编码"], "客户": r["客户"], "开票日期": r["开票日期"],
                        "推算到期日": due.isoformat(), "逾期天数": days, "账龄段": seg, "应收金额": amt,
                        "台账已收": rec, "明细已收": real, "台账未收": bal, "核实未收": v,
                        "计提方式": how, "计提率": rate, "应计提": round(max(v, 0) * rate, 2),
                        "status": st, "issues": issues or ["正确"]})
    cnt = Counter(x["status"] for x in results)
    assert sum(cnt.values()) == len(ar), "笔数守恒失败"
    bk = {b: 0.0 for b in BUCKETS}
    bkc = {b: 0 for b in BUCKETS}
    bkp = {b: 0.0 for b in BUCKETS}
    dr = cr = ind_prov = 0.0
    cust = defaultdict(lambda: {"未收": 0.0, "逾期": 0.0, "应计提": 0.0, "贷方余额": 0.0})
    for x in results:
        v = x["核实未收"]
        if v > TOL:
            dr += v
            bk[x["账龄段"]] += v
            bkc[x["账龄段"]] += 1
            if x["计提方式"].startswith("单项"):
                ind_prov += x["应计提"]
            else:
                bkp[x["账龄段"]] += x["应计提"]
            c = cust[x["客户"]]
            c["未收"] += v
            c["应计提"] += x["应计提"]
            if x["账龄段"] != "未到期":
                c["逾期"] += v
        elif v < -TOL:
            cr += -v
            cust[x["客户"]]["贷方余额"] += -v
    assert abs(sum(bk.values()) - dr) < 0.05, "分桶守恒失败"
    prov = round(sum(x["应计提"] for x in results), 2)
    combo = sum(bk[b] - sum(x["核实未收"] for x in results if x["账龄段"] == b and x["计提方式"].startswith("单项")) for b in BUCKETS)
    assert abs(prov - (sum(bkp.values()) + ind_prov)) < 0.05, "计提恒等式失败"
    ledger = round(sum(num(r["未收余额"]) for r in ar), 2)
    gl_ar, gl_bd = gl.get("1122", 0.0), gl.get("1231", 0.0)
    overdue = round(dr - bk["未到期"], 2)
    issue_types = Counter(re.sub(r"\(.*", "", i) for x in results for i in x["issues"])
    for t in table_issues:
        issue_types[re.sub(r"\(.*", "", t)] += 1
    if orphan:
        issue_types["无发票收款"] = len(orphan)
    gl_issues = []
    if abs(ledger - gl_ar) > TOL:
        gl_issues.append(f"应收台账与总账不符(台账{ledger:,.2f}/总账{gl_ar:,.2f})")
    if abs(prov - gl_bd) > TOL:
        gl_issues.append(f"坏账准备需{'补提' if prov > gl_bd else '冲回'} {abs(prov - gl_bd):,.2f}(应计提{prov:,.2f}/账面{gl_bd:,.2f})")
    for t in gl_issues:
        issue_types[re.sub(r"[\( ].*", "", t)] += 1
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「正确」
    all_ok = not DQ and not (set(issue_types) - {"正确"})
    return {
        "base_date": base, "summary": dict(cnt), "total": len(results), "all_ok": all_ok, "data_issues": list(DQ),
        "pass_rate": round(cnt.get("pass", 0) / len(results) * 100, 1), "issue_types": dict(issue_types),
        "table_issues": table_issues, "gl_issues": gl_issues, "orphan_receipts": orphan,
        "bucket_amt": {k: round(v, 2) for k, v in bk.items()}, "bucket_cnt": bkc, "bucket_prov": {k: round(v, 2) for k, v in bkp.items()},
        "rates": rates, "individual_provision": round(ind_prov, 2),
        "overdue_total": overdue, "overdue_rate": round(overdue / dr * 100, 1) if dr else 0,
        "provision_total": prov, "provision_rate": round(prov / dr * 100, 1) if dr else 0,
        "conservation": {"借方余额(核实)": round(dr, 2), "贷方余额(核实)": round(cr, 2), "各段合计": round(sum(bk.values()), 2),
                         "台账未收合计": ledger, "应收总账": gl_ar, "台账−总账": round(ledger - gl_ar, 2),
                         "组合计提": round(sum(bkp.values()), 2), "单项计提": round(ind_prov, 2), "应计提合计": prov,
                         "账面坏账准备": gl_bd, "应补提(负为冲回)": round(prov - gl_bd, 2),
                         "发票笔数": len(ar), "收款笔数": len(rcs)},
        "customers": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in sorted(cust.items(), key=lambda x: -x[1]["应计提"])},
        "inputs": [x for x in (fp(P(f)) for f in ["ar_ledger.csv", "receipts.csv", "customer_master.csv", "provision_matrix.csv", "individual_provision.csv", "gl_balance.csv"]) if x],
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
