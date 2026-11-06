"""三单匹配核对 v2 · 确定性计算脚本
用法: python three_way_match_v2.py <数据目录> [out.json]
数据目录需包含：
  purchase_orders.csv  采购订单行（po_id, line_no, supplier_id, supplier, item, unit, qty, unit_price, amount, order_date）
  goods_receipts.csv   入库单（grn_id, po_id, line_no, supplier_id, qty_received, recv_date），一行订单可分多次收货
  invoices.csv         采购发票（inv_no, po_id, line_no, supplier_id, qty_billed, unit_price, amount, inv_date），可分多张开

逐订单行判据：
  ① 完整性     有发票无入库 → 缺入库单（异常）；有入库无发票 → 待开票（正常，不计错误）
  ② 供应商一致 入库单、发票的 supplier_id == 订单供应商
  ③ 数量       收货 vs 订单（短收/超收）；开票 ≤ 收货（按收货付款）；开票 ≤ 订单
  ④ 单价       发票单价 == 订单单价（容差 PRICE_TOL）
  ⑤ 金额恒等式 发票金额 == 开票数量 × 发票单价
  ⑥ 时间       发票日期不早于首次收货日期（先票后货 → 待核实）
  数据质量     引用该订单行的入库单或发票因数值无法解析被剔除（见 data_issues）→ 含无法解析单据（待核实）
全表判据：
  ⑦ 发票唯一性 发票号归一化（去空格/全半角/前导零）后唯一；「供应商 + 金额 + 日期」组合指纹唯一（抓换号重开）
  ⑧ 范围完备   入库单、发票引用的订单行必须存在
守恒（付款口径）：
  可付金额 = min(开票, 收货, 订单) × 订单单价；暂缓金额 = 发票金额 − 可付金额
  Σ发票金额 == Σ可付金额 + Σ暂缓金额；入库单、发票行数全部归集（含孤立单据）
可回溯：输入 SHA-256 指纹 + 时间戳；逐订单行收货、开票、可付、暂缓与问题标签。
"""
import sys, os, csv, json, hashlib, re, unicodedata, math
from collections import defaultdict, Counter
from datetime import date, datetime

PRICE_TOL = 0.01
AMT_TOL = 0.5


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "purchase_orders.csv": ("line_no", "qty", "unit_price"),
    "goods_receipts.csv": ("line_no", "qty_received"),
    "invoices.csv": ("line_no", "qty_billed", "unit_price", "amount"),
}
BLANK_OK = set()
DQ = []  # 本次运行的数据质量问题；入口函数开头清空
QUAR = []  # 被剔除的原始行（文件名, 行号, 行），用于把问题归到所在订单行


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


def norm(s):
    s = unicodedata.normalize("NFKC", str(s)).upper()
    s = re.sub(r"\s+", "", s)
    # 只去掉每段数字开头的前导零（如 FP-0012 → FP-12），不动数字中间的 0
    return re.sub(r"(?<!\d)0+(?=\d)", "", s)


