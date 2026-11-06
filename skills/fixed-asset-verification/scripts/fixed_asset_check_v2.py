"""固定资产卡片与实物核对 v2 · 确定性计算脚本
用法: python fixed_asset_check_v2.py <数据目录> [盘点基准日 YYYY-MM-DD] [out.json]
数据目录需包含：
  asset_cards.csv    资产卡片（卡片编号, 资产名称, 类别, 原值, 残值率, 入账日期, 使用年限月, 已计提月数, 月折旧额, 累计折旧, 账面净值, 部门, 存放地点, 状态）
  physical_count.csv 实物盘点（卡片编号, 实物部门, 实物地点, 盘点状态, 盘点人）
  category_rules.csv 类别规则（类别, 最低使用年限月, 残值率）
  params.csv         参数（盘点基准日）——只用于比对，不参与计算
盘点基准日（折旧重算口径）取调用方参数，未给出时用引擎默认 BASE_DEFAULT；数据目录中的基准日与采用值不同
  → 记入 param_issues 与 issue_types「参数·盘点基准日与核对口径不一致」，all_ok=False。

判据：
  ① 账实交叉    有账无物→盘亏；有物无账→盘盈；部门/位置不符
  ② 折旧恒等式  账面净值 == 原值 − 累计折旧
  ③ 折旧重算    月折旧 == 原值×(1−残值率)/使用年限；应计提月数 == min(基准日−入账日期, 使用年限)
                应累计折旧 == 月折旧 × 应计提月数（封顶可折旧总额）
  ④ 边界        累计折旧 ≤ 原值×(1−残值率)（可折旧总额）；账面净值 ≥ 残值
  ⑤ 类别白名单  使用年限 ≥ 类别最低；残值率 == 类别规则
  ⑥ 状态        已处置资产不应出现在盘点表且净值应为 0
守恒：Σ原值、Σ累计折旧、Σ净值；账实相符率；盘盈/盘亏敞口。
可回溯：输入 SHA-256 指纹 + 基准日 + 时间戳；逐卡账实、重算与判据结论。
"""
import sys, os, csv, json, hashlib, math, re
from collections import Counter
from datetime import date, datetime

TOL = 1.0
BASE_DEFAULT = "2026-09-30"   # 盘点基准日默认值；调用方可显式传入


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "asset_cards.csv": ("原值", "累计折旧", "账面净值", "残值率", "使用年限月", "月折旧额"),
    "category_rules.csv": ("最低使用年限月", "残值率"),
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


def mob(d0, d1):
    return (d1.year - d0.year) * 12 + (d1.month - d0.month)


