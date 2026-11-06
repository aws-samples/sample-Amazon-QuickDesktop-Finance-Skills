"""关联方往来对账 v2 · 确定性计算脚本
用法: python intercompany_recon_v2.py <intercompany_ledger.csv> [related_party_marked.csv related_party_registry.csv counterparty_balances.csv] [out.json]

逐交易判据（同一交易号应有双方各一条、金额对称）：
  ① 完整性     只有一条 → 单边登记（exception）；超过两条 → 重复入账（exception）
  ② 对手方对称 A 的对手方 = B 的记账实体，且反之          → 否则 对手方不符（exception）
  ③ 方向       一方应收(+)、一方应付(−)                    → 同向 方向错误（exception）
  ④ 交叉一致   A 额 + B 额 = 0（容差 0.01）                → 否则 金额不符（difference）
  ⑤ 类型一致   双方交易类型相同                            → 否则 交易类型不一致（difference）
  ⑥ 时间性     双方入账日期差 ≤ 5 天                       → 否则 时间性差异（difference）
单边记录配对提示：两条单边记录主体互为对手方、金额对称、日期相近 → 疑似交易号不一致。
守恒自检：
  · 交易数守恒：matched + difference + exception == 交易总数
  · 笔数守恒：Σ各交易记录数 == 台账行数
  · 抵消差额归因：全体净额 == Σ 各交易净额；并分向列示（正差 / 负差 / 毛额），防净额掩盖
范围完备性（盲区补丁，🟡需外部锚点）：
  财务系统关联方标记 ↔ 工商股权登记 双向比对；再用全部客商往来余额，量化未标记关联方的余额。
  未提供登记数据时，诚实声明"无法验证关联方清单完备性"。
  范围不完备（存在未标记关联方或其往来余额）或无法验证 → 计入问题标签，all_ok=False。
可回溯：输入文件 SHA-256 指纹 + 行数 + 时间戳，逐交易问题标签。
"""
import sys, json, csv, hashlib, os, math
from datetime import date, datetime
from collections import defaultdict, Counter

TOL = 0.01
TIMING_DAYS = 5
PAIR_DAYS = 10


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "intercompany_ledger.csv": ("金额",),
    "counterparty_balances.csv": ("期末余额",),
}
BLANK_OK = set()
DQ = []  # 本次运行的数据质量问题；入口函数开头清空


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return screen(path, list(csv.DictReader(f)))


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


def parse(d):
    return date.fromisoformat(str(d)[:10])


def fingerprint(path):
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    n = max(sum(1 for _ in open(path, encoding="utf-8-sig")) - 1, 0)
    return {"file": os.path.basename(path), "sha256": h, "rows": n}


def scope_check(marked_path, registry_path, balances_path):
    if not (marked_path and registry_path and os.path.exists(marked_path) and os.path.exists(registry_path)):
        return {"verifiable": False,
                "advice": "未提供工商股权登记等外部来源，无法验证关联方清单是否完备；当前只能核对已标记主体之间的往来。"}
    marked = {r["主体"] for r in load(marked_path)}
    reg = {r["主体"]: r.get("关系", "") for r in load(registry_path)}
    unmarked = sorted(set(reg) - marked)
    marked_only = sorted(marked - set(reg))
    leaked = []
    if balances_path and os.path.exists(balances_path):
        for r in load(balances_path):
            if r["客商名称"] in unmarked:
                leaked.append({"记账实体": r["记账实体"], "客商名称": r["客商名称"], "关系": reg[r["客商名称"]],
                               "往来类型": r["往来类型"], "期末余额": round(num(r["期末余额"]), 2)})
    return {"verifiable": True, "marked": sorted(marked), "registry": sorted(reg),
            "unmarked_related": [{"主体": u, "关系": reg[u]} for u in unmarked],
            "marked_not_in_registry": marked_only,
            "leaked_balances": leaked,
            "leaked_amount": round(sum(x["期末余额"] for x in leaked), 2),
            "complete": not unmarked}


