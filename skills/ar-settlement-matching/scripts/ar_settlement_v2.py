"""收款核销匹配 v2 · 确定性计算脚本
用法: python ar_settlement_v2.py <数据目录> [截止日 YYYY-MM-DD] [out.json]
数据目录需包含：
  ar_invoices.csv     应收发票（发票号, 客户编码, 开票日期, 应收金额, 状态[正常/作废]）
  bank_credits.csv    银行到账流水（银行流水号, 入账日期, 付款账户, 付款户名, 金额, 摘要）——外部锚点
  receipts.csv        收款单（收款单号, 银行流水号, 客户编码, 收款日期, 收款金额）
  allocations.csv     核销关系（核销单号, 收款单号, 发票号, 核销金额, 核销日期）
  payer_accounts.csv  客户付款账户白名单（客户编码, 付款账户, 付款户名）
  customer_master.csv 客户主数据（客户编码, 客户）
  gl_balance.csv      总账（科目, 期末余额）：1122 应收账款、2203 合同负债(预收)

判据：
  ① 收款 ↔ 银行（外部锚点） 每张收款单必须对应一笔银行到账；金额相等；收款日期不早于入账日期、相差 ≤ 3 天
                             银行到账没有收款单 → 到账未入账；同一银行流水被两张收款单引用 → 重复入账
                             银行流水号在 bank_credits.csv 中出现多行 → 银行流水号重复，且每一行都要通过本条与②的校验
                             （不按「后一行覆盖前一行」取值）；发票号重复同样报出
  ② 付款人归属            银行付款账户 ∈ 该客户的付款账户白名单（抓“收款挂错客户”）
  ③ 核销准入              核销引用的收款单、发票必须存在；发票状态为正常；收款客户 == 发票客户；同一(收款,发票)只核销一次
  ④ 边界                  每张收款的核销合计 ≤ 收款金额（余额挂预收）；每张发票的核销合计 ≤ 应收金额
守恒：
  · 核销三方守恒：Σ核销 == Σ发票已核销 == Σ收款已核销
  · 收款守恒：Σ收款 == Σ已核销 + Σ未核销余额(预收)
  · 应收闭环：Σ正常发票应收 − Σ有效核销 == 期末应收，对 1122 总账；未核销收款余额对 2203
可回溯：输入 SHA-256 指纹 + 截止日 + 时间戳；逐笔收款、发票、核销的状态与原因。
"""
import sys, os, csv, json, hashlib, re, math
from collections import defaultdict, Counter
from datetime import date, datetime

CUTOFF_DEFAULT = "2026-09-30"
TOL = 0.5
DATE_TOL = 3


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "ar_invoices.csv": ("应收金额",),
    "bank_credits.csv": ("金额",),
    "receipts.csv": ("收款金额",),
    "allocations.csv": ("核销金额",),
    "gl_balance.csv": ("期末余额",),
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


