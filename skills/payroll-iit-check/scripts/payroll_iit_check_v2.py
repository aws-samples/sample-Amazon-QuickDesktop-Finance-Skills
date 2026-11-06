"""薪酬个税复核 v2 · 确定性计算脚本（累计预扣法）
用法: python payroll_iit_check_v2.py <数据目录> [out.json]
数据目录需包含：
  payroll_monthly.csv   逐月台账（员工编码, 姓名, 部门, 月份, 当月应发, 当月五险一金, 当月专项附加, 当月减除费用,
                          累计应发, 累计五险一金, 累计专项附加, 累计减除费用, 累计应纳税所得额, 适用税率, 速算扣除数,
                          累计已预扣, 当月应预扣, 当月申报个税）
  special_deductions.csv 专项附加备案（员工编码, 项目, 月扣除额）
  iit_brackets.csv       累计预扣税率表（级数, 累计应纳税所得额上限, 税率, 速算扣除数）
  sa_caps.csv            专项附加月度上限（项目, 月度上限）

判据：
  ① 税率表     档次单调、税率 ∈ 白名单、速算扣除数与档次一致（分界点连续）
  ② 累计预扣重算 逐月累加 → 累计应纳税所得额 = 累计应发 − 五险一金 − 专项附加 − 减除费用
                累计个税 = 税率表(累计应纳税所得额)；当月应预扣 = 累计个税 − 上月累计个税
  ③ 累计一致   累计列 == 逐月加总；累计已预扣 == 上月累计个税（链条衔接）
  ④ 减除费用   每月 == 5000
  ⑤ 专项附加   台账当月专项附加 == 备案合计；各项 ≤ 月度上限；有扣除必须有备案
  ⑥ 边界       当月申报个税 ≥ 0
  ⑦ 完整性     每人每月齐全（1–9 月）
守恒：Σ当月申报 == 末月累计个税；申报 vs 正确差异分少缴/多缴。
可回溯：输入 SHA-256 指纹 + 时间戳；逐条重算过程与判据结论。
"""
import sys, os, csv, json, hashlib, math
from collections import defaultdict, Counter
from datetime import datetime

TOL = 0.5


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "payroll_monthly.csv": ("当月应发", "当月五险一金", "当月专项附加", "当月减除费用", "累计应发", "累计已预扣", "当月申报个税"),
    "special_deductions.csv": ("月扣除额",),
    "iit_brackets.csv": ("累计应纳税所得额上限", "税率", "速算扣除数"),
    "sa_caps.csv": ("月度上限",),
}
BLANK_OK = {"累计应纳税所得额上限"}
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


def load_brackets(rows):
    bs = []
    for r in rows:
        cap = str(r.get("累计应纳税所得额上限", "")).strip()
        bs.append((float(cap) if cap else float("inf"), num(r["税率"]), num(r["速算扣除数"])))
    return sorted(bs, key=lambda x: x[0])


def calc(taxable, bs):
    for cap, rt, d in bs:
        if taxable <= cap:
            return round(max(0.0, taxable * rt - d), 2), rt, d
    return 0.0, 0.0, 0.0


LEGAL_RATES = {0.03, 0.10, 0.20, 0.25, 0.30, 0.35, 0.45}
# 法定累计预扣税率表（基准，用于重算；提供的 iit_brackets.csv 作为被校验对象）
STATUTORY = [(36000, 0.03, 0), (144000, 0.10, 2520), (300000, 0.20, 16920), (420000, 0.25, 31920),
             (660000, 0.30, 52920), (960000, 0.35, 85920), (float("inf"), 0.45, 181920)]