def reconcile(ledger_path, marked_path=None, registry_path=None, balances_path=None, timing_days=TIMING_DAYS):
    DQ.clear()
    rows = load(ledger_path)
    for r in rows:
        r["金额"] = num(r["金额"])
    by_txn = defaultdict(list)
    for r in rows:
        by_txn[r["交易号"]].append(r)

    results = []
    for txn, recs in by_txn.items():
        issues, status = [], "matched"

        def exc(m):
            nonlocal status
            issues.append(m)
            status = "exception"

        def diff(m):
            nonlocal status
            issues.append(m)
            if status == "matched":
                status = "difference"

        net = round(sum(r["金额"] for r in recs), 2)
        a = recs[0]
        b = recs[1] if len(recs) > 1 else None
        if len(recs) == 1:
            exc("单边登记(仅一方入账)")
        if len(recs) > 2:
            exc(f"重复入账({len(recs)}条)")
        if b is not None:
            if not (a["对手方"] == b["记账实体"] and b["对手方"] == a["记账实体"]):
                exc(f"对手方不符({a['记账实体']}→{a['对手方']} / {b['记账实体']}→{b['对手方']})")
            if (a["金额"] > 0) == (b["金额"] > 0):
                exc("方向错误(双方同向)")
            elif abs(a["金额"] + b["金额"]) > TOL:
                diff(f"金额不符(差{a['金额'] + b['金额']:+,.2f})")
            if a["交易类型"] != b["交易类型"]:
                diff(f"交易类型不一致({a['交易类型']}/{b['交易类型']})")
            gap = abs((parse(a["日期"]) - parse(b["日期"])).days)
            if gap > timing_days:
                diff(f"时间性差异({gap}天)")
        if not issues:
            issues = ["双向一致"]
        results.append({"交易号": txn, "实体A": a["记账实体"], "对手方A": a["对手方"],
                        "实体B": b["记账实体"] if b else None, "类型": a["交易类型"],
                        "A额": a["金额"], "B额": b["金额"] if b else None,
                        "记录数": len(recs), "净额": net, "status": status, "issues": issues})

    # 单边记录配对提示（疑似交易号不一致）
    singles = [r for r in results if r["记录数"] == 1]
    hints, used = [], set()
    for i, x in enumerate(singles):
        rx = by_txn[x["交易号"]][0]
        for y in singles[i + 1:]:
            if y["交易号"] in used or x["交易号"] in used:
                continue
            ry = by_txn[y["交易号"]][0]
            if (rx["记账实体"] == ry["对手方"] and ry["记账实体"] == rx["对手方"]
                    and abs(rx["金额"] + ry["金额"]) <= TOL
                    and abs((parse(rx["日期"]) - parse(ry["日期"])).days) <= PAIR_DAYS):
                hints.append({"交易号1": x["交易号"], "交易号2": y["交易号"],
                              "主体": f"{rx['记账实体']} ↔ {ry['记账实体']}", "金额": abs(rx["金额"])})
                used |= {x["交易号"], y["交易号"]}
    hinted = used
    for r in results:
        if r["交易号"] in hinted:
            r["issues"].append("疑似交易号不一致(已找到对称记录)")

    cnt = Counter(r["status"] for r in results)
    # ── 守恒自检 ──
    assert sum(cnt.values()) == len(results), "交易数守恒失败"
    assert sum(r["记录数"] for r in results) == len(rows), "笔数守恒失败"
    elim_gap = round(sum(r["金额"] for r in rows), 2)
    assert abs(elim_gap - round(sum(r["净额"] for r in results), 2)) < 0.05, "抵消差额归因失败"
    pos = round(sum(r["净额"] for r in results if r["净额"] > TOL), 2)
    neg = round(sum(r["净额"] for r in results if r["净额"] < -TOL), 2)

    issue_cnt = Counter(i.split("(")[0] for r in results for i in r["issues"])
    entity_net = defaultdict(float)
    for r in rows:
        entity_net[r["记账实体"]] += r["金额"]
    scope = scope_check(marked_path, registry_path, balances_path)   # 先跑完：其中的余额表也参与数据质量校验
    # 范围完备性计入总判定：无法验证或存在未标记关联方（及其往来余额）→ all_ok=False
    if not scope.get("verifiable"):
        issue_cnt["关联方清单完备性无法验证"] += 1
    else:
        if scope.get("unmarked_related"):
            issue_cnt["未标记关联方"] += len(scope["unmarked_related"])
        if scope.get("leaked_balances"):
            issue_cnt["未标记关联方余额"] += len(scope["leaked_balances"])
    for d in DQ:
        issue_cnt["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「双向一致」
    all_ok = not DQ and not (set(issue_cnt) - {"双向一致"})
    return {
        "summary": dict(cnt), "total": len(results), "all_ok": all_ok, "data_issues": list(DQ),
        "match_rate": round(cnt.get("matched", 0) / len(results) * 100, 1),
        "issue_types": dict(issue_cnt),
        "elimination_gap": elim_gap,
        "gap_by_sign": {"正差合计": pos, "负差合计": neg, "毛额": round(pos - neg, 2), "净额": round(pos + neg, 2)},
        "consistent_amount": round(sum(abs(r["A额"]) for r in results if r["status"] == "matched"), 2),
        "entity_net": {k: round(v, 2) for k, v in sorted(entity_net.items(), key=lambda x: -abs(x[1]))},
        "pair_hints": hints,
        "conservation": {"交易数": len(results), "台账行数": len(rows), "全体净额": elim_gap},
        "scope": scope,
        "inputs": [x for x in (fingerprint(p) for p in (ledger_path, marked_path, registry_path, balances_path)) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv)
        args = [a for a in argv[1:] if a.endswith(".csv")]
        dest = argv[-1] if argv[-1].endswith(".json") else "analysis.json"
        out = reconcile(*args)
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", {}, out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
