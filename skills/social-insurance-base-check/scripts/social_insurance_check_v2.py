"""社保公积金基数核定 v2 · 确定性计算脚本
用法: python social_insurance_check_v2.py <数据目录> [out.json] [--avg-wage 社平工资]
数据目录需包含：
  employees.csv     花名册（员工编码, 姓名, 部门, 用工类型[在职/新入职], 上年度月均工资, 本月工资）
  contributions.csv 缴费明细（员工编码, 姓名, 险种, 缴费基数, 单位费率, 个人费率, 单位缴费, 个人缴费）
  rate_rules.csv    费率白名单（险种, 单位费率, 个人费率）
  params.csv        参数（社平工资、最低工资、社保基数下限/上限、公积金基数下限/上限）；社保基数下限/上限只用于比对，
                    核定一律用 社平×SOC_FLOOR_RATIO(60%) / 社平×SOC_CAP_RATIO(300%) 推导值；社平工资取调用方参数
                    --avg-wage，未给出时取 params.csv（未独立核对，结果 param_source 披露）

判据：
  ① 基数核定    社保基数 = clamp(核定工资, 社平×60%, 社平×300%)（params.csv 上下限与推导值不符 → param_issues，
                「参数·…」计入 issue_types，all_ok=False）；公积金基数 = clamp(核定工资, 最低工资, 公积金上限)
                核定工资：在职取上年度月均工资，新入职取本月工资
  ② 基数区间    下限 ≤ 基数 ≤ 上限（社保、公积金各自区间）
  ③ 同员工社保基数一致  养老/医疗/失业/工伤 四险基数相同
  ④ 费率白名单  单位/个人费率 == 规则表
  ⑤ 缴费恒等式  单位缴费 == 基数 × 单位费率；个人缴费 == 基数 × 个人费率
  ⑥ 完整性      每名在册员工每个险种齐全；缴费记录的员工必须在花名册
守恒：Σ单位缴费、Σ个人缴费；实缴 vs 应缴（按核定基数×规则费率）差异，分单位/个人。
可回溯：输入 SHA-256 指纹 + 时间戳；逐条核定基数、应缴、实缴、差异与判据结论。
"""
import sys, os, csv, json, hashlib, math
from collections import defaultdict, Counter
from datetime import datetime

TOL = 1.0
SOC_FLOOR_RATIO = 0.6   # 社保基数下限 = 社平工资 × 60%
SOC_CAP_RATIO = 3.0     # 社保基数上限 = 社平工资 × 300%
SOC = ["养老保险", "医疗保险", "失业保险", "工伤保险"]


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "employees.csv": ("上年度月均工资", "本月工资"),
    "contributions.csv": ("缴费基数", "单位费率", "个人费率", "单位缴费", "个人缴费"),
    "rate_rules.csv": ("单位费率", "个人费率"),
    "params.csv": ("值",),
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


