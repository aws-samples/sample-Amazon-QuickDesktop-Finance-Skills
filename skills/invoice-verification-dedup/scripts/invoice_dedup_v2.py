"""发票查验与重复报销识别 v2 · 确定性计算脚本
用法: python invoice_dedup_v2.py <数据目录> [out.json]
数据目录需包含：
  reimbursements.csv   本期报销（报销单号, 发票号码, 开票方, 开票方税号, 开票方行业, 费用类别, 发票金额, 报销金额, 开票日期, 报销日期, 报销人, 部门）
  invoice_history.csv  历史已报销发票池（发票号码, 开票方税号, 发票金额, 开票日期, 原报销单号, 报销期间）   —— 可选，跨期查重
  expense_policy.csv   费用标准（费用类别, 单笔上限）                                                         —— 可选
  category_rules.csv   开票方行业 × 允许费用类别（行业, 允许费用类别）                                       —— 可选

唯一性判据：
  ① 发票号归一化后唯一（去空格、全角转半角、去前导零）——同号 / 变形重复
  ② 跨期唯一：与历史已报销发票池比对
  ③ 组合指纹：开票方税号 + 发票金额 + 开票日期 相同而号码不同 → 疑似换号重开
  ④ 拆分报销：同一报销人、同一开票方、同一天 ≥ 2 张，每张都低于单笔上限、合计超过上限
  ⑤ 连号：同一报销人、同一开票方 ≥ 3 张号码连续
其他判据：
  ⑥ 报销金额 ≤ 发票金额；⑦ 开票日期 ≤ 报销日期，且开票不超过 180 天
  ⑧ 交叉白名单：开票方行业 × 费用类别；⑨ 单笔上限（超过需审批 → 待核实）
守恒：Σ报销金额 == 通过 + 待核实 + 风险 三类金额之和；笔数同理。
可回溯：输入 SHA-256 指纹 + 时间戳；逐笔归一化号码、命中的判据与关联单号。
"""
import sys, os, csv, json, hashlib, re, unicodedata, math
from collections import defaultdict, Counter
from datetime import date, datetime

MAX_AGE = 180


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "reimbursements.csv": ("发票金额", "报销金额"),
    "expense_policy.csv": ("单笔上限",),
}
BLANK_OK = set()
DQ = []  # 本次运行的数据质量问题；入口函数开头清空
QUAR = []  # 被剔除的原始行（文件名, 行号, 行），用于标出单笔上限无法校验的费用类别


def load(p):
    if not os.path.exists(p):
        return None
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
            r["_ln"] = i                      # 保留原始 CSV 行号，供后续登记数据质量问题
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


def norm(s):
    s = unicodedata.normalize("NFKC", str(s)).upper()
    s = re.sub(r"\s+", "", s)
    return re.sub(r"(?<!\d)0+(?=\d)", "", s)


