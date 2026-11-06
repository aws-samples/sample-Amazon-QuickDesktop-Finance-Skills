"""供应商对账 v2 · 确定性计算脚本
用法: python supplier_recon_v2.py <数据目录> [截止日 YYYY-MM-DD] [out.json]
数据目录需包含（金额口径统一：正 = 增加应付，负 = 减少应付）：
  our_ap_ledger.csv       本方应付明细（凭证号, 供应商编码, 供应商, 业务类型, 对方单据号, 日期, 金额）
  supplier_statements.csv 供应商对账单明细（供应商编码, 单据号, 业务类型, 日期, 金额）
  our_balances.csv        本方余额表（供应商编码, 期初余额, 期末余额）——对账范围以此为准
  statement_summary.csv   对账单汇总（供应商编码, 期初余额, 本期增加, 本期减少, 期末余额）——供方自报
  supplier_master.csv     供应商主数据（供应商编码, 供应商）

判据：
  ① 范围完备   本方有余额的供应商都必须回函；未回函单列（余额未经确认）
  ② 期初一致   本方期初 == 供方期初（上期差异是否结清）
  ③ 余额恒等式 本方：期初 + Σ明细 == 余额表期末；供方：期初 + 增加 − 减少 == 自报期末，且增加/减少 == 明细合计
  ④ 逐笔匹配   按「供应商 + 归一化单据号」匹配；同一方同号多次 → 重复入账
  ⑤ 单号纠错   未匹配项中同供应商、金额相同、日期相差 ≤ 3 天 → 疑似单号录错（配对后待核实）
  ⑥ 交叉一致   配对项：金额相反 → 方向错误；金额不等 → 金额不符；业务类型不同 → 类型不一致
  ⑦ 时间性     单边项日期在截止日前 TIMING_DAYS 天内 → 在途（未达账，下期核销），其余单边为真差异
差额闭合（逐供应商、再合计）：
  供方自报期末 − 本方余额表期末 = 期初差 + 供方独有 − 本方独有 + 配对金额差 + 供方汇总差 − 本方余额表差
  并分向列示毛额（正向差异、负向差异分别合计），防止净额掩盖。
可回溯：输入 SHA-256 指纹 + 截止日 + 时间戳；逐笔匹配方式、双方金额、差异分类。
"""
import sys, os, csv, json, hashlib, re, unicodedata, math
from collections import defaultdict, Counter
from datetime import date, datetime

CUTOFF_DEFAULT = "2026-09-30"
TIMING_DAYS = 5
TOL = 0.5


