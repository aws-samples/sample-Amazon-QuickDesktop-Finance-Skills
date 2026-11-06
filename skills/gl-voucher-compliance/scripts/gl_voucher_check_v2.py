"""总账凭证合规自检 v2 · 确定性计算脚本
用法: python gl_voucher_check_v2.py <gl_entries.csv> <account_whitelist.csv> [business_rules.csv] [out.json]

判据（②③④⑤ 逐行检查凭证的每一行分录，不只看首行）：
  ① 恒等式   每张凭证 Σ借 == Σ贷（容差 0.01）                         → fail
  ② 白名单   科目 ∈ 启用科目表；每行会计期间 ∈ 合法期间；同一凭证各行期间一致 → fail
  ③ 交叉白名单（v2 新增，补「合法但不适用」盲区）
             每行按本行业务类型 × 借/贷方向 × 允许科目；科目合法但与业务不符 → fail
  ④ 完整性   分录 ≥ 2 行；每行摘要非空                                 → fail / review
  ⑤ 边界     金额 > 5 万时每行都须有附件                               → review
  ⑥ 数据质量 借方/贷方无法解析或留空 → 该行剔除并登记 data_issues，所在凭证 fail，all_ok=False
自检（守恒）：
  · 凭证数守恒：pass + review + fail == 凭证总数
  · 分录笔数守恒：Σ各凭证分录行数 == 输入总行数
  · 借贷差额归因：全表 Σ借 − Σ贷 == Σ 各不平凭证差额
可回溯：输入文件 SHA-256 指纹 + 行数，逐凭证问题标签与借贷金额。
向后兼容：无 business_rules.csv 或无「业务类型」列时，跳过交叉白名单。
"""
import sys, json, csv, hashlib, os, math
from collections import defaultdict, Counter
from datetime import datetime

VALID_PERIODS = {"2026-07", "2026-08", "2026-09"}
BIG = 50000.0
TOL = 0.01
OK_LABELS = {"合规"}

# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计（附件留空按无附件处理）。
NUMERIC = {"gl_entries.csv": ("借方", "贷方", "附件")}
BLANK_OK = {"附件"}
DQ = []      # 本次 check() 的数据质量问题；check() 开头清空
QUAR = []    # 被剔除的原始行（文件名, 行号, 行），用于把问题归到所在凭证


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return screen(path, list(csv.DictReader(f)))


def screen(path, rows):
    """按 NUMERIC 严格校验数值字段；不合格的行登记到 DQ 并剔除，返回可用行。行号按 CSV 文件行计（表头为第 1 行）。"""
    fn, keep = os.path.basename(path), []
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


def fingerprint(path):
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256(open(path, "rb").read()).hexdigest()
    n = max(sum(1 for _ in open(path, encoding="utf-8-sig")) - 1, 0)
    return {"file": os.path.basename(path), "sha256": h, "rows": n}


def load_rules(path):
    if not path or not os.path.exists(path):
        return None
    rules = defaultdict(lambda: {"借": set(), "贷": set()})
    for r in load(path):
        rules[r["业务类型"]][r["方向"]] |= set(r["允许科目"].split("|"))
    return dict(rules)


