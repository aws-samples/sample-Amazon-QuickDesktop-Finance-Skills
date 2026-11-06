"""合并抵消检查 v2 · 确定性计算脚本
用法: python consolidation_check_v2.py <数据目录> [out.json]
数据目录需包含：
  entity_statements.csv   个别报表（主体, 科目编码, 科目名称, 类别, 余额；余额按科目自然方向记正数）
  elimination_entries.csv 抵消分录（抵消分录号, 说明, 抵消类型, 关联主体, 科目编码, 科目名称, 借方, 贷方）
  intercompany_ledger.csv 集团内往来台账（与 #5 关联方往来对账同一口径）
  internal_inventory.csv  内部销售期末留存（交易号, 卖方, 买方, 交易金额, 期末留存比例, 卖方毛利率）
  ownership_internal.csv  集团股权台账（主体, 持股比例）
  ownership_registry.csv  工商股权登记（主体, 持股比例）——外部锚点

判据：
  ① 恒等式   各主体个别报表试算平衡；每笔抵消分录 Σ借 = Σ贷
  ② 白名单   四类抵消齐全；交叉白名单：抵消类型 × 允许科目
  ③ 交叉一致 抵消金额与来源重算一致（往来/收入成本来自往来台账，未实现利润来自期末留存，
             长投权益来自子公司报表与工商持股比例，含少数股东权益）
  ④ 合并范围 集团股权台账 ↔ 工商登记（比例不符、控股判定冲突）；报表主体须与控股子公司清单一致
  ⑤ 数据质量 任一输入的数值无法解析或留空 → 该行剔除并登记 data_issues，all_ok=False；
             往来台账 / 期末留存的行被剔除时，对应抵消分录判 fail（来源数值无法解析，应抵消额无法重算），
             不会因为少算了这一行而把抵消判为相符
守恒：
  · 合并数 = 个别加总 + 抵消净额（逐科目）
  · 合并试算平衡差 = Σ个别报表不平额 + Σ抵消分录不平额（差异归因）
  · 抵消分录行数全部归集
可回溯：输入文件 SHA-256 指纹 + 时间戳；逐分录、逐来源项的预期值与实际值。
"""
import sys, os, csv, json, hashlib, math
from collections import defaultdict, Counter
from datetime import datetime

TOL = 0.5
CTRL = 0.5
PARENT_DEFAULT = "示例集团母公司"
TYPES = {
    "往来抵消": {"1122", "1221", "2202", "2241"},
    "收入成本抵消": {"6001", "6401", "6602"},
    "未实现利润抵消": {"6401", "1405"},
    "长投权益抵消": {"4001", "4002", "4104", "1511", "4190"},
}
DEBIT_NATURE = {"资产", "成本费用"}
EXTRA_NATURE = {"4190": ("少数股东权益", "权益")}   # 合并层科目，个别报表中没有
PARTNER = {"2202": "1122", "1122": "2202", "2241": "1221", "1221": "2241", "4104": "1511", "4190": "1511",
           "6401": "6001", "6602": "6001", "1405": "6401"}


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "entity_statements.csv": ("余额",),
    "elimination_entries.csv": ("借方", "贷方"),
    "intercompany_ledger.csv": ("金额",),
    "internal_inventory.csv": ("交易金额", "期末留存比例", "卖方毛利率"),
    "ownership_internal.csv": ("持股比例",),
    "ownership_registry.csv": ("持股比例",),
}
BLANK_OK = set()
DQ = []  # 本次运行的数据质量问题；入口函数开头清空
QUAR = []  # 被剔除的原始行（文件名, 行号, 行），用于把台账问题归到对应抵消分录


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


