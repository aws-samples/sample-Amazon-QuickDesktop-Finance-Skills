"""增值税进销项匹配 v2 · 确定性计算脚本
用法: python vat_matching_v2.py <数据目录> [out.json]
数据目录需包含：
  output_invoices.csv  销项发票（发票代码号码, 开票日期, 销方税号, 购方税号, 购方名称, 商品服务类别, 金额, 税率, 税额, 发票状态, 原蓝字发票）
  input_invoices.csv   进项发票（发票代码号码, 开票日期, 销方税号, 销方名称, 购方税号, 商品服务类别, 用途, 金额, 税率, 税额, 发票状态, 勾选状态, 勾选期间）
  rate_rules.csv       商品服务类别 × 适用税率（交叉白名单）
  non_deductible.csv   不得抵扣清单（类型[类别/用途], 值, 原因）
  period_info.csv      税款所属期、本公司税号、上期留抵税额、本期进项转出

逐票判据：
  ① 税额恒等式   税额 == 金额 × 税率（容差 0.06 元）
  ② 税率白名单   税率 ∈ {13%, 9%, 6%, 5%, 3%, 1%, 0}
  ③ 交叉白名单   商品服务类别 × 适用税率
  ④ 唯一性       发票代码号码唯一
  ⑤ 状态         作废发票不计税；红字发票必须对应存在的蓝字发票，且冲减不超过原票
  ⑥ 销项         销方税号 == 本公司；开票日期 ∈ 所属期
  ⑦ 进项抵扣     购方税号 == 本公司；已勾选且勾选期间 == 所属期；发票状态正常；不属于不得抵扣类别/用途
计算：应纳税额 = 销项税额 − (本期可抵扣进项 − 进项转出) − 上期留抵；为负即期末留抵。
守恒：Σ全部销项税额 == 计入 + 剔除；Σ全部进项税额 == 可抵扣 + 不可抵扣/未勾选 + 剔除；张数守恒。
可回溯：输入 SHA-256 指纹 + 所属期 + 时间戳；逐票状态与原因。
"""
import sys, os, csv, json, hashlib, re, math
from collections import defaultdict, Counter
from datetime import datetime

TOL = 0.06
LEGAL = {0.13, 0.09, 0.06, 0.05, 0.03, 0.01, 0.0}


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "output_invoices.csv": ("金额", "税率", "税额"),
    "input_invoices.csv": ("金额", "税率", "税额"),
    "rate_rules.csv": ("适用税率",),
    "period_info.csv": (("值", "项目", ("上期留抵税额", "本期进项转出")),),
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
            if isinstance(k, tuple):          # 键值表：只校验指定参数行的「值」列
                k, kc, keys = k
                if r.get(kc) not in keys:
                    continue
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


def _common(r, rules, seen, blue_by):
    iss = []
    a, rt, t = num(r["金额"]), round(num(r["税率"]), 4), num(r["税额"])
    if abs(t - a * rt) > TOL:
        iss.append(f"税额≠金额×税率(应{a * rt:,.2f}/实{t:,.2f})")
    if rt not in LEGAL:
        iss.append(f"非法税率({rt:.0%})")
    elif rules and r["商品服务类别"] in rules and abs(rules[r["商品服务类别"]] - rt) > 1e-6:
        iss.append(f"类别与税率不符({r['商品服务类别']}应{rules[r['商品服务类别']]:.0%}/实{rt:.0%})")
    if seen[r["发票代码号码"]] > 1:
        iss.append("发票重复(同一代码号码多次出现)")
    return iss