def check(data_dir):
    DQ.clear()
    QUAR.clear()
    P = lambda f: os.path.join(data_dir, f)
    rows = load(P("reimbursements.csv"))
    hist = load(P("invoice_history.csv")) or []
    pol, pol_inval = {}, set()
    for r in (load(P("expense_policy.csv")) or []):
        v = num(r["单笔上限"])
        if v <= 0:      # 上限为 0 或负数：不能当作「不设上限」，登记数据质量问题，该类别逐笔标出
            DQ.append({"文件": "expense_policy.csv", "行号": r["_ln"], "字段": "单笔上限", "原值": r["单笔上限"], "问题": "数值超出范围"})
            pol_inval.add(r["费用类别"])
            continue
        pol[r["费用类别"]] = v
    pol_bad = {r.get("费用类别", "") for fn, i, r in QUAR if fn == "expense_policy.csv"}   # 上限无法解析的类别
    crule = defaultdict(set)
    for r in (load(P("category_rules.csv")) or []):
        crule[r["行业"]].add(r["允许费用类别"])
    for r in rows:
        r["_no"] = norm(r["发票号码"])
        r["发票金额"], r["报销金额"] = num(r["发票金额"]), num(r["报销金额"])
    hist_by = {norm(h["发票号码"]): h for h in hist}
    by_no, by_fp, by_split, by_seq = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for r in rows:
        by_no[r["_no"]].append(r)
        by_fp[(r["开票方税号"], round(r["发票金额"], 2), r["开票日期"])].append(r)
        by_split[(r["报销人"], r["开票方税号"], r["开票日期"])].append(r)
        by_seq[(r["报销人"], r["开票方税号"])].append(r)
    flags = defaultdict(list)
    for no, g in by_no.items():
        if len(g) > 1:
            raw = {x["发票号码"] for x in g}
            kind = "重复报销(同一发票号)" if len(raw) == 1 else "号码变形重复"
            for x in g:
                flags[x["报销单号"]].append(("risk", f"{kind}({'/'.join(y['报销单号'] for y in g if y is not x)})"))
    for r in rows:
        h = hist_by.get(r["_no"])
        if h:
            flags[r["报销单号"]].append(("risk", f"跨期重复报销(已于{h['报销期间']}在{h['原报销单号']}报销)"))
    for k, g in by_fp.items():
        if len({x["_no"] for x in g}) > 1:
            for x in g:
                flags[x["报销单号"]].append(("risk", f"疑似换号重开({'/'.join(y['报销单号'] for y in g if y is not x)})"))
    for k, g in by_split.items():
        uniq = {}
        for x in g:
            uniq.setdefault(x["_no"], x)          # 同号重复不算拆分，已由 ① 处理
        g = list(uniq.values())
        if len(g) >= 2:
            lim = pol.get(g[0]["费用类别"])
            tot = sum(x["发票金额"] for x in g)
            if lim is not None and all(x["发票金额"] < lim for x in g) and tot > lim:
                for x in g:
                    flags[x["报销单号"]].append(("risk", f"疑似拆分报销({len(g)}张合计{tot:,.2f}>上限{lim:,.0f})"))
    for k, g in by_seq.items():
        nums = sorted({(int(x["_no"]), x["报销单号"]) for x in g if x["_no"].isdigit()})
        run = [nums[0]] if nums else []
        runs = []
        for a, b in zip(nums, nums[1:]):
            if b[0] - a[0] == 1:
                run.append(b)
            else:
                runs.append(run)
                run = [b]
        runs.append(run)
        for rn in runs:
            if len(rn) >= 3:
                for _, bxn in rn:
                    flags[bxn].append(("review", f"连号发票({len(rn)}张)"))
    results = []
    for r in rows:
        issues = list(flags.get(r["报销单号"], []))
        if r["报销金额"] > r["发票金额"] + 0.01:
            issues.append(("risk", f"报销超票面(报{r['报销金额']:,.2f}>票{r['发票金额']:,.2f})"))
        di, dr = date.fromisoformat(r["开票日期"]), date.fromisoformat(r["报销日期"])
        if dr < di:
            issues.append(("risk", f"报销早于开票({r['报销日期']}<{r['开票日期']})"))
        elif (dr - di).days > MAX_AGE:
            issues.append(("review", f"超期发票(开票至报销{(dr - di).days}天)"))
        if crule and r["费用类别"] not in crule.get(r["开票方行业"], set()):
            issues.append(("review", f"类别与开票方不符({r['开票方行业']}→{r['费用类别']})"))
        lim = pol.get(r["费用类别"])
        if lim is not None and r["发票金额"] > lim:
            issues.append(("review", f"超标准(>{lim:,.0f})"))
        if r["费用类别"] in pol_bad:
            issues.append(("review", "费用标准无法解析(该类别单笔上限与拆分报销未校验，见 data_issues)"))
        if r["费用类别"] in pol_inval:
            issues.append(("review", "费用标准无效(该类别单笔上限不大于 0，单笔上限与拆分报销未校验，见 data_issues)"))
        status = "risk" if any(s == "risk" for s, _ in issues) else ("review" if issues else "pass")
        results.append({"报销单号": r["报销单号"], "发票号码": r["发票号码"], "归一化号码": r["_no"], "开票方": r["开票方"],
                        "费用类别": r["费用类别"], "发票金额": r["发票金额"], "报销金额": r["报销金额"], "开票日期": r["开票日期"],
                        "报销日期": r["报销日期"], "报销人": r["报销人"], "部门": r["部门"], "status": status,
                        "issues": [m for _, m in issues] or ["查验通过"]})
    cnt = Counter(r["status"] for r in results)
    amt = {s: round(sum(r["报销金额"] for r in results if r["status"] == s), 2) for s in ("pass", "review", "risk")}
    tot = round(sum(r["报销金额"] for r in results), 2)
    assert sum(cnt.values()) == len(rows), "笔数守恒失败"
    assert abs(sum(amt.values()) - tot) < 0.05, "金额守恒失败"
    issue_types = Counter(re.sub(r"\(.*", "", m) for r in results for m in r["issues"])
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「查验通过」
    all_ok = not DQ and not (set(issue_types) - {"查验通过"})
    emp = defaultdict(lambda: {"笔数": 0, "风险笔数": 0, "风险金额": 0.0})
    for r in results:
        e = emp[r["报销人"]]
        e["笔数"] += 1
        if r["status"] == "risk":
            e["风险笔数"] += 1
            e["风险金额"] = round(e["风险金额"] + r["报销金额"], 2)
    return {"summary": dict(cnt), "total": len(results), "pass_rate": round(cnt.get("pass", 0) / len(results) * 100, 1),
            "all_ok": all_ok, "data_issues": list(DQ),
            "issue_types": dict(issue_types), "amount_by_status": amt, "risk_amount": amt["risk"],
            "conservation": {"报销金额合计": tot, "通过": amt["pass"], "待核实": amt["review"], "风险": amt["risk"], "笔数": len(rows), "历史发票池": len(hist)},
            "employee_risk": {k: v for k, v in sorted(emp.items(), key=lambda x: -x[1]["风险金额"]) if v["风险笔数"]},
            "inputs": [x for x in (fp(P(f)) for f in ["reimbursements.csv", "invoice_history.csv", "expense_policy.csv", "category_rules.csv", "sellers.csv"]) if x],
            "timestamp": datetime.now().isoformat(timespec="seconds"), "results": results}


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
