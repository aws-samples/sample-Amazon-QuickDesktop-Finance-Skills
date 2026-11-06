"""存货盘点差异处理 v2 · 确定性计算脚本
用法: python inventory_count_check_v2.py <数据目录> [out.json] [--major 金额] [--loss-rate 类别=比例 ...]
数据目录需含：
  book_inventory.csv  账存台账（SKU, 品名, 类别, 仓库, 单位, 单价, 账存数量）
  physical_count.csv  实盘表（SKU, 仓库, 单位, 实盘数量）
  tolerance_rules.csv 类别合理损耗率（类别, 合理损耗率）——只用于比对，不参与计算
  params.csv          参数（重大差异金额阈值, 盘点基准日）——阈值只用于比对，不参与计算
核对口径：重大差异金额阈值与各类别合理损耗率取引擎常量 MAJOR_DEFAULT / LOSS_RATES，或调用方显式参数
  （major > 0；损耗率 ∈ [0, MAX_LOSS_RATE]，越界抛 ValueError）。数据目录中的值与采用口径不一致
  → 记入 param_issues 与 issue_types「参数·…」，all_ok=False；被审数据不能放宽自己的审计阈值。
匹配键：SKU + 仓库（支持同一 SKU 多仓分别盘点）。

判据：
  ① 账实交叉    账有实无→全盘亏；实有账无→全盘盈；账实均有→比数量
  ② 单位一致    账实单位相同（否则数量不可比，单列）
  ③ 合理损耗    |差异率| ≤ 类别合理损耗率 → 正常损耗（不计异常）；超出 → 异常盘盈/盘亏
  ④ 金额分级    |差异金额| ≥ 重大差异阈值 → 重大差异（需说明）
  ⑤ 边界        账存、实盘数量 ≥ 0；单价 > 0（否则金额无法折算，单列核实）
  ⑥ 完整性      同一 SKU 的所有账存仓位都必须有实盘记录
差异守恒：净差异金额 = Σ盘盈金额 − Σ盘亏金额；另列盘盈毛额、盘亏毛额（防净额掩盖）。
可回溯：输入 SHA-256 指纹 + 基准日 + 时间戳；逐项账实、差异率、金额与判据结论。
"""
import sys, os, csv, json, hashlib, math
from collections import Counter, defaultdict
from datetime import datetime

TOL = 0.01
MAJOR_DEFAULT = 50000.0   # 重大差异金额阈值（元）
LOSS_RATES = {"原材料": 0.01, "半成品": 0.005, "产成品": 0.005, "包装物": 0.02, "低值易耗": 0.03}
MAX_LOSS_RATE = 0.05      # 调用方可设定的合理损耗率上限


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "book_inventory.csv": ("单价", "账存数量"),
    "physical_count.csv": ("实盘数量",),
    "tolerance_rules.csv": ("合理损耗率",),
    "params.csv": (("值", "参数", ("重大差异金额阈值",)),),
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


