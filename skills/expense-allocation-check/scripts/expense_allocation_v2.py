"""期间费用分摊复核 v2 · 确定性计算脚本
用法: python expense_allocation_v2.py <数据目录> [截止期间 YYYY-MM] [out.json]
数据目录需包含：
  prepaid_expenses.csv 待摊费用台账（费用单号, 费用类别, 摘要, 部门, 原值, 分摊月数, 起始月, 月摊额, 已摊期数, 累计摊销, 账面余额）
  gl_amortization.csv  本期总账摊销入账（凭证号, 期间, 费用单号, 部门, 借方科目, 科目名称, 摊销金额）
  category_rules.csv   费用类别期限白名单（费用类别, 最长分摊月数）            —— 可选
  dept_accounts.csv    部门 × 费用科目交叉白名单（部门, 费用科目, 科目名称）   —— 可选

逐单判据（台账）：
  ① 分摊守恒   月摊额 × 分摊月数 ≈ 原值（允许尾月调整，容差 = 分摊月数 × 0.01）
  ② 期间推算   已摊期数由「起始月 → 截止期间」独立推算，不采信台账自报值
  ③ 累计一致   累计摊销 == 按推算期数计算的应累计额（摊完时 = 原值）
  ④ 账面恒等式 账面余额 == 原值 − 累计摊销；账面 ≥ 0；累计 ≤ 原值（超摊）
  ⑤ 期限白名单 分摊月数 ≤ 费用类别的最长分摊月数
逐单判据（本期入账，交叉一致）：
  ⑥ 本期应摊额 == 总账本期入账额；借方科目 ∈ 部门允许科目
  ⑦ 范围完备：台账应摊而总账未入账 / 总账入账而台账无此单 / 台账已摊完而总账仍入账
守恒：
  · 原值合计 == 累计摊销合计 + 账面余额合计
  · 总账入账合计 − 台账本期应摊合计 == Σ 逐单入账差异（差异归因）
可回溯：输入文件 SHA-256 指纹 + 行数 + 时间戳；逐单推算期数、应累计、应摊、实际入账。
"""
import sys, os, csv, json, hashlib, math
from collections import defaultdict, Counter
from datetime import datetime

END_DEFAULT = "2026-09"
TOL = 0.5


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "prepaid_expenses.csv": ("原值", "分摊月数", "月摊额", "累计摊销", "账面余额", "已摊期数"),
    "gl_amortization.csv": ("摊销金额",),
    "category_rules.csv": ("最长分摊月数",),
}
BLANK_OK = {"已摊期数"}
DQ = []  # 本次运行的数据质量问题；入口函数开头清空


def load(p):
    if not p or not os.path.exists(p):
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


def idx(ym):
    y, m = map(int, str(ym)[:7].split("-"))
    return y * 12 + m - 1