def check(data_dir, avg_wage=None):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    emps = {e["员工编码"]: e for e in load(P("employees.csv"))}
    cons = load(P("contributions.csv"))
    rules = {r["险种"]: (num(r["单位费率"]), num(r["个人费率"])) for r in load(P("rate_rules.csv"))}
    pm = {r["参数"]: num(r["值"]) for r in load(P("params.csv"))}
    s_floor, s_cap = pm.get("社保基数下限"), pm.get("社保基数上限")
    h_floor, h_cap = pm.get("公积金基数下限"), pm.get("公积金基数上限")
    lost = [k for k in ("社保基数下限", "社保基数上限", "公积金基数下限", "公积金基数上限") if pm.get(k) is None]
    if lost:   # 基数上下限缺失或无法解析：无法核定基数，直接报错，不按 0 或默认值继续
        raise ValueError(f"params.csv 缺少或无法解析基数上下限 {lost}；详见 data_issues（{len(DQ)} 处）")
    # 基数上下限必须大于 0 且下限不高于上限：下限为 0 会让托底失效、上限为 0 会把基数压成 0，都不能按核定正确继续
    bad_range = [k for k in ("社保基数下限", "社保基数上限", "公积金基数下限", "公积金基数上限") if pm[k] <= 0]
    bad_range += [f"{a}>{b}" for a, b in (("社保基数下限", "社保基数上限"), ("公积金基数下限", "公积金基数上限")) if pm[a] > pm[b]]
    if bad_range:
        raise ValueError(f"params.csv 基数上下限取值无效 {bad_range}（须大于 0 且下限不高于上限）")
    # 社保基数上下限按社平工资推导，params.csv 中的上下限只比对
    try:
        sp = float(avg_wage) if avg_wage is not None else pm.get("社平工资")
    except (TypeError, ValueError):
        sp = None
    if sp is None or not math.isfinite(sp) or sp <= 0:
        src = "调用方参数 avg_wage" if avg_wage is not None else "params.csv 社平工资"
        raise ValueError(f"社平工资缺失或取值无效（{src}={avg_wage if avg_wage is not None else pm.get('社平工资')!r}，须为大于 0 的有限数）")
    param_issues = []
    if avg_wage is not None and pm.get("社平工资") is not None and abs(pm["社平工资"] - sp) > TOL:
        param_issues.append(f"社平工资与核对口径不一致(数据目录{pm['社平工资']:,.2f}/采用{sp:,.2f})")
    d_floor, d_cap = round(sp * SOC_FLOOR_RATIO, 2), round(sp * SOC_CAP_RATIO, 2)
    if abs(s_floor - d_floor) > TOL:
        param_issues.append(f"社保基数下限≠社平工资×60%(数据目录{s_floor:,.2f}/应{d_floor:,.2f})")
    if abs(s_cap - d_cap) > TOL:
        param_issues.append(f"社保基数上限≠社平工资×300%(数据目录{s_cap:,.2f}/应{d_cap:,.2f})")
    s_floor, s_cap = d_floor, d_cap

    def wage(e):
        return num(e["本月工资"]) if e.get("用工类型") == "新入职" else num(e["上年度月均工资"])

    def correct_base(e, ins):
        w = wage(e)
        return round(min(max(w, h_floor), h_cap), 2) if ins == "住房公积金" else round(min(max(w, s_floor), s_cap), 2)

    # 同员工社保基数一致性
    soc_bases = defaultdict(set)
    for r in cons:
        if r["险种"] in SOC:
            soc_bases[r["员工编码"]].add(round(num(r["缴费基数"]), 2))
    results = []
    for r in cons:
        code, ins = r["员工编码"], r["险种"]
        e = emps.get(code)
        base = num(r["缴费基数"])
        er, ir = num(r["单位费率"]), num(r["个人费率"])
        emp_paid, ind_paid = num(r["单位缴费"]), num(r["个人缴费"])
        issues, st = [], "pass"

        def bad(m):
            nonlocal st
            issues.append(m)
            st = "fail"

        def rev(m):
            nonlocal st
            issues.append(m)
            if st == "pass":
                st = "review"

        if not e:
            bad(f"员工不在花名册({code})")
            cb, rr = base, (er, ir)
        else:
            cb = correct_base(e, ins)
            rr = rules.get(ins, (er, ir))
            lo, hi = (h_floor, h_cap) if ins == "住房公积金" else (s_floor, s_cap)
            if base < lo - TOL:
                bad(f"基数低于下限({base:,.0f}<{lo:,.0f})")
            elif base > hi + TOL:
                bad(f"基数高于上限({base:,.0f}>{hi:,.0f})")
            elif abs(base - cb) > TOL:
                bad(f"基数≠核定值(实{base:,.0f}/应{cb:,.0f})")
            if ins in SOC and len(soc_bases[code]) > 1:
                rev("同员工社保各险基数不一致")
            if abs(er - rr[0]) > 1e-6:
                bad(f"单位费率错误(实{er:.2%}/应{rr[0]:.2%})")
            if abs(ir - rr[1]) > 1e-6:
                bad(f"个人费率错误(实{ir:.2%}/应{rr[1]:.2%})")
        if abs(emp_paid - base * er) > max(TOL, base * 0.001):
            bad(f"单位缴费≠基数×费率(实{emp_paid:,.2f}/应{base * er:,.2f})")
        if abs(ind_paid - base * ir) > max(TOL, base * 0.001):
            bad(f"个人缴费≠基数×费率(实{ind_paid:,.2f}/应{base * ir:,.2f})")
        due_er, due_ir = (rules.get(ins, (er, ir)))
        results.append({"员工编码": code, "姓名": r.get("姓名", e["姓名"] if e else ""), "险种": ins,
                        "核定工资": round(wage(e), 2) if e else None, "缴费基数": base, "核定基数": cb,
                        "单位缴费": emp_paid, "个人缴费": ind_paid,
                        "应缴单位": round(cb * due_er, 2) if e else emp_paid, "应缴个人": round(cb * due_ir, 2) if e else ind_paid,
                        "status": st, "issues": issues or ["核定正确"]})
    # 完整性：在册员工缺险种
    have = defaultdict(set)
    for r in cons:
        have[r["员工编码"]].add(r["险种"])
    need = set(rules) or set(SOC + ["住房公积金"])
    missing = []
    for code, e in emps.items():
        miss = need - have.get(code, set())
        for ins in sorted(miss):
            missing.append({"员工编码": code, "姓名": e["姓名"], "险种": ins})
    cnt = Counter(r["status"] for r in results)
    assert sum(cnt.values()) == len(results), "笔数守恒失败"
    emp_paid_tot = round(sum(r["单位缴费"] for r in results), 2)
    ind_paid_tot = round(sum(r["个人缴费"] for r in results), 2)
    emp_due_tot = round(sum(r["应缴单位"] for r in results), 2)
    ind_due_tot = round(sum(r["应缴个人"] for r in results), 2)
    issue_types = Counter()
    for r in results:
        for m in (r["issues"] if r["status"] != "pass" else []):
            issue_types[m.split("(")[0]] += 1
    if missing:
        issue_types["漏缴险种"] = len(missing)
    for p in param_issues:
        issue_types["参数·" + p.split("(")[0]] += 1
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且没有任何问题标签
    all_ok = not DQ and not issue_types
    return {
        "summary": dict(cnt), "total": len(results), "all_ok": all_ok, "data_issues": list(DQ),
        "pass_rate": round(cnt.get("pass", 0) / len(results) * 100, 1),
        "issue_types": dict(issue_types), "missing": missing,
        "params": {"社平工资": sp, "社保下限": s_floor, "社保上限": s_cap, "公积金下限": h_floor, "公积金上限": h_cap},
        "param_issues": param_issues,
        "param_source": "调用方参数" if avg_wage is not None else "数据目录 params.csv（未独立核对）",
        "conservation": {"实缴单位": emp_paid_tot, "实缴个人": ind_paid_tot, "实缴合计": round(emp_paid_tot + ind_paid_tot, 2),
                         "应缴单位": emp_due_tot, "应缴个人": ind_due_tot, "应缴合计": round(emp_due_tot + ind_due_tot, 2),
                         "单位差异": round(emp_paid_tot - emp_due_tot, 2), "个人差异": round(ind_paid_tot - ind_due_tot, 2),
                         "合计差异": round(emp_paid_tot + ind_paid_tot - emp_due_tot - ind_due_tot, 2),
                         "员工数": len(emps), "缴费记录数": len(cons), "漏缴险种数": len(missing)},
        "inputs": [x for x in (fp(P(f)) for f in ["employees.csv", "contributions.csv", "rate_rules.csv", "params.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv, value_opts=("--avg-wage",))
        avg_wage = opts.get("--avg-wage")
        if avg_wage is not None:
            try:
                avg_wage = float(avg_wage)
            except ValueError:
                avg_wage = float("nan")
            if not (math.isfinite(avg_wage) and avg_wage > 0):
                print(f"参数无效：--avg-wage {opts['--avg-wage']!r} 须为大于 0 的有限数", file=sys.stderr)
                sys.exit(2)
        out = check(argv[1], avg_wage)
        dest = argv[2] if len(argv) > 2 else "analysis.json"
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json",
                      {"avg_wage": out["params"]["社平工资"], "param_source": out["param_source"]}, out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