def check(data_dir):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    outs, ins = load(P("output_invoices.csv")), load(P("input_invoices.csv"))
    rules = {r["商品服务类别"]: num(r["适用税率"]) for r in load(P("rate_rules.csv"))}
    nd = load(P("non_deductible.csv"))
    nd_cat = {r["值"]: r["原因"] for r in nd if r["类型"] == "类别"}
    nd_use = {r["值"]: r["原因"] for r in nd if r["类型"] == "用途"}
    info = {r["项目"]: r["值"] for r in load(P("period_info.csv"))}
    per, me = info.get("税款所属期", "2026-09"), info.get("本公司税号", "")
    carry, transfer = num(info.get("上期留抵税额", 0)), num(info.get("本期进项转出", 0))
    so, si = Counter(r["发票代码号码"] for r in outs), Counter(r["发票代码号码"] for r in ins)
    blue = {r["发票代码号码"]: r for r in outs if num(r["金额"]) > 0}
    red_sum = defaultdict(float)
    ores, ires = [], []
    dup_seen = set()
    for r in outs:
        iss = _common(r, rules, so, blue)
        st_note = r.get("发票状态", "正常")
        if r["发票代码号码"] in dup_seen and "发票重复(同一代码号码多次出现)" in iss:
            pass
        first_dup = r["发票代码号码"] not in dup_seen
        dup_seen.add(r["发票代码号码"])
        if r["销方税号"] != me:
            iss.append("销方不是本公司")
        if not r["开票日期"].startswith(per):
            iss.append(f"销项跨期({r['开票日期']})")
        a, t = num(r["金额"]), num(r["税额"])
        if a < 0:
            b = blue.get(r.get("原蓝字发票", ""))
            if not b:
                iss.append(f"红字无对应蓝字({r.get('原蓝字发票') or '未填'})")
            elif b.get("发票状态", "正常") == "作废":
                # 已作废的蓝字不计税，红字不得冲抵；不累计冲减额
                iss.append(f"红字对应蓝字已作废({b['发票代码号码']})")
            else:
                red_sum[b["发票代码号码"]] += -a
                if red_sum[b["发票代码号码"]] > num(b["金额"]) + TOL:
                    iss.append("红字冲减超过原蓝字金额")
        if st_note == "作废":
            state = "作废(不计税)"
        elif iss:
            state = "剔除"
        else:
            state = "计入"
        # 重复发票：第一张照常计入，后续重复张剔除
        if "发票重复(同一代码号码多次出现)" in iss and first_dup and len(iss) == 1:
            state = "计入"
        ores.append({"发票代码号码": r["发票代码号码"], "开票日期": r["开票日期"], "往来方": r["购方名称"], "商品服务类别": r["商品服务类别"],
                     "金额": a, "税率": num(r["税率"]), "税额": t, "发票状态": st_note, "计税": state,
                     "status": "fail" if iss else "pass", "issues": iss or (["作废，不计税"] if st_note == "作废" else ["正确"])})
    dup_seen = set()
    for r in ins:
        iss = _common(r, rules, si, {})
        first_dup = r["发票代码号码"] not in dup_seen
        dup_seen.add(r["发票代码号码"])
        sel = r.get("勾选状态") == "已勾选"
        nd_reason = nd_cat.get(r["商品服务类别"]) or nd_use.get(r.get("用途", ""))
        if r["购方税号"] != me:
            iss.append(f"购方抬头错误({r['购方税号']})")
        if r.get("发票状态", "正常") != "正常" and sel:
            iss.append(f"{r['发票状态']}发票已勾选抵扣")
        if sel and r.get("勾选期间") != per:
            iss.append(f"勾选期间不是本期({r.get('勾选期间')})")
        if sel and nd_reason:
            iss.append(f"不得抵扣项目已勾选({nd_reason})")
        t = num(r["税额"])
        if "发票重复(同一代码号码多次出现)" in iss and first_dup and len(iss) == 1:
            iss = []
        if iss:
            state = "剔除"
        elif not sel:
            state = "不抵扣(" + (nd_reason or "未勾选") + ")"
        else:
            state = "可抵扣"
        ires.append({"发票代码号码": r["发票代码号码"], "开票日期": r["开票日期"], "往来方": r["销方名称"], "商品服务类别": r["商品服务类别"],
                     "用途": r.get("用途", ""), "金额": num(r["金额"]), "税率": num(r["税率"]), "税额": t, "勾选": r.get("勾选状态", ""),
                     "勾选期间": r.get("勾选期间", ""), "计税": state, "status": "fail" if iss else "pass", "issues": iss or ["正确"]})
    s = lambda L, cond: round(sum(x["税额"] for x in L if cond(x)), 2)
    out_all, out_in = s(ores, lambda x: True), s(ores, lambda x: x["计税"] == "计入")
    out_void, out_ex = s(ores, lambda x: x["计税"].startswith("作废")), s(ores, lambda x: x["计税"] == "剔除")
    assert abs(out_all - out_in - out_void - out_ex) < 0.05, "销项守恒失败"
    in_all, in_ok = s(ires, lambda x: True), s(ires, lambda x: x["计税"] == "可抵扣")
    in_no, in_ex = s(ires, lambda x: x["计税"].startswith("不抵扣")), s(ires, lambda x: x["计税"] == "剔除")
    assert abs(in_all - in_ok - in_no - in_ex) < 0.05, "进项守恒失败"
    assert len(ores) == len(outs) and len(ires) == len(ins), "张数守恒失败"
    payable = round(out_in - (in_ok - transfer) - carry, 2)
    raw_payable = round(sum(num(r["税额"]) for r in outs if r.get("发票状态") != "作废")
                        - sum(num(r["税额"]) for r in ins if r.get("勾选状态") == "已勾选") + transfer - carry, 2)
    rd = defaultdict(lambda: {"张数": 0, "税额": 0.0})
    for x in ores + ires:
        if x["计税"] in ("计入", "可抵扣"):
            k = ("销项 " if x in ores else "进项 ") + f"{x['税率']:.0%}"
            rd[k]["张数"] += 1
            rd[k]["税额"] = round(rd[k]["税额"] + x["税额"], 2)
    it = Counter()
    for x in ores:
        for m in (x["issues"] if x["status"] == "fail" else []):
            it["销项·" + re.sub(r"\(.*", "", m)] += 1
    for x in ires:
        for m in (x["issues"] if x["status"] == "fail" else []):
            it["进项·" + re.sub(r"\(.*", "", m)] += 1
    oc, ic = Counter(x["status"] for x in ores), Counter(x["status"] for x in ires)
    for d in DQ:
        it["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且没有任何问题标签
    all_ok = not DQ and not it
    return {"period": per, "out_summary": dict(oc), "in_summary": dict(ic), "issue_types": dict(it),
            "all_ok": all_ok, "data_issues": list(DQ),
            "pass_rate": round((oc.get("pass", 0) + ic.get("pass", 0)) / (len(ores) + len(ires)) * 100, 1),
            "rate_distribution": dict(rd),
            "conservation": {"销项税额合计": out_all, "销项计入": out_in, "销项作废": out_void, "销项剔除": out_ex,
                             "进项税额合计": in_all, "进项可抵扣": in_ok, "进项不抵扣": in_no, "进项剔除": in_ex,
                             "进项转出": transfer, "上期留抵": carry, "应纳税额": payable,
                             "期末留抵": round(-payable, 2) if payable < 0 else 0.0,
                             "未经校验直接计算的应纳税额": raw_payable, "差异": round(raw_payable - payable, 2),
                             "销项张数": len(outs), "进项张数": len(ins)},
            "inputs": [x for x in (fp(P(f)) for f in ["output_invoices.csv", "input_invoices.csv", "rate_rules.csv", "non_deductible.csv", "period_info.csv"]) if x],
            "timestamp": datetime.now().isoformat(timespec="seconds"), "outputs": ores, "inputs_inv": ires}


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