def check(entries_path, whitelist_path, rules_path=None, valid_periods=VALID_PERIODS, big=BIG):
    DQ.clear()
    QUAR.clear()
    entries = load(entries_path)
    acct_set = {r["科目编码"] for r in load(whitelist_path)}
    rules = load_rules(rules_path)
    has_type = bool(entries) and "业务类型" in entries[0]

    by_v = defaultdict(list)
    for e in entries:
        by_v[e["凭证号"]].append(e)
    # 被剔除的分录行归到所在凭证（凭证号可读时）
    quar_v = defaultdict(list)
    for fn, i, r in QUAR:
        if fn == os.path.basename(entries_path):
            quar_v[r.get("凭证号", "")].append(i)

    results = []
    for vid, lines in by_v.items():
        h = lines[0]
        periods = sorted({l["会计期间"] for l in lines})
        btypes = sorted({l.get("业务类型", "") for l in lines})
        dr = round(sum(num(l["借方"]) for l in lines), 2)
        cr = round(sum(num(l["贷方"]) for l in lines), 2)
        amt = max(dr, cr)
        issues, status = [], "pass"

        def hard(msg):
            nonlocal status
            issues.append(msg)
            status = "fail"

        def soft(msg):
            nonlocal status
            issues.append(msg)
            if status != "fail":
                status = "review"

        if abs(dr - cr) > TOL:
            hard(f"借贷不平(借{dr:,.2f}/贷{cr:,.2f})")
        if len(lines) < 2:
            hard("单边分录")
        bad = sorted({l["科目"] for l in lines if l["科目"] not in acct_set})
        if bad:
            hard(f"科目不在白名单({','.join(bad)})")
        bad_p = [p for p in periods if p not in valid_periods]
        if bad_p:
            hard(f"非法会计期间({','.join(bad_p)})")
        if len(periods) > 1:
            hard(f"凭证内会计期间不一致({'/'.join(periods)})")
        # 交叉白名单：每行按本行业务类型判断，仅对白名单内科目判断（白名单外已由②拦截）
        if rules is not None and has_type:
            unreg = [bt for bt in btypes if bt not in rules]
            if unreg:
                hard(f"业务类型未登记({','.join(unreg)})")
            mis = defaultdict(list)
            for l in lines:
                bt = l.get("业务类型", "")
                if bt not in rules or l["科目"] not in acct_set:
                    continue
                side = "借" if num(l["借方"]) > 0 else "贷"
                if l["科目"] not in rules[bt][side]:
                    mis[bt].append(f"{side}{l['科目']}")
            if mis:
                desc = "；".join(bt + ":" + ",".join(m) for bt, m in mis.items())
                hard(f"科目与业务不符({desc})")
        if any(not (l.get("摘要") or "").strip() for l in lines):
            soft("缺摘要")
        if amt > big and any(num(l.get("附件", 1)) == 0 for l in lines):
            soft("大额无附件")
        if vid in quar_v:
            hard(f"数值无法解析(第{','.join(map(str, quar_v[vid]))}行已剔除，见 data_issues)")
        if not issues:
            issues = ["合规"]
        results.append({"凭证号": vid, "会计期间": "/".join(periods), "业务类型": "/".join(btypes),
                        "制单人": h["制单人"], "分录数": len(lines), "借方合计": dr, "贷方合计": cr,
                        "差额": round(dr - cr, 2), "金额": round(amt, 2),
                        "status": status, "issues": issues})

    cnt = Counter(r["status"] for r in results)
    # ── 自检（守恒）──
    assert sum(cnt.values()) == len(results), "凭证数守恒失败"
    assert sum(r["分录数"] for r in results) == len(entries), "分录笔数守恒失败"
    tot_dr = round(sum(num(e["借方"]) for e in entries), 2)
    tot_cr = round(sum(num(e["贷方"]) for e in entries), 2)
    unbal = round(sum(r["差额"] for r in results if abs(r["差额"]) > TOL), 2)
    assert abs((tot_dr - tot_cr) - unbal) < 0.05, "借贷差额归因失败"

    issue_cnt = Counter()
    for r in results:
        for i in r["issues"]:
            issue_cnt[i.split("(")[0]] += 1
    for d in DQ:
        issue_cnt["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「合规」
    all_ok = not DQ and not (set(issue_cnt) - OK_LABELS)
    maker = defaultdict(lambda: {"凭证数": 0, "问题凭证": 0})
    for r in results:
        maker[r["制单人"]]["凭证数"] += 1
        if r["status"] != "pass":
            maker[r["制单人"]]["问题凭证"] += 1

    return {
        "summary": dict(cnt), "total": len(results),
        "pass_rate": round(cnt.get("pass", 0) / len(results) * 100, 1),
        "fail_amount": round(sum(r["金额"] for r in results if r["status"] == "fail"), 2),
        "review_amount": round(sum(r["金额"] for r in results if r["status"] == "review"), 2),
        "issue_types": dict(issue_cnt),
        "maker_risk": dict(maker),
        "conservation": {"凭证数": len(results), "分录行数": len(entries),
                         "全表借方": tot_dr, "全表贷方": tot_cr,
                         "全表借贷差": round(tot_dr - tot_cr, 2), "不平凭证差额合计": unbal},
        "cross_whitelist": rules is not None and has_type,
        "data_issues": list(DQ), "all_ok": all_ok,
        "inputs": [x for x in (fingerprint(entries_path), fingerprint(whitelist_path), fingerprint(rules_path)) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv)
        rp = argv[3] if len(argv) > 3 and argv[3].endswith(".csv") else None
        dest = argv[-1] if argv[-1].endswith(".json") else "analysis.json"
        out = check(argv[1], argv[2], rp)
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", {}, out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