def check(data_dir):
    DQ.clear(); QUAR.clear()
    P = lambda f: os.path.join(data_dir, f)
    pos, grns, invs = load(P("purchase_orders.csv")), load(P("goods_receipts.csv")), load(P("invoices.csv"))
    key = lambda r: (r["po_id"], str(int(num(r["line_no"]))))
    po_by = {key(p): p for p in pos}
    g_by, i_by = defaultdict(list), defaultdict(list)
    orphan = []
    for g in grns:
        if key(g) in po_by:
            g_by[key(g)].append(g)
        else:
            orphan.append(("入库单", g))
    for i in invs:
        if key(i) in po_by:
            i_by[key(i)].append(i)
        else:
            orphan.append(("发票", i))

    # ⑦ 发票唯一性
    no_groups, fp_groups = defaultdict(list), defaultdict(list)
    for i in invs:
        no_groups[norm(i["inv_no"])].append(i)
        fp_groups[(i["supplier_id"], round(num(i["amount"]), 2), i["inv_date"])].append(i)
    dup_no = {k: v for k, v in no_groups.items() if len(v) > 1}
    dup_fp = {k: v for k, v in fp_groups.items() if len(v) > 1 and len({norm(x["inv_no"]) for x in v}) > 1}
    dup_flag = defaultdict(list)
    for k, v in dup_no.items():
        for x in v:
            dup_flag[key(x)].append(f"重复发票号({'/'.join(sorted({y['inv_no'] for y in v}))})")
    for k, v in dup_fp.items():
        for x in v:
            dup_flag[key(x)].append(f"疑似换号重开({'/'.join(y['inv_no'] for y in v)})")

    # 被剔除的入库单 / 发票行归到所引用的订单行（行号可读时）：该行少了一张单据，不能判「完全匹配」或「待开票」
    # 行号须为有限整数（与 key() 的取值一致）；行号无法解析、非有限或带小数时无法确定订单行，挂到该订单的全部行
    quar_k = defaultdict(list)
    for fn, i, r in QUAR:
        if fn in ("goods_receipts.csv", "invoices.csv"):
            tag = f"{fn}第{i}行"
            try:
                x = float(r.get("line_no") or "")
                ln = str(int(x)) if math.isfinite(x) and x == int(x) else None
            except (ValueError, OverflowError):
                ln = None
            if ln is not None:
                quar_k[(r.get("po_id", ""), ln)].append(tag)
            else:
                for p in pos:
                    if p["po_id"] == r.get("po_id", ""):
                        quar_k[key(p)].append(tag)
    results = []
    for p in pos:
        k = key(p)
        gs, iv = g_by.get(k, []), i_by.get(k, [])
        oq, op = num(p["qty"]), num(p["unit_price"])
        rq = sum(num(g["qty_received"]) for g in gs)
        bq = sum(num(i["qty_billed"]) for i in iv)
        ba = round(sum(num(i["amount"]) for i in iv), 2)
        issues, status = [], "matched"

        def exc(m):
            nonlocal status
            issues.append(m)
            status = "exception"

        def dif(m):
            nonlocal status
            issues.append(m)
            if status in ("matched", "pending"):
                status = "difference"

        def rev(m):
            nonlocal status
            issues.append(m)
            if status in ("matched", "pending"):
                status = "review"

        if iv and not gs:
            exc("缺入库单(已开票未收货)")
        if gs and not iv:
            status = "pending"
        for g in gs:
            if g["supplier_id"] != p["supplier_id"]:
                exc(f"入库单供应商不一致({g['supplier_id']}≠{p['supplier_id']})")
                break
        for i in iv:
            if i["supplier_id"] != p["supplier_id"]:
                exc(f"发票供应商不一致({i['supplier_id']}≠{p['supplier_id']})")
                break
        if gs and rq < oq:
            (dif if iv else rev)(f"短收({rq:g}/{oq:g})")
        if gs and rq > oq:
            exc(f"超量收货({rq:g}>{oq:g})")
        if iv and gs and bq > rq:
            exc(f"开票超过收货({bq:g}>{rq:g})")
        if iv and bq > oq:
            exc(f"超订单开票({bq:g}>{oq:g})")
        for i in iv:
            if abs(num(i["unit_price"]) - op) > PRICE_TOL:
                dif(f"单价差异({num(i['unit_price']):,.2f}≠{op:,.2f})")
                break
        for i in iv:
            if abs(num(i["amount"]) - num(i["qty_billed"]) * num(i["unit_price"])) > AMT_TOL:
                exc(f"发票金额≠数量×单价({i['inv_no']})")
                break
        if iv and gs:
            first = min(date.fromisoformat(g["recv_date"]) for g in gs)
            early = [i for i in iv if date.fromisoformat(i["inv_date"]) < first]
            if early:
                rev(f"先票后货(发票{early[0]['inv_date']}早于收货{first.isoformat()})")
        for m in dup_flag.get(k, []):
            exc(m)
        if k in quar_k:
            rev(f"含无法解析单据({'、'.join(quar_k[k])}已剔除，见 data_issues)")
        payable = round(min(bq, rq, oq) * op, 2) if (iv and gs) else 0.0
        if any("供应商不一致" in x or "重复发票" in x or "换号重开" in x or "金额≠" in x for x in issues) or k in quar_k:
            payable = 0.0           # 含被剔除单据的订单行单据不全，可付为 0，全部暂缓
        hold = round(ba - payable, 2)
        if not issues:
            issues = ["待开票(已收货)"] if status == "pending" else ["完全匹配"]
        results.append({"订单行": f"{p['po_id']}#{k[1]}", "供应商": p["supplier"], "物料": p["item"], "订单数量": oq, "订单单价": op,
                        "收货数量": rq, "开票数量": bq, "发票金额": ba, "可付金额": payable, "暂缓金额": hold,
                        "入库单数": len(gs), "发票数": len(iv), "status": status, "issues": issues})
    for kind, r in orphan:
        amt = round(num(r.get("amount", 0)), 2)
        results.append({"订单行": f"{r['po_id']}#{r['line_no']}", "供应商": r["supplier_id"], "物料": "—", "订单数量": 0.0, "订单单价": 0.0,
                        "收货数量": num(r.get("qty_received", 0)), "开票数量": num(r.get("qty_billed", 0)), "发票金额": amt,
                        "可付金额": 0.0, "暂缓金额": amt, "入库单数": 1 if kind == "入库单" else 0, "发票数": 1 if kind == "发票" else 0,
                        "status": "exception", "issues": [f"无订单{kind}({r.get('inv_no') or r.get('grn_id')})"]})

    cnt = Counter(r["status"] for r in results)
    assert sum(cnt.values()) == len(results)
    assert sum(r["入库单数"] for r in results) == len(grns), "入库单归集守恒失败"
    assert sum(r["发票数"] for r in results) == len(invs), "发票归集守恒失败"
    inv_tot = round(sum(num(i["amount"]) for i in invs), 2)
    pay_tot = round(sum(r["可付金额"] for r in results), 2)
    hold_tot = round(sum(r["暂缓金额"] for r in results), 2)
    assert abs(inv_tot - pay_tot - hold_tot) < 0.05, "付款守恒失败"
    issue_types = Counter(re.sub(r"\(.*", "", i) for r in results for i in r["issues"])
    judged = [r for r in results if r["status"] != "pending"]
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「完全匹配」「待开票」
    all_ok = not DQ and not (set(issue_types) - {"完全匹配", "待开票"})
    return {
        "summary": dict(cnt), "total": len(results), "all_ok": all_ok, "data_issues": list(DQ),
        "match_rate": round(cnt.get("matched", 0) / max(len(judged), 1) * 100, 1),
        "issue_types": dict(issue_types),
        "duplicates": {"按发票号": [[x["inv_no"] for x in v] for v in dup_no.values()],
                       "按组合指纹": [[x["inv_no"] for x in v] for v in dup_fp.values()]},
        "conservation": {"发票总额": inv_tot, "可付金额": pay_tot, "暂缓金额": hold_tot,
                         "订单行": len(pos), "入库单": len(grns), "发票": len(invs), "孤立单据": len(orphan)},
        "inputs": [x for x in (fp(P(f)) for f in ["purchase_orders.csv", "goods_receipts.csv", "invoices.csv", "suppliers.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
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