def check(data_dir, parent=PARENT_DEFAULT):
    DQ.clear()
    QUAR.clear()
    P = lambda f: os.path.join(data_dir, f)
    stm = load(P("entity_statements.csv"))
    el = load(P("elimination_entries.csv"))
    led = load(P("intercompany_ledger.csv"))
    inv = load(P("internal_inventory.csv"))
    own_i = {r["主体"]: num(r["持股比例"]) for r in load(P("ownership_internal.csv"))}
    own_r = {r["主体"]: num(r["持股比例"]) for r in load(P("ownership_registry.csv"))}
    nature = dict(EXTRA_NATURE)
    bal = defaultdict(lambda: defaultdict(float))
    for r in stm:
        nature[r["科目编码"]] = (r["科目名称"], r["类别"])
        bal[r["主体"]][r["科目编码"]] += num(r["余额"])
    for e in el:
        e["借方"], e["贷方"] = num(e["借方"]), num(e["贷方"])
    sgn = lambda code: 1 if nature.get(code, ("", "资产"))[1] in DEBIT_NATURE else -1

    # ① 个别报表试算平衡
    ent_tb = {}
    for ent, accts in bal.items():
        d = sum(v for a, v in accts.items() if sgn(a) > 0)
        c = sum(v for a, v in accts.items() if sgn(a) < 0)
        ent_tb[ent] = round(d - c, 2)
    ent_unbal = {k: v for k, v in ent_tb.items() if abs(v) > TOL}

    # ① 抵消分录借贷平衡 + ② 交叉白名单
    by_no = defaultdict(list)
    for e in el:
        by_no[e["抵消分录号"]].append(e)
    entries = []
    for no, ls in by_no.items():
        dr = round(sum(x["借方"] for x in ls), 2)
        cr = round(sum(x["贷方"] for x in ls), 2)
        typ = ls[0]["抵消类型"]
        issues = []
        if abs(dr - cr) > TOL:
            issues.append(f"借贷不平(差{dr - cr:+,.2f})")
        if typ not in TYPES:
            issues.append(f"抵消类型不在白名单({typ})")
        else:
            bad = sorted({x["科目编码"] for x in ls} - TYPES[typ])
            if bad:
                issues.append(f"科目与抵消类型不符({','.join(x + nature.get(x, ('', ''))[0] for x in bad)})")
        entries.append({"抵消分录号": no, "抵消类型": typ, "关联主体": ls[0]["关联主体"], "行数": len(ls),
                        "借方": dr, "贷方": cr, "差额": round(dr - cr, 2), "issues": issues})
    present = {e["抵消类型"] for e in entries}
    missing_types = sorted(set(TYPES) - present)

    # ④ 合并范围
    subs_reg = {k for k, v in own_r.items() if v > CTRL}
    in_stm = set(bal) - {parent}
    scope = []
    for k in sorted(set(own_i) | set(own_r)):
        a, b = own_i.get(k), own_r.get(k)
        if a is not None and b is not None and abs(a - b) > 0.001:
            scope.append({"主体": k, "问题": "持股比例不符", "集团台账": a, "工商登记": b})
    for k in sorted(in_stm - subs_reg):
        scope.append({"主体": k, "问题": "不该合而合", "集团台账": own_i.get(k), "工商登记": own_r.get(k)})
    for k in sorted(subs_reg - in_stm):
        scope.append({"主体": k, "问题": "该合未合", "集团台账": own_i.get(k), "工商登记": own_r.get(k)})

    # ③ 来源重算：预期抵消额
    exp = defaultdict(float)
    for r in led:
        amt = num(r["金额"])
        if amt <= 0:
            continue
        pair = f"{r['记账实体']}↔{r['对手方']}"
        t = r["交易类型"]
        if t in ("内部销售", "内部服务", "租赁"):
            exp[("往来抵消", pair, "2202", "借")] += amt
            exp[("往来抵消", pair, "1122", "贷")] += amt
            exp[("收入成本抵消", pair, "6001", "借")] += amt
            exp[("收入成本抵消", pair, "6401" if t == "内部销售" else "6602", "贷")] += amt
        else:
            exp[("往来抵消", pair, "2241", "借")] += amt
            exp[("往来抵消", pair, "1221", "贷")] += amt
    for x in inv:
        u = num(x["交易金额"]) * num(x["期末留存比例"]) * num(x["卖方毛利率"])
        if u > 0:
            pair = f"{x['卖方']}↔{x['买方']}"
            exp[("未实现利润抵消", pair, "6401", "借")] += u
            exp[("未实现利润抵消", pair, "1405", "贷")] += u
    for s in sorted(subs_reg & set(bal)):
        o = own_r[s]
        c, p, re_ = bal[s]["4001"], bal[s]["4002"], bal[s]["4104"]
        pair = f"{parent}↔{s}"
        exp[("长投权益抵消", pair, "4001", "借")] += c
        exp[("长投权益抵消", pair, "4002", "借")] += p
        exp[("长投权益抵消", pair, "1511", "贷")] += o * (c + p)
        if o < 1:
            exp[("长投权益抵消", pair, "4104", "借")] += (1 - o) * re_
            exp[("长投权益抵消", pair, "4190", "贷")] += (1 - o) * (c + p + re_)
    act = defaultdict(float)
    for e in el:
        if e["借方"]:
            act[(e["抵消类型"], e["关联主体"], e["科目编码"], "借")] += e["借方"]
        if e["贷方"]:
            act[(e["抵消类型"], e["关联主体"], e["科目编码"], "贷")] += e["贷方"]
    src = []
    for k in sorted(set(exp) | set(act)):
        ev, av = round(exp.get(k, 0.0), 2), round(act.get(k, 0.0), 2)
        if abs(ev - av) > TOL:
            kind = "漏做抵消" if av == 0 else ("来源中无此项" if ev == 0 else "金额与来源不符")
            src.append({"抵消类型": k[0], "关联主体": k[1], "科目": k[2] + nature.get(k[2], ("", ""))[0],
                        "方向": k[3], "预期": ev, "实际": av, "差异": round(av - ev, 2), "问题": kind})
    # 数值无法解析而被剔除的来源行：对应的应抵消额无法独立重算，不能因少了这一行就判抵消相符。
    # 台账行金额的正负未知，按该行为应收方（记账实体↔对手方）挂到相关分录上，分录判 fail。
    unver = defaultdict(list)   # (抵消类型, 关联主体) -> [(相关科目, 说明)]
    quar_el = defaultdict(list)  # 抵消分录号 -> 被剔除的行号
    for fn, i, r in QUAR:
        if fn == "intercompany_ledger.csv":
            pair, t, note = f"{r['记账实体']}↔{r['对手方']}", r["交易类型"], f"往来台账第{i}行{r.get('交易号', '')}"
            if t in ("内部销售", "内部服务", "租赁"):
                unver[("往来抵消", pair)].append(({"2202", "1122"}, note))
                unver[("收入成本抵消", pair)].append(({"6001", "6401" if t == "内部销售" else "6602"}, note))
            else:
                unver[("往来抵消", pair)].append(({"2241", "1221"}, note))
        elif fn == "internal_inventory.csv":
            unver[("未实现利润抵消", f"{r['卖方']}↔{r['买方']}")].append(({"6401", "1405"}, f"期末留存第{i}行{r.get('交易号', '')}"))
        elif fn == "elimination_entries.csv":
            quar_el[r.get("抵消分录号", "")].append(i)
    # 把来源问题挂到对应分录上：同类型、同主体对，且科目（或其配对科目）出现在该分录中
    codes_of = {e["抵消分录号"]: {x["科目编码"] for x in by_no[e["抵消分录号"]]} for e in entries}
    attached = set()
    for e in entries:
        cs = codes_of[e["抵消分录号"]]
        hits = []
        for i, s_ in enumerate(src):
            c = s_["科目"][:4]
            if s_["抵消类型"] == e["抵消类型"] and s_["关联主体"] == e["关联主体"] and (c in cs or PARTNER.get(c) in cs):
                hits.append(s_); attached.add(i)
        if hits:
            e["issues"].append("与来源重算不符(" + "；".join(f"{h['方向']}{h['科目']} 预期{h['预期']:,.0f} 实际{h['实际']:,.0f}" for h in hits[:2]) + ")")
        if any(x["主体"] in e["关联主体"] for x in scope if x["问题"] == "不该合而合"):
            e["issues"].append("对应主体不应纳入合并")
        notes = [n for codes, n in unver.get((e["抵消类型"], e["关联主体"]), []) if codes & cs]
        if notes:
            e["issues"].append("来源数值无法解析(" + "；".join(notes) + "，应抵消额无法重算)")
        if e["抵消分录号"] in quar_el:
            e["issues"].append(f"数值无法解析(本分录第{','.join(map(str, quar_el[e['抵消分录号']]))}行已剔除)")
        e["status"] = "fail" if e["issues"] else "pass"
        if not e["issues"]:
            e["issues"] = ["合规"]
    # 未挂到任何分录上的来源项 = 整组漏做抵消
    missed_pairs = sorted({(s_["抵消类型"], s_["关联主体"]) for i, s_ in enumerate(src) if i not in attached})

    # 守恒
    accts = sorted({a for v in bal.values() for a in v} | {e["科目编码"] for e in el})
    sheet = []
    for a in accts:
        tot = round(sum(bal[x].get(a, 0.0) for x in bal), 2)
        net = round(sum((e["借方"] - e["贷方"]) * sgn(a) for e in el if e["科目编码"] == a), 2)
        sheet.append({"科目编码": a, "科目名称": nature.get(a, (a, ""))[0], "类别": nature.get(a, ("", ""))[1],
                      "个别加总": tot, "抵消净额": net, "合并数": round(tot + net, 2)})
    cons_d = sum(r["合并数"] for r in sheet if sgn(r["科目编码"]) > 0)
    cons_c = sum(r["合并数"] for r in sheet if sgn(r["科目编码"]) < 0)
    cons_gap = round(cons_d - cons_c, 2)
    attrib = round(sum(ent_tb.values()) + sum(e["差额"] for e in entries), 2)
    assert abs(cons_gap - attrib) < 1.0, "合并试算差异归因失败"
    assert sum(e["行数"] for e in entries) == len(el), "抵消行数守恒失败"
    cnt = Counter(e["status"] for e in entries)
    assert sum(cnt.values()) == len(entries)

    issue_types = Counter()
    for e in entries:
        for i in e["issues"]:
            issue_types[i.split("(")[0]] += 1
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    all_ok = not (ent_unbal or missing_types or scope or src or cnt.get("fail") or DQ)
    return {
        "data_issues": list(DQ),
        "summary": dict(cnt), "total_entries": len(entries), "total_lines": len(el),
        "pass_rate": round(cnt.get("pass", 0) / len(entries) * 100, 1),
        "entity_tb": ent_tb, "entity_unbalanced": ent_unbal,
        "types_present": sorted(present), "types_missing": missing_types,
        "elim_by_type": {t: round(sum(e["借方"] for e in entries if e["抵消类型"] == t), 2) for t in sorted(present)},
        "scope": scope, "source_mismatch": src, "missed_pairs": missed_pairs,
        "issue_types": dict(issue_types),
        "conservation": {"合并试算差": cons_gap, "个别报表不平合计": round(sum(ent_tb.values()), 2),
                         "抵消分录不平合计": round(sum(e["差额"] for e in entries), 2),
                         "抵消分录数": len(entries), "抵消行数": len(el)},
        "consolidated_sheet": sheet, "all_ok": all_ok,
        "inputs": [x for x in (fp(P(f)) for f in ["entity_statements.csv", "elimination_entries.csv", "intercompany_ledger.csv",
                                                     "internal_inventory.csv", "ownership_internal.csv", "ownership_registry.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
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