def check(data_dir, end=END_DEFAULT):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    items = load(P("prepaid_expenses.csv"))
    gl = load(P("gl_amortization.csv")) or []
    rules = {r["费用类别"]: int(num(r["最长分摊月数"])) for r in (load(P("category_rules.csv")) or [])}
    dacc = defaultdict(set)
    for r in (load(P("dept_accounts.csv")) or []):
        dacc[r["部门"]].add(r["费用科目"])
    gl_by = defaultdict(list)
    for g in gl:
        g["摊销金额"] = num(g["摊销金额"])
        gl_by[g["费用单号"]].append(g)
    E = idx(end)
    results = []
    seen = set()
    for it in items:
        no = it["费用单号"]
        seen.add(no)
        tot, n, mon = num(it["原值"]), int(num(it["分摊月数"])), num(it["月摊额"])
        acc, book, rep_k = num(it["累计摊销"]), num(it["账面余额"]), num(it.get("已摊期数", 0))
        k_raw = E - idx(it["起始月"]) + 1
        k = max(0, min(k_raw, n))
        tail = round(tot - mon * (n - 1), 2)
        exp_acc = round(tot if k == n else mon * k, 2)
        cur = 0.0 if (k_raw < 1 or k_raw > n) else (tail if k_raw == n else mon)
        posted = round(sum(g["摊销金额"] for g in gl_by.get(no, [])), 2)
        issues, status = [], "pass"

        def fail(m):
            nonlocal status
            issues.append(m)
            status = "fail"

        if abs(mon * n - tot) > max(TOL, n * 0.01):
            fail(f"分摊不守恒(月摊×{n}={mon * n:,.2f}≠原值{tot:,.2f})")
        if rep_k and int(rep_k) != k:
            fail(f"已摊期数与起始月不符(台账{int(rep_k)}期/推算{k}期)")
        if acc > tot + TOL:
            fail(f"超摊(累计{acc:,.2f}>原值)")
        elif abs(acc - exp_acc) > TOL:
            fail(f"累计摊销与期间不符(应{exp_acc:,.2f}/实{acc:,.2f})")
        if abs(book - (tot - acc)) > TOL:
            fail(f"账面余额≠原值−累计(差{book - (tot - acc):+,.2f})")
        if book < -TOL:
            fail("账面余额为负")
        if rules and it["费用类别"] in rules and n > rules[it["费用类别"]]:
            fail(f"分摊期限超上限({it['费用类别']}{n}月>{rules[it['费用类别']]}月)")
        if gl:
            if cur > 0 and posted == 0:
                fail(f"本期漏入账(应摊{cur:,.2f})")
            elif cur == 0 and posted > 0:
                fail(f"不应摊销却入账({posted:,.2f})")
            elif abs(posted - cur) > TOL:
                fail(f"本期入账金额不符(应{cur:,.2f}/实{posted:,.2f})")
            if dacc:
                bad = sorted({g["借方科目"] for g in gl_by.get(no, []) if g["借方科目"] not in dacc.get(it["部门"], set())})
                if bad:
                    fail(f"入账科目与部门不符({it['部门']}:{','.join(bad)})")
        if not issues:
            issues = ["分摊正确"]
        results.append({"费用单号": no, "费用类别": it["费用类别"], "部门": it["部门"], "原值": tot, "分摊月数": n,
                        "起始月": it["起始月"], "月摊额": mon, "推算已摊期数": k, "台账已摊期数": int(rep_k),
                        "应累计": exp_acc, "累计摊销": acc, "账面余额": book, "本期应摊": round(cur, 2),
                        "本期入账": posted, "入账差异": round(posted - cur, 2), "status": status, "issues": issues})
    orphan = []
    for no, gs in gl_by.items():
        if no not in seen:
            amt = round(sum(g["摊销金额"] for g in gs), 2)
            orphan.append({"费用单号": no, "凭证号": ",".join(g["凭证号"] for g in gs), "部门": gs[0]["部门"], "摊销金额": amt})
            results.append({"费用单号": no, "费用类别": "—", "部门": gs[0]["部门"], "原值": 0.0, "分摊月数": 0, "起始月": "—",
                            "月摊额": 0.0, "推算已摊期数": 0, "台账已摊期数": 0, "应累计": 0.0, "累计摊销": 0.0, "账面余额": 0.0,
                            "本期应摊": 0.0, "本期入账": amt, "入账差异": amt, "status": "fail", "issues": ["台账外摊销(总账有、台账无)"]})

    cnt = Counter(r["status"] for r in results)
    assert sum(cnt.values()) == len(results), "单数守恒失败"
    reg = [r for r in results if r["起始月"] != "—"]
    t_tot = round(sum(r["原值"] for r in reg), 2)
    t_acc = round(sum(r["累计摊销"] for r in reg), 2)
    t_book = round(sum(r["账面余额"] for r in reg), 2)
    exp_cur = round(sum(r["本期应摊"] for r in reg), 2)
    gl_tot = round(sum(g["摊销金额"] for g in gl), 2)
    diff_sum = round(sum(r["入账差异"] for r in results), 2)
    assert abs((gl_tot - exp_cur) - diff_sum) < 0.05, "入账差异归因失败"
    issue_types = Counter(i.split("(")[0] for r in results for i in r["issues"])
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「分摊正确」
    all_ok = not DQ and not (set(issue_types) - {"分摊正确"})
    return {
        "summary": dict(cnt), "total": len(results), "pass_rate": round(cnt.get("pass", 0) / len(results) * 100, 1),
        "all_ok": all_ok, "data_issues": list(DQ),
        "issue_types": dict(issue_types), "period_end": end,
        "conservation": {"原值合计": t_tot, "累计摊销合计": t_acc, "账面余额合计": t_book,
                         "原值−累计−账面": round(t_tot - t_acc - t_book, 2),
                         "台账本期应摊": exp_cur, "总账本期入账": gl_tot, "入账差": round(gl_tot - exp_cur, 2),
                         "逐单差异合计": diff_sum},
        "orphan_gl": orphan,
        "inputs": [x for x in (fp(P(f)) for f in ["prepaid_expenses.csv", "gl_amortization.csv", "category_rules.csv", "dept_accounts.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv)
        end = argv[2] if len(argv) > 2 and not argv[2].endswith(".json") else END_DEFAULT
        out = check(argv[1], end)
        dest = argv[-1] if argv[-1].endswith(".json") else "analysis.json"
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", {"end": end}, out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