def check(data_dir, base=None):
    base_str = base or BASE_DEFAULT
    BASE = date.fromisoformat(base_str)   # 非法日期抛 ValueError
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    cards = load(P("asset_cards.csv"))
    phys = load(P("physical_count.csv"))
    rules = {r["类别"]: (int(num(r["最低使用年限月"])), num(r["残值率"])) for r in load(P("category_rules.csv"))}
    pm = {r["参数"]: r["值"] for r in load(P("params.csv"))}
    param_issues = []
    x = (pm.get("盘点基准日") or "").strip()
    if "盘点基准日" in pm and x != base_str:   # 数据目录的基准日只比对，不参与计算
        param_issues.append(f"盘点基准日与核对口径不一致(数据目录{x}/采用{base_str})")
    # 同一卡片编号只取首行；重复键不静默覆盖，单列 duplicate_keys 并在该卡片结果中记为异常
    card_by, phys_by = {}, {}
    for c in cards:
        card_by.setdefault(c["卡片编号"], c)
    for p in phys:
        phys_by.setdefault(p["卡片编号"], p)
    card_dup = {k: n for k, n in Counter(c["卡片编号"] for c in cards).items() if n > 1}
    phys_dup = {k: n for k, n in Counter(p["卡片编号"] for p in phys).items() if n > 1}
    dup = ([{"文件": "asset_cards.csv", "卡片编号": k, "行数": n} for k, n in sorted(card_dup.items())]
           + [{"文件": "physical_count.csv", "卡片编号": k, "行数": n} for k, n in sorted(phys_dup.items())])
    results = []
    for aid in sorted(set(card_by) | set(phys_by)):
        c, p = card_by.get(aid), phys_by.get(aid)
        issues, st = [], "matched"

        def exc(m):
            nonlocal st
            issues.append(m)
            st = "exception"

        def dif(m):
            nonlocal st
            issues.append(m)
            if st == "matched":
                st = "difference"

        orig = num(c["原值"]) if c else 0.0
        accum = num(c["累计折旧"]) if c else 0.0
        net = num(c["账面净值"]) if c else 0.0
        if aid in card_dup:
            exc(f"重复卡片({card_dup[aid]}条)")
        if aid in phys_dup:
            exc(f"重复盘点记录({phys_dup[aid]}条)")
        if c and not p:
            if c.get("状态") == "已处置":
                dif("已处置(不在盘点表，正常)")
            else:
                exc("实物盘亏(有账无物)")
        elif p and not c:
            exc("实物盘盈(有物无账)")
        else:
            res_rate = num(c["残值率"])
            life = int(num(c["使用年限月"]))
            salvage = round(orig * res_rate, 2)
            deprec_total = round(orig - salvage, 2)
            if abs(net - (orig - accum)) > TOL:
                dif(f"净值≠原值−累计(差{net - (orig - accum):+,.2f})")
            # 折旧重算
            elapsed = max(0, min(mob(date.fromisoformat(c["入账日期"]), BASE), life)) if life else 0
            monthly_calc = round(deprec_total / life, 2) if life else 0.0
            if abs(num(c["月折旧额"]) - monthly_calc) > TOL:
                dif(f"月折旧额≠重算(账{num(c['月折旧额']):,.2f}/应{monthly_calc:,.2f})")
            accum_calc = round(min(monthly_calc * elapsed, deprec_total), 2)
            if abs(accum - accum_calc) > max(TOL, accum_calc * 0.005):
                dif(f"累计折旧≠重算(账{accum:,.2f}/应{accum_calc:,.2f})")
            if accum > deprec_total + TOL:
                exc(f"超额折旧(累计{accum:,.2f}>可折旧{deprec_total:,.2f})")
            if net < salvage - TOL:
                exc(f"净值低于残值(净值{net:,.2f}<残值{salvage:,.2f})")
            if c["类别"] in rules:
                min_life, std_res = rules[c["类别"]]
                if life < min_life:
                    dif(f"使用年限低于最低({life}<{min_life})")
                if abs(res_rate - std_res) > 1e-6:
                    dif(f"残值率不符规则(实{res_rate:.0%}/规则{std_res:.0%})")
            if c.get("状态") == "已处置":
                if net > TOL:
                    exc(f"已处置仍有净值({net:,.2f})")
                if p:
                    exc("已处置仍在盘点表")
            if c["部门"] != p["实物部门"]:
                dif(f"部门不符(账{c['部门']}/物{p['实物部门']})")
            if c["存放地点"] != p["实物地点"]:
                dif(f"位置不符(账{c['存放地点']}/物{p['实物地点']})")
        results.append({"卡片编号": aid, "资产名称": (c or {}).get("资产名称", "(无卡片)"), "类别": (c or {}).get("类别", "—"),
                        "原值": orig, "累计折旧": accum, "账面净值": net, "部门": (c or {}).get("部门", "—"),
                        "status": st, "issues": issues or ["账实相符"]})
    cnt = Counter(r["status"] for r in results)
    assert sum(cnt.values()) == len(results), "笔数守恒失败"
    n_loss = sum(1 for r in results if any("盘亏" in i for i in r["issues"]))
    n_gain = sum(1 for r in results if any("盘盈" in i for i in r["issues"]))
    loss_amt = round(sum(r["账面净值"] for r in results if any("盘亏" in i for i in r["issues"])), 2)
    matched = cnt.get("matched", 0)
    issue_types = Counter()
    for r in results:
        for m in (r["issues"] if r["status"] != "matched" else []):
            issue_types[m.split("(")[0]] += 1
    for p in param_issues:
        issue_types["参数·" + p.split("(")[0]] += 1
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「已处置(不在盘点表，正常)」
    all_ok = not DQ and not (set(issue_types) - {"已处置"})
    return {
        "summary": dict(cnt), "total": len(results), "match_rate": round(matched / len(results) * 100, 1),
        "all_ok": all_ok, "data_issues": list(DQ),
        "issue_types": dict(issue_types), "n_loss": n_loss, "n_gain": n_gain, "loss_amount": loss_amt, "duplicate_keys": dup,
        "base_date": base_str, "base_date_source": "调用方参数" if base else "引擎默认值", "param_issues": param_issues,
        "conservation": {"原值合计": round(sum(num(c["原值"]) for c in cards), 2),
                         "累计折旧合计": round(sum(num(c["累计折旧"]) for c in cards), 2),
                         "账面净值合计": round(sum(num(c["账面净值"]) for c in cards), 2),
                         "净值=原值−累计": round(sum(num(c["原值"]) - num(c["累计折旧"]) for c in cards), 2),
                         "卡片数": len(cards), "盘点数": len(phys), "盘亏": n_loss, "盘盈": n_gain},
        "inputs": [x for x in (fp(P(f)) for f in ["asset_cards.csv", "physical_count.csv", "category_rules.csv", "params.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv)
        rest = argv[2:]
        base = rest.pop(0) if rest and re.match(r"\d{4}-\d{2}-\d{2}$", rest[0]) else None
        dest = rest[0] if rest else None   # 非 .json 的输出名由 finish_cli 拒绝
        try:
            base and date.fromisoformat(base)
        except ValueError as e:
            print(f"参数无效：盘点基准日 {base!r} 不是有效日期（{e}）", file=sys.stderr)
            sys.exit(2)
        out = check(argv[1], base)
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", {"base_date": base or BASE_DEFAULT},
                      out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