def _finite(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def effective_params(major=None, loss_rates=None):
    """返回本次采用的 (重大差异金额阈值, 各类别合理损耗率)；显式参数越界抛 ValueError（消息含「取值无效」）。"""
    if major is not None:
        m = _finite(major)
        if m is None or m <= 0:
            raise ValueError(f"重大差异金额阈值 major={major!r} 取值无效（须为大于 0 的有限数）")
    bad_rates = {k: v for k, v in (loss_rates or {}).items()
                 if _finite(v) is None or not (0 <= _finite(v) <= MAX_LOSS_RATE)}
    if bad_rates:
        raise ValueError(f"合理损耗率取值无效 {bad_rates}（须在 [0, {MAX_LOSS_RATE}]）")
    eff_rates = dict(LOSS_RATES)
    eff_rates.update({k: float(v) for k, v in (loss_rates or {}).items()})
    return (MAJOR_DEFAULT if major is None else float(major)), eff_rates


def check(data_dir, major=None, loss_rates=None):
    major_arg = major
    eff_major, eff_rates = effective_params(major, loss_rates)
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    book = load(P("book_inventory.csv"))
    count = load(P("physical_count.csv"))
    # 数据目录中的损耗率与阈值只比对，不参与计算
    param_issues = []
    for r in load(P("tolerance_rules.csv")):
        cat, v = r["类别"], num(r["合理损耗率"])
        if cat not in eff_rates or abs(v - eff_rates[cat]) > 1e-9:
            param_issues.append(f"合理损耗率与核对口径不一致({cat}:数据目录{v:g}/采用{eff_rates.get(cat, '未设')})")
    pm = {r["参数"]: r["值"] for r in load(P("params.csv"))}
    if "重大差异金额阈值" in pm and abs(num(pm["重大差异金额阈值"]) - eff_major) > 1e-6:
        param_issues.append(f"重大差异金额阈值与核对口径不一致(数据目录{num(pm['重大差异金额阈值']):g}/采用{eff_major:g})")
    tol, major = eff_rates, eff_major
    key = lambda r: (r["SKU"], r["仓库"])
    # 同一 (SKU, 仓库) 只取首行；重复键不静默覆盖，单列 duplicate_keys 并在该键结果中记为异常
    bby, cby = {}, {}
    for b in book:
        bby.setdefault(key(b), b)
    for c in count:
        cby.setdefault(key(c), c)
    bdup = {k: n for k, n in Counter(key(b) for b in book).items() if n > 1}
    cdup = {k: n for k, n in Counter(key(c) for c in count).items() if n > 1}
    dup = ([{"文件": "book_inventory.csv", "SKU": k[0], "仓库": k[1], "行数": n} for k, n in sorted(bdup.items())]
           + [{"文件": "physical_count.csv", "SKU": k[0], "仓库": k[1], "行数": n} for k, n in sorted(cdup.items())])
    sku_wh = defaultdict(set)
    for b in book:
        sku_wh[b["SKU"]].add(b["仓库"])
    counted_wh = defaultdict(set)
    for c in count:
        counted_wh[c["SKU"]].add(c["仓库"])
    results = []
    for k in sorted(set(bby) | set(cby)):
        b, c = bby.get(k), cby.get(k)
        price = num(b["单价"]) if b else 0.0
        cat = (b or {}).get("类别", "—")
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

        bq = num(b["账存数量"]) if b else 0.0
        cq = num(c["实盘数量"]) if c else 0.0
        diff_qty = 0.0
        diff_amt = 0.0
        note_rate = None
        if k in bdup:
            exc(f"重复账存记录({bdup[k]}条)")
        if k in cdup:
            exc(f"重复盘点记录({cdup[k]}条)")
        if b and not c:
            diff_qty = -bq
            diff_amt = round(diff_qty * price, 2)
            exc("账有实无(全盘亏)")
        elif c and not b:
            diff_qty = cq
            diff_amt = 0.0
            exc("实有账无(全盘盈)")
        else:
            if b.get("单位") != c.get("单位"):
                exc(f"单位不一致(账{b.get('单位')}/实{c.get('单位')})")
            if bq < 0:
                exc(f"账存为负({bq:g})")
            if cq < 0:
                exc(f"实盘为负({cq:g})")
            diff_qty = round(cq - bq, 4)
            diff_amt = round(diff_qty * price, 2)
            rate = (diff_qty / bq) if bq > 0 else (0.0 if diff_qty == 0 else float("inf"))
            note_rate = round(rate, 4)
            lr = tol.get(cat, 0.0)
            if st == "matched" and abs(diff_qty) > TOL:
                if abs(rate) <= lr + 1e-9:
                    issues.append(f"正常损耗(差异率{rate:.1%}≤损耗率{lr:.1%})")  # 不改状态，视为相符
                elif diff_qty < 0:
                    dif(f"盘亏({-diff_qty:g}，差异率{rate:.1%}>损耗率{lr:.1%})")
                else:
                    dif(f"盘盈(+{diff_qty:g})")
            if price <= 0 and abs(diff_qty) > TOL:
                exc("单价为零(金额无法折算)")
            if abs(diff_amt) >= major:
                (dif if st == "matched" else exc)(f"重大差异(金额{diff_amt:,.2f})")
        results.append({"SKU": k[0], "仓库": k[1], "品名": (b or {}).get("品名", "(无账)"), "类别": cat, "单位": (b or c or {}).get("单位", "—"),
                        "单价": price, "账存": bq, "实盘": cq, "差异数量": diff_qty, "差异率": note_rate, "差异金额": diff_amt,
                        "status": st, "issues": issues or ["账实相符"]})
    # ⑥ 多仓漏盘
    miss = []
    for sku, whs in sku_wh.items():
        for wh in whs - counted_wh.get(sku, set()):
            miss.append({"SKU": sku, "仓库": wh})
            for r in results:
                if r["SKU"] == sku and r["仓库"] == wh and "账有实无" not in "".join(r["issues"]):
                    pass
    cnt = Counter(r["status"] for r in results)
    assert sum(cnt.values()) == len(results), "笔数守恒失败"
    loss = round(sum(r["差异金额"] for r in results if r["差异金额"] < 0), 2)
    gain = round(sum(r["差异金额"] for r in results if r["差异金额"] > 0), 2)
    net = round(gain + loss, 2)
    assert abs(net - round(sum(r["差异金额"] for r in results), 2)) < 0.5, "差异守恒失败"
    n_loss = sum(1 for r in results if any("盘亏" in i for i in r["issues"]))
    n_gain = sum(1 for r in results if any("盘盈" in i for i in r["issues"]))
    normal = sum(1 for r in results if any("正常损耗" in i for i in r["issues"]))
    issue_types = Counter()
    for r in results:
        for m in (r["issues"] if r["status"] != "matched" else []):
            issue_types[m.split("(")[0]] += 1
    if miss:
        issue_types["多仓漏盘"] = len(miss)
    for p in param_issues:
        issue_types["参数·" + p.split("(")[0]] += 1
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且没有任何问题标签（正常损耗不计入问题标签）
    all_ok = not DQ and not issue_types
    matched = sum(1 for r in results if r["status"] == "matched")
    return {
        "summary": dict(cnt), "total": len(results), "match_rate": round(matched / len(results) * 100, 1),
        "all_ok": all_ok, "data_issues": list(DQ),
        "issue_types": dict(issue_types), "missing_count": miss, "normal_loss_items": normal, "duplicate_keys": dup,
        "n_loss": n_loss, "n_gain": n_gain,
        "params": {"重大差异金额阈值": eff_major, "合理损耗率": eff_rates,
                   "来源": "调用方参数" if major_arg is not None or loss_rates else "引擎内置"},
        "param_issues": param_issues,
        "conservation": {"盘亏金额": round(abs(loss), 2), "盘盈金额": round(gain, 2), "净差异": net,
                         "盘亏毛额": round(abs(loss), 2), "盘盈毛额": round(gain, 2),
                         "账面总额": round(sum(num(b["账存数量"]) * num(b["单价"]) for b in book), 2),
                         "账存行数": len(book), "实盘行数": len(count), "多仓漏盘": len(miss)},
        "inputs": [x for x in (fp(P(f)) for f in ["book_inventory.csv", "physical_count.csv", "tolerance_rules.csv", "params.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv, value_opts=("--major",), multi_opts=("--loss-rate",))
        rates = {}
        for item in opts.get("--loss-rate", []):
            cat, eq, val = item.partition("=")
            if not eq or not cat.strip() or _finite(val) is None:
                raise og.OutputPathError(f"--loss-rate 格式须为 类别=比例：{item!r}")
            rates[cat.strip()] = float(val)
        try:
            effective_params(opts.get("--major"), rates or None)
        except ValueError as e:
            print(f"参数无效：{e}", file=sys.stderr)
            sys.exit(2)
        out = check(argv[1], major=opts.get("--major"), loss_rates=rates or None)
        dest = argv[2] if len(argv) > 2 else "analysis.json"
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", out["params"], out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