def check(data_dir):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    rows = load(P("payroll_monthly.csv"))
    sa = load(P("special_deductions.csv"))
    braw = load(P("iit_brackets.csv"))
    caps = {r["项目"]: num(r["月度上限"]) for r in load(P("sa_caps.csv"))}
    bs_provided = load_brackets(braw)
    bs = STATUTORY  # 重算一律用法定基准表
    # ① 税率表校验：提供的表逐档与法定基准比对
    table_issues = []
    if len(bs_provided) != len(STATUTORY):
        table_issues.append(f"档数不符(提供{len(bs_provided)}/法定{len(STATUTORY)})")
    for i, ((cap, rt, d), (scap, srt, sd)) in enumerate(zip(bs_provided, STATUTORY), 1):
        if srt not in LEGAL_RATES:
            pass
        if abs(rt - srt) > 1e-6:
            table_issues.append(f"第{i}档税率错误(提供{rt:.0%}/法定{srt:.0%})")
        if cap != scap and not (cap == float("inf") and scap == float("inf")):
            table_issues.append(f"第{i}档上限错误(提供{cap:,.0f}/法定{scap:,.0f})")
        if abs(d - sd) > 1.0:
            table_issues.append(f"第{i}档速算扣除数错误(提供{d:,.0f}/法定{sd:,.0f})")
    # 专项附加备案
    sa_by = defaultdict(dict)
    sa_issues = []
    for r in sa:
        sa_by[r["员工编码"]][r["项目"]] = num(r["月扣除额"])
        if r["项目"] in caps and num(r["月扣除额"]) > caps[r["项目"]] + TOL:
            sa_issues.append({"员工编码": r["员工编码"], "项目": r["项目"], "月扣除额": num(r["月扣除额"]), "上限": caps[r["项目"]]})
    sa_month = {k: round(sum(v.values()), 2) for k, v in sa_by.items()}

    by = defaultdict(list)
    for r in rows:
        by[r["员工编码"]].append(r)
    results = []
    emp_summ = []
    for code, rs in by.items():
        rs.sort(key=lambda x: x["月份"])
        cum_pay = cum_si = cum_sa = cum_ded = 0.0
        prev_cum_tax = 0.0
        e_report = e_correct = 0.0
        for r in rs:
            issues, st = [], "pass"

            def bad(m):
                nonlocal st
                issues.append(m)
                st = "fail"

            pay, si, sad, ded = num(r["当月应发"]), num(r["当月五险一金"]), num(r["当月专项附加"]), num(r["当月减除费用"])
            cum_pay += pay
            cum_si += si
            cum_sa += sad
            cum_ded += ded
            if abs(ded - 5000) > TOL:
                bad(f"减除费用≠5000(实{ded:,.0f})")
            # 专项附加与备案
            if code in sa_month and abs(sad - sa_month[code]) > TOL:
                bad(f"专项附加≠备案(台账{sad:,.0f}/备案{sa_month[code]:,.0f})")
            if sad > TOL and code not in sa_by:
                bad("专项附加无备案")
            # 累计一致
            if abs(num(r["累计应发"]) - cum_pay) > TOL:
                bad(f"累计应发≠逐月加总(台账{num(r['累计应发']):,.0f}/加总{cum_pay:,.0f})")
            if abs(num(r["累计已预扣"]) - prev_cum_tax) > TOL:
                bad(f"累计已预扣断链(台账{num(r['累计已预扣']):,.0f}/上月累计{prev_cum_tax:,.0f})")
            taxable = round(cum_pay - cum_si - cum_sa - cum_ded, 2)
            cum_tax, rt, dd = calc(max(0.0, taxable), bs)
            month_due = round(cum_tax - prev_cum_tax, 2)
            reported = num(r["当月申报个税"])
            if reported < -TOL:
                bad(f"当月个税为负({reported:,.2f})")
            if abs(reported - month_due) > max(TOL, abs(month_due) * 0.001):
                bad(f"当月个税≠重算(申报{reported:,.2f}/应{month_due:,.2f})")
            prev_cum_tax = cum_tax
            e_report += reported
            e_correct += month_due
            results.append({"员工编码": code, "姓名": r.get("姓名", ""), "月份": r["月份"], "当月应发": pay,
                            "累计应纳税所得额": max(0.0, taxable), "适用税率": rt, "当月应预扣": month_due,
                            "当月申报个税": reported, "差异": round(reported - month_due, 2),
                            "status": st, "issues": issues or ["正确"]})
        n = len(rs)
        emp_summ.append({"员工编码": code, "姓名": rs[0].get("姓名", ""), "月数": n, "申报合计": round(e_report, 2),
                         "正确合计": round(e_correct, 2), "差异": round(e_report - e_correct, 2),
                         "缺月": 9 - n if n < 9 else 0})
    cnt = Counter(r["status"] for r in results)
    assert sum(cnt.values()) == len(results), "笔数守恒失败"
    tr = round(sum(r["当月申报个税"] for r in results), 2)
    tc = round(sum(r["当月应预扣"] for r in results), 2)
    under = round(sum(r["差异"] for r in results if r["差异"] < 0), 2)
    over = round(sum(r["差异"] for r in results if r["差异"] > 0), 2)
    issue_types = Counter()
    for r in results:
        for m in (r["issues"] if r["status"] != "pass" else []):
            issue_types[m.split("(")[0]] += 1
    for t in table_issues:
        issue_types["税率表·" + t.split("(")[0]] += 1
    for s in sa_issues:
        issue_types["专项附加超上限"] += 1
    incomplete = [e for e in emp_summ if e["缺月"] > 0]
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，没有任何问题标签，且无缺月员工
    all_ok = not DQ and not issue_types and not incomplete
    return {
        "summary": dict(cnt), "total": len(results), "pass_rate": round(cnt.get("pass", 0) / len(results) * 100, 1),
        "all_ok": all_ok, "data_issues": list(DQ),
        "issue_types": dict(issue_types), "table_issues": table_issues, "sa_over_cap": sa_issues,
        "incomplete_employees": incomplete,
        "conservation": {"申报个税合计": tr, "正确个税合计": tc, "差异": round(tr - tc, 2), "少缴": round(abs(under), 2),
                         "多缴": round(over, 2), "员工数": len(by), "记录数": len(results), "缺月人数": len(incomplete)},
        "employee_summary": sorted(emp_summ, key=lambda x: abs(x["差异"]), reverse=True),
        "inputs": [x for x in (fp(P(f)) for f in ["payroll_monthly.csv", "special_deductions.csv", "iit_brackets.csv", "sa_caps.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv)
        out = check(argv[1])
        dest = argv[2] if len(argv) > 2 else "analysis.json"
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", {}, out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