def check(data_dir, cutoff=CUTOFF_DEFAULT):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    # 主键唯一性：同一发票号 / 银行流水号出现多行时不静默覆盖——发票保留首行并报「发票号重复」，
    # 银行流水按号保留全部行，引用该号的收款对每一行都做锚点校验，并报「银行流水号重复」
    inv_rows = load(P("ar_invoices.csv"))
    inv_n = Counter(r["发票号"] for r in inv_rows)
    inv = {}
    for r in inv_rows:
        inv.setdefault(r["发票号"], r)
    bank_rows = load(P("bank_credits.csv"))
    bank_all = defaultdict(list)
    for r in bank_rows:
        bank_all[r["银行流水号"]].append(r)
    bank = {k: v[0] for k, v in bank_all.items()}
    recs = load(P("receipts.csv"))
    als = load(P("allocations.csv"))
    acc = {r["付款账户"]: r["客户编码"] for r in load(P("payer_accounts.csv"))}
    names = {r["客户编码"]: r["客户"] for r in load(P("customer_master.csv"))}
    gl = {r["科目"][:4]: num(r["期末余额"]) for r in load(P("gl_balance.csv"))}
    for r in inv.values():
        r["应收金额"] = num(r["应收金额"])
    for r in recs:
        r["收款金额"] = num(r["收款金额"])
    for a in als:
        a["核销金额"] = num(a["核销金额"])
    rec_by = {r["收款单号"]: r for r in recs}
    bank_used = Counter(r["银行流水号"] for r in recs)

    # ③ 核销准入
    seen_pair = set()
    alloc_res = []
    for a in als:
        why = []
        r = rec_by.get(a["收款单号"])
        i = inv.get(a["发票号"])
        if not r:
            why.append(f"收款单不存在({a['收款单号']})")
        if not i:
            why.append(f"发票不存在({a['发票号']})")
        elif i.get("状态", "正常") != "正常":
            why.append(f"核销已{i['状态']}发票")
        if r and i and r["客户编码"] != i["客户编码"]:
            why.append(f"跨客户核销(收款{names.get(r['客户编码'], r['客户编码'])}/发票{names.get(i['客户编码'], i['客户编码'])})")
        key = (a["收款单号"], a["发票号"])
        if key in seen_pair:
            why.append("重复核销(同一收款对同一发票第二次核销)")
        seen_pair.add(key)
        alloc_res.append({"核销单号": a["核销单号"], "收款单号": a["收款单号"], "发票号": a["发票号"], "核销金额": a["核销金额"],
                          "有效": not why, "原因": why or ["有效"]})
    valid = [x for x in alloc_res if x["有效"]]
    r_alloc, i_alloc = defaultdict(float), defaultdict(float)
    for x in valid:
        r_alloc[x["收款单号"]] += x["核销金额"]
        i_alloc[x["发票号"]] += x["核销金额"]
    r_alloc_all = defaultdict(float)
    for x in alloc_res:
        r_alloc_all[x["收款单号"]] += x["核销金额"]
    bad_alloc_by_rec = defaultdict(list)
    for x in alloc_res:
        if not x["有效"]:
            bad_alloc_by_rec[x["收款单号"]].append(x)

    # ①② 收款 ↔ 银行、付款人归属；④ 收款边界
    rec_res = []
    for r in recs:
        issues, st = [], "pass"

        def bad(m):
            nonlocal st
            issues.append(m)
            st = "fail"

        b = bank.get(r["银行流水号"])
        cands = bank_all.get(r["银行流水号"], [])
        if not b:
            bad(f"收款无银行流水({r['银行流水号']})")
        if len(cands) > 1:
            bad(f"银行流水号重复({r['银行流水号']}共{len(cands)}行)")
        for bb in cands:      # 每一行银行流水都要通过锚点校验，任何一行不符即报出
            if abs(num(bb["金额"]) - r["收款金额"]) > TOL:
                bad(f"收款金额与银行不符(收款{r['收款金额']:,.2f}/银行{num(bb['金额']):,.2f})")
            dr, db = date.fromisoformat(r["收款日期"]), date.fromisoformat(bb["入账日期"])
            if dr < db:
                bad(f"收款早于银行到账(收款{r['收款日期']}/到账{bb['入账日期']})")
            elif (dr - db).days > DATE_TOL:
                bad(f"收款入账滞后({(dr - db).days}天)")
            owner = acc.get(bb["付款账户"])
            if owner is None:
                bad(f"付款账户未登记({bb['付款户名']})")
            elif owner != r["客户编码"]:
                bad(f"收款挂错客户(付款人{bb['付款户名']}/记在{names.get(r['客户编码'], r['客户编码'])})")
        if bank_used[r["银行流水号"]] > 1:
            bad("同一银行流水被多张收款单引用")
        if r["客户编码"] not in names:
            bad(f"客户不在主数据({r['客户编码']})")
        if r_alloc_all[r["收款单号"]] > r["收款金额"] + TOL:
            bad(f"核销超收款(核销{r_alloc_all[r['收款单号']]:,.2f}>收款{r['收款金额']:,.2f})")
        for x in bad_alloc_by_rec.get(r["收款单号"], []):
            bad("含无效核销(" + "；".join(x["原因"]) + ")")
        applied = round(r_alloc[r["收款单号"]], 2)
        unapplied = round(r["收款金额"] - applied, 2)
        rec_res.append({"收款单号": r["收款单号"], "客户编码": r["客户编码"], "客户": names.get(r["客户编码"], r["客户编码"]),
                        "收款日期": r["收款日期"], "银行流水号": r["银行流水号"], "付款户名": b["付款户名"] if b else "—",
                        "收款金额": r["收款金额"], "有效核销": applied, "未核销余额": unapplied,
                        "核销张数": sum(1 for x in valid if x["收款单号"] == r["收款单号"]),
                        "status": st, "issues": issues or (["全额核销"] if abs(unapplied) <= TOL else [f"余额挂预收({unapplied:,.2f})"])})
    # 银行到账未入账
    unbooked = []
    for bn, b in bank.items():
        if bn not in bank_used and b["入账日期"] <= cutoff:
            unbooked.append({"银行流水号": bn, "入账日期": b["入账日期"], "付款户名": b["付款户名"], "金额": num(b["金额"]),
                             "推定客户": names.get(acc.get(b["付款账户"]), "未知")})
    # 发票
    inv_res = []
    for fn, i in inv.items():
        amt = i["应收金额"]
        s = round(i_alloc.get(fn, 0.0), 2)
        issues, st = [], "pass"
        if inv_n[fn] > 1:
            issues.append(f"发票号重复({fn}共{inv_n[fn]}行，按首行计)")
            st = "fail"
        if i.get("状态", "正常") != "正常":
            st_txt = "作废"
            if any(x["发票号"] == fn for x in alloc_res):
                issues.append("作废发票被核销")
                st = "fail"
        if s > amt + TOL:
            issues.append(f"超额核销(核销{s:,.2f}>应收{amt:,.2f})")
            st = "fail"
        state = "作废" if i.get("状态", "正常") != "正常" else ("已结清" if abs(s - amt) <= TOL else ("部分核销" if s > TOL else "未收"))
        inv_res.append({"发票号": fn, "客户编码": i["客户编码"], "客户": names.get(i["客户编码"], i["客户编码"]), "开票日期": i["开票日期"],
                        "应收金额": amt, "有效核销": s, "未收余额": round(amt - s, 2) if state != "作废" else 0.0, "核销状态": state,
                        "status": st, "issues": issues or [state]})
    # 守恒
    tv = round(sum(x["核销金额"] for x in valid), 2)
    assert abs(tv - sum(i_alloc.values())) < 0.05 and abs(tv - sum(r_alloc.values())) < 0.05, "核销三方守恒失败"
    tr = round(sum(r["收款金额"] for r in recs), 2)
    tun = round(sum(x["未核销余额"] for x in rec_res), 2)
    assert abs(tr - tv - tun) < 0.05, "收款守恒失败"
    normal_ar = round(sum(i["应收金额"] for i in inv.values() if i.get("状态", "正常") == "正常"), 2)
    tv_normal = round(sum(x["核销金额"] for x in valid if inv[x["发票号"]].get("状态", "正常") == "正常"), 2)
    end_ar = round(normal_ar - tv_normal, 2)
    assert abs(end_ar - sum(x["未收余额"] for x in inv_res if x["核销状态"] != "作废")) < 0.05, "应收闭环失败"
    gl_issues = []
    if "1122" in gl and abs(end_ar - gl["1122"]) > TOL:
        gl_issues.append(f"期末应收与1122总账不符(核销结果{end_ar:,.2f}/总账{gl['1122']:,.2f})")
    pre = round(sum(max(x["未核销余额"], 0) for x in rec_res), 2)
    if "2203" in gl and abs(pre - gl["2203"]) > TOL:
        gl_issues.append(f"未核销收款与2203总账不符(核销结果{pre:,.2f}/总账{gl['2203']:,.2f})")
    rc, ic = Counter(x["status"] for x in rec_res), Counter(x["核销状态"] for x in inv_res)
    issue_types = Counter()
    for x in rec_res:
        for m in x["issues"]:
            if x["status"] == "fail":
                issue_types[re.sub(r"\(.*", "", m)] += 1
    for x in inv_res:
        if x["status"] == "fail":
            for m in x["issues"]:
                issue_types["发票·" + re.sub(r"\(.*", "", m)] += 1
    if unbooked:
        issue_types["银行到账未入账"] = len(unbooked)
    for g in gl_issues:
        issue_types[re.sub(r"\(.*", "", g)] += 1
    dup_bank = sorted(k for k, v in bank_all.items() if len(v) > 1)
    dup_inv = sorted(k for k, c in inv_n.items() if c > 1)
    for k in dup_bank:
        if k not in bank_used:          # 没有收款引用的重复流水号也要报出
            issue_types["银行流水号重复"] += 1
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且没有任何问题标签
    all_ok = not DQ and not issue_types
    return {"cutoff": cutoff, "rec_summary": dict(rc), "inv_summary": dict(ic), "all_ok": all_ok, "data_issues": list(DQ),
            "duplicate_keys": {"银行流水号": dup_bank, "发票号": dup_inv},
            "alloc_summary": {"有效": len(valid), "无效": len(alloc_res) - len(valid)},
            "pass_rate": round(rc.get("pass", 0) / max(len(rec_res), 1) * 100, 1),
            "issue_types": dict(issue_types), "gl_issues": gl_issues, "unbooked_bank": unbooked,
            "conservation": {"收款合计": tr, "有效核销合计": tv, "未核销余额合计": tun, "正常发票应收合计": normal_ar,
                             "期末应收(核销结果)": end_ar, "1122总账": gl.get("1122"), "未核销收款(预收)": pre, "2203总账": gl.get("2203"),
                             # 到账合计每个银行流水号只计首行；重复流水号的其余行另列，不重复计入到账
                             "银行到账合计": round(sum(num(b["金额"]) for b in bank.values()), 2),
                             "银行流水号重复行合计": round(sum(num(b["金额"]) for v in bank_all.values() for b in v[1:]), 2),
                             "银行到账未入账合计": round(sum(u["金额"] for u in unbooked), 2),
                             "收款笔数": len(recs), "发票张数": len(inv), "核销笔数": len(als)},
            "inputs": [x for x in (fp(P(f)) for f in ["ar_invoices.csv", "bank_credits.csv", "receipts.csv", "allocations.csv", "payer_accounts.csv", "customer_master.csv", "gl_balance.csv"]) if x],
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "receipts": rec_res, "invoices": inv_res, "allocations": alloc_res}


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv)
        cut = argv[2] if len(argv) > 2 and re.match(r"\d{4}-\d{2}-\d{2}$", argv[2]) else CUTOFF_DEFAULT
        out = check(argv[1], cut)
        dest = argv[-1] if argv[-1].endswith(".json") else "analysis.json"
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", {"cutoff": cut}, out.get("inputs", []))
    except og.OutputPathError as e:
        og.reject(e)