# 严格数值解析（fail-closed）：NUMERIC 列出各输入文件中按数值使用的字段。值无法解析（如千分位
# "1,234.00"、货币符号、全角数字、nan/inf）或在不允许留空的字段留空 → 登记为数据质量问题
# （文件、行号、字段、原值），该行不参与任何计算（不按 0 计），本次运行 all_ok=False。
# BLANK_OK 中的字段允许留空，留空按 0 计。
NUMERIC = {
    "our_ap_ledger.csv": ("金额",),
    "supplier_statements.csv": ("金额",),
    "our_balances.csv": ("期初余额", "期末余额"),
    "statement_summary.csv": ("期初余额", "本期增加", "本期减少", "期末余额"),
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


def norm(s):
    s = unicodedata.normalize("NFKC", str(s)).upper()
    s = re.sub(r"\s+", "", s)
    return re.sub(r"(?<!\d)0+(?=\d)", "", s)


def check(data_dir, cutoff=CUTOFF_DEFAULT, timing_days=TIMING_DAYS):
    DQ.clear()
    P = lambda f: os.path.join(data_dir, f)
    CUT = date.fromisoformat(cutoff)
    our = load(P("our_ap_ledger.csv"))
    sup = load(P("supplier_statements.csv"))
    # 同一供应商编码只取首行；重复行不静默覆盖，计入该供应商问题
    obal_rows = load(P("our_balances.csv"))
    ssum_rows = load(P("statement_summary.csv"))
    obal, ssum = {}, {}
    for r in obal_rows:
        obal.setdefault(r["供应商编码"], r)
    for r in ssum_rows:
        ssum.setdefault(r["供应商编码"], r)
    obal_dup = {k: n for k, n in Counter(r["供应商编码"] for r in obal_rows).items() if n > 1}
    ssum_dup = {k: n for k, n in Counter(r["供应商编码"] for r in ssum_rows).items() if n > 1}
    names = {r["供应商编码"]: r["供应商"] for r in load(P("supplier_master.csv"))}
    for r in our:
        r["金额"] = num(r["金额"])
        r["_k"] = norm(r["对方单据号"])
    for r in sup:
        r["金额"] = num(r["金额"])
        r["_k"] = norm(r["单据号"])
    items = []
    sup_rows = []
    for sid in obal:
        name = names.get(sid, sid)
        O = [r for r in our if r["供应商编码"] == sid]
        S = [r for r in sup if r["供应商编码"] == sid]
        ob_open, ob_end = num(obal[sid]["期初余额"]), num(obal[sid]["期末余额"])
        o_calc = round(ob_open + sum(r["金额"] for r in O), 2)
        if sid not in ssum:
            sup_rows.append({"供应商编码": sid, "供应商": name, "状态": "未回函", "本方期末": ob_end, "供方期末": None,
                             "差额": None, "期初差": 0, "供方独有": 0, "本方独有": 0, "配对金额差": 0, "供方汇总差": 0,
                             "本方余额表差": round(ob_end - o_calc, 2), "在途净额": 0, "正向毛额": 0, "负向毛额": 0,
                             "问题": ["供应商未回函(余额未经确认)"] + (["本方余额表与明细不符"] if abs(ob_end - o_calc) > TOL else [])
                                     + ([f"本方余额表重复({obal_dup[sid]}行)"] if sid in obal_dup else [])})
            continue
        sm = ssum[sid]
        s_open, s_inc, s_dec, s_end = (num(sm[k]) for k in ("期初余额", "本期增加", "本期减少", "期末余额"))
        s_calc = round(s_open + sum(r["金额"] for r in S), 2)
        sup_issue = []
        if sid in obal_dup:
            sup_issue.append(f"本方余额表重复({obal_dup[sid]}行)")
        if sid in ssum_dup:
            sup_issue.append(f"供方汇总重复({ssum_dup[sid]}行)")
        if abs(ob_open - s_open) > TOL:
            sup_issue.append(f"期初不一致(本方{ob_open:,.2f}/供方{s_open:,.2f})")
        if abs(ob_end - o_calc) > TOL:
            sup_issue.append(f"本方余额表与明细不符(差{ob_end - o_calc:+,.2f})")
        if abs(s_open + s_inc - s_dec - s_end) > TOL or abs(s_end - s_calc) > TOL:
            sup_issue.append(f"供方期末与其明细不符(差{s_end - s_calc:+,.2f})")
        inc_d = round(sum(r["金额"] for r in S if r["金额"] > 0), 2)
        dec_d = round(-sum(r["金额"] for r in S if r["金额"] < 0), 2)
        if abs(inc_d - s_inc) > TOL or abs(dec_d - s_dec) > TOL:
            sup_issue.append("供方汇总与明细发生额不符")
        # ④ 逐笔匹配
        og, sg = defaultdict(list), defaultdict(list)
        for r in O:
            og[r["_k"]].append(r)
        for r in S:
            sg[r["_k"]].append(r)
        pairs, o_left, s_left = [], [], []
        # 按首次出现顺序遍历（本方在前、供方补充），不用 set 并集，避免结果随 PYTHONHASHSEED 变化
        for k in list(og) + [k for k in sg if k not in og]:
            a, b = og.get(k, []), sg.get(k, [])
            n = min(len(a), len(b))
            for i in range(n):
                pairs.append((a[i], b[i], "单号匹配"))
            for x in a[n:]:
                x["_dup"] = len(a) > 1
                o_left.append(x)
            for x in b[n:]:
                x["_dup"] = len(b) > 1
                s_left.append(x)
        # ⑤ 单号纠错
        for x in list(o_left):
            if x.get("_dup"):
                continue
            cand = [y for y in s_left if not y.get("_dup") and abs(y["金额"] - x["金额"]) <= TOL
                    and abs((date.fromisoformat(y["日期"]) - date.fromisoformat(x["日期"])).days) <= 3]
            if cand:
                y = cand[0]
                pairs.append((x, y, "疑似单号录错"))
                o_left.remove(x)
                s_left.remove(y)
        a_open = round(s_open - ob_open, 2)
        a_sonly = a_oonly = a_pair = t_net = 0.0
        pos = neg = 0.0

        def add(item):
            items.append(item)

        for o, s, how in pairs:
            d = round(s["金额"] - o["金额"], 2)
            iss, st = [], "matched"
            if how == "疑似单号录错":
                iss.append(f"疑似单号录错(本方{o['对方单据号']}/供方{s['单据号']})")
                st = "review"
            if abs(d) > TOL:
                if abs(o["金额"] + s["金额"]) <= TOL:
                    iss.append(f"方向错误(本方{o['金额']:,.2f}/供方{s['金额']:,.2f})")
                else:
                    iss.append(f"金额不符(本方{o['金额']:,.2f}/供方{s['金额']:,.2f})")
                st = "exception"
            if o["业务类型"] != s["业务类型"]:
                iss.append(f"业务类型不一致(本方{o['业务类型']}/供方{s['业务类型']})")
                if st == "matched":
                    st = "difference"
            a_pair += d
            pos += max(d, 0)
            neg += max(-d, 0)
            add({"供应商编码": sid, "供应商": name, "单据号": s["单据号"], "本方凭证": o["凭证号"], "业务类型": s["业务类型"],
                 "本方日期": o["日期"], "供方日期": s["日期"], "本方金额": o["金额"], "供方金额": s["金额"], "差额": d,
                 "匹配方式": how, "status": st, "issues": iss or ["一致"]})
        for side, lst in (("本方", o_left), ("供方", s_left)):
            for x in lst:
                d = x["金额"] if side == "供方" else -x["金额"]
                dt = date.fromisoformat(x["日期"])
                if x.get("_dup"):
                    iss, st = [f"{side}重复入账(同一单号出现多次)"], "exception"
                elif 0 <= (CUT - dt).days < timing_days:
                    iss, st = [f"在途·{side}已记对方未记({x['日期']}，下期核销)"], "timing"
                    t_net += d
                else:
                    iss, st = [f"{'供方独有(本方漏记)' if side == '供方' else '本方独有(供方漏记)'}"], "exception"
                if side == "供方":
                    a_sonly += x["金额"]
                else:
                    a_oonly += x["金额"]
                pos += max(d, 0)
                neg += max(-d, 0)
                add({"供应商编码": sid, "供应商": name, "单据号": x.get("单据号") or x.get("对方单据号"), "本方凭证": x.get("凭证号", "—"),
                     "业务类型": x["业务类型"], "本方日期": x["日期"] if side == "本方" else "—", "供方日期": x["日期"] if side == "供方" else "—",
                     "本方金额": x["金额"] if side == "本方" else None, "供方金额": x["金额"] if side == "供方" else None, "差额": round(d, 2),
                     "匹配方式": f"{side}单边", "status": st, "issues": iss})
        a_ssum = round(s_end - s_calc, 2)
        a_obal = round(ob_end - o_calc, 2)
        gap = round(s_end - ob_end, 2)
        attributed = round(a_open + a_sonly - a_oonly + a_pair + a_ssum - a_obal, 2)
        assert abs(attributed - gap) < 0.05, f"{sid} 差额不闭合: 归因{attributed} vs 差额{gap}"
        n_bad = sum(1 for it in items if it["供应商编码"] == sid and it["status"] in ("exception", "difference", "review"))
        state = "不一致" if (n_bad or sup_issue) else ("一致(仅在途)" if abs(t_net) > TOL else "一致")
        sup_rows.append({"供应商编码": sid, "供应商": name, "状态": state, "本方期末": ob_end, "供方期末": s_end, "差额": gap,
                         "期初差": a_open, "供方独有": round(a_sonly, 2), "本方独有": round(a_oonly, 2), "配对金额差": round(a_pair, 2),
                         "供方汇总差": a_ssum, "本方余额表差": a_obal, "在途净额": round(t_net, 2), "正向毛额": round(pos, 2), "负向毛额": round(neg, 2),
                         "问题": sup_issue or (["仅在途差异"] if abs(t_net) > TOL else ["余额一致"])})
    cnt = Counter(i["status"] for i in items)
    assert sum(cnt.values()) == len(items), "笔数守恒失败"
    replied = [s for s in sup_rows if s["状态"] != "未回函"]
    tot = lambda k: round(sum(s[k] for s in replied), 2)
    issue_types = Counter(re.sub(r"[(·].*", "", m) for i in items for m in i["issues"])
    for s in sup_rows:
        for m in s["问题"]:
            if m not in ("余额一致", "仅在途差异"):
                issue_types[re.sub(r"\(.*", "", m)] += 1
    real = [i for i in items if i["status"] != "timing"]
    for d in DQ:
        issue_types["数据质量·" + d["问题"]] += 1
    # 总判定：无数据质量问题，且问题标签只有「一致」「在途」（在途为正常的未达账）
    all_ok = not DQ and not (set(issue_types) - {"一致", "在途"})
    return {
        "cutoff": cutoff, "summary": dict(cnt), "total": len(items), "all_ok": all_ok, "data_issues": list(DQ),
        "match_rate": round(cnt.get("matched", 0) / max(len(real), 1) * 100, 1),
        "supplier_summary": dict(Counter(s["状态"] for s in sup_rows)),
        "issue_types": dict(issue_types),
        "suppliers": sup_rows,
        "conservation": {"本方期末合计(已回函)": tot("本方期末"), "供方期末合计": tot("供方期末"), "差额合计": tot("差额"),
                         "期初差": tot("期初差"), "供方独有": tot("供方独有"), "本方独有": tot("本方独有"), "配对金额差": tot("配对金额差"),
                         "供方汇总差": tot("供方汇总差"), "本方余额表差": tot("本方余额表差"), "其中在途净额": tot("在途净额"),
                         "正向毛额": tot("正向毛额"), "负向毛额": tot("负向毛额"),
                         "未回函余额": round(sum(s["本方期末"] for s in sup_rows if s["状态"] == "未回函"), 2),
                         "本方明细笔数": len(our), "供方明细笔数": len(sup)},
        "inputs": [x for x in (fp(P(f)) for f in ["our_ap_ledger.csv", "supplier_statements.csv", "our_balances.csv", "statement_summary.csv", "supplier_master.csv"]) if x],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": items,
    }


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
