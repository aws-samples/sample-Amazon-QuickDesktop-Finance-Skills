"""增值税申报一致性 v2 · 确定性计算脚本
用法: python vat_filing_check_v2.py <数据目录> [out.json]
数据目录需包含：
  book_summary.csv           账载汇总（项目, 账载金额）——承接 #15 增值税进销项匹配的输出
  filing_main.csv            申报表主表（栏次, 项目, 申报值）
  filing_schedule1_sales.csv 附表一 销项按税率（税率, 销售额, 销项税额）
  filing_schedule2_input.csv 附表二 进项明细（项目, 份数, 金额, 税额）

判据：
  ① 账表交叉一致  主表各栏 == 账载对应项目（销项/进项/上期留抵等）
  ② 附表恒等式    附表一每档 销项税额 == 销售额 × 税率；税率 ∈ 白名单
  ③ 附表↔主表     附表一销项税额合计 == 主表销项税额；附表二税额合计 == 主表进项税额
  ④ 主表内部恒等式 应抵扣税额合计 == 进项税额 − 进项转出 + 上期留抵
                   实际抵扣税额 == min(应抵扣税额合计, 销项税额)
                   应纳税额 == 销项税额 − 实际抵扣税额
                   期末留抵税额 == 应抵扣税额合计 − 实际抵扣税额
  ⑤ 逻辑互斥      应纳税额 > 0 与 期末留抵税额 > 0 不能同时成立
可回溯：输入 SHA-256 指纹 + 时间戳；逐栏账载/申报/重算/差异与判据结论。
"""
import sys, os, csv, json, hashlib, math
from datetime import datetime

TOL = 0.5
LEGAL = {0.13, 0.09, 0.06, 0.05, 0.03, 0.01, 0.0}
CROSS = ["按适用税率销售额", "销项税额", "进项税额", "进项转出", "上期留抵税额"]


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "book_summary.csv": ("账载金额",),
    "filing_main.csv": ("申报值",),
    "filing_schedule1_sales.csv": ("税率", "销售额", "销项税额"),
    "filing_schedule2_input.csv": ("税额",),
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


def check(data_dir):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    book = {r["项目"]: num(r["账载金额"]) for r in load(P("book_summary.csv"))}
    main = {r["栏次"]: num(r["申报值"]) for r in load(P("filing_main.csv"))}
    s1 = load(P("filing_schedule1_sales.csv"))
    s2 = load(P("filing_schedule2_input.csv"))
    checks = []

    def add(cat, name, expect, actual, note=""):
        d = round(actual - expect, 2)
        checks.append({"类别": cat, "项目": name, "基准/重算": round(expect, 2), "申报值": round(actual, 2),
                       "差异": d, "一致": abs(d) <= TOL, "说明": note})

    # ① 账表交叉
    for k in CROSS:
        if k in book or k in main:
            add("账表交叉", k, book.get(k, 0.0), main.get(k, 0.0))
    # ② 附表一恒等式 + 税率白名单
    s1_tax_sum = 0.0
    for r in s1:
        rt, amt, tax = round(num(r["税率"]), 4), num(r["销售额"]), num(r["销项税额"])
        s1_tax_sum += tax
        add("附表一恒等式", f"{rt:.0%}档 税额=销售额×税率", round(amt * rt, 2), tax)
        if rt not in LEGAL:
            checks.append({"类别": "税率白名单", "项目": f"{rt:.0%}档", "基准/重算": "合法税率", "申报值": f"{rt:.0%}",
                           "差异": "—", "一致": False, "说明": "非法/已废止税率"})
    s1_tax_sum = round(s1_tax_sum, 2)
    # ③ 附表↔主表
    add("附表↔主表", "附表一销项税额合计=主表销项税额", main.get("销项税额", 0.0), s1_tax_sum)
    s2_tax = round(sum(num(r["税额"]) for r in s2 if "转出" not in r["项目"]), 2)
    add("附表↔主表", "附表二进项税额合计=主表进项税额", main.get("进项税额", 0.0), s2_tax)
    # ④ 主表内部恒等式
    out_tax = main.get("销项税额", 0.0)
    in_tax = main.get("进项税额", 0.0)
    trans = main.get("进项转出", 0.0)
    carry = main.get("上期留抵税额", 0.0)
    ded_total = round(in_tax - trans + carry, 2)
    add("主表恒等式", "应抵扣税额合计=进项−转出+上期留抵", ded_total, main.get("应抵扣税额合计", 0.0))
    actual_ded = round(min(main.get("应抵扣税额合计", 0.0), out_tax), 2)
    add("主表恒等式", "实际抵扣税额=min(应抵扣,销项)", actual_ded, main.get("实际抵扣税额", 0.0))
    payable = round(out_tax - main.get("实际抵扣税额", 0.0), 2)
    add("主表恒等式", "应纳税额=销项−实际抵扣", payable, main.get("应纳税额", 0.0))
    end_carry = round(main.get("应抵扣税额合计", 0.0) - main.get("实际抵扣税额", 0.0), 2)
    add("主表恒等式", "期末留抵=应抵扣−实际抵扣", end_carry, main.get("期末留抵税额", 0.0))
    # ⑤ 逻辑互斥
    mutual_ok = not (main.get("应纳税额", 0.0) > TOL and main.get("期末留抵税额", 0.0) > TOL)
    checks.append({"类别": "逻辑互斥", "项目": "应纳税额与期末留抵不同为正", "基准/重算": "至多一项>0",
                   "申报值": f"应纳{main.get('应纳税额', 0.0):,.2f}/留抵{main.get('期末留抵税额', 0.0):,.2f}",
                   "差异": "—", "一致": mutual_ok, "说明": "" if mutual_ok else "两项同时为正，逻辑冲突"})
    # 正确应纳（以账载为基准重算）
    correct_ded = round(book.get("进项税额", 0.0) - book.get("进项转出", 0.0) + book.get("上期留抵税额", 0.0), 2)
    correct_actual = round(min(correct_ded, book.get("销项税额", 0.0)), 2)
    correct_payable = round(book.get("销项税额", 0.0) - correct_actual, 2)
    n_bad = sum(1 for c in checks if not c["一致"])
    by_cat = {}
    for c in checks:
        by_cat.setdefault(c["类别"], {"总数": 0, "不符": 0})
        by_cat[c["类别"]]["总数"] += 1
        by_cat[c["类别"]]["不符"] += 0 if c["一致"] else 1
    return {
        "all_ok": not DQ and n_bad == 0, "data_issues": list(DQ),   # 总判定：无数据质量问题且全部校验一致
        "checks": checks, "n_checks": len(checks), "n_inconsistent": n_bad,
        "consistent_rate": round((len(checks) - n_bad) / len(checks) * 100, 1),
        "by_category": by_cat,
        "declared_payable": round(main.get("应纳税额", 0.0), 2), "correct_payable": correct_payable,
        "payable_gap": round(main.get("应纳税额", 0.0) - correct_payable, 2),
        "total_abs_diff": round(sum(abs(c["差异"]) for c in checks if isinstance(c["差异"], (int, float))), 2),
        "inputs": [x for x in (fp(P(f)) for f in ["book_summary.csv", "filing_main.csv", "filing_schedule1_sales.csv", "filing_schedule2_input.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
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
