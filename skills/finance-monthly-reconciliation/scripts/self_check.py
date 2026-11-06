# -*- coding: utf-8 -*-
"""finance-monthly-reconciliation 自检（回归测试）——仅用内置 samples/ 构造数据验证脚本本身是否健康。
这不是业务运行：正式运行请按 SKILL.md 把当期 ERP 数据包与回单路径传给 run_full_pipeline()。
用法：python self_check.py
退出码 0=全部通过；1=自检失败（脚本可能已退化）；2=无失败但存在 KNOWN GAP（已知未修复缺陷，见输出）。
黄金答案 samples/expected.json 由 samples/gen_samples.py 按构造推导，不由引擎生成。
"""
import os, sys, json, csv, tempfile, importlib.util, contextlib, io
HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
SAMPLES = os.path.join(SKILL_ROOT, "samples")
ENTRY_SCRIPT = "reconciliation_engine.py"


def _load_entry():
    # 以普通模块方式载入入口脚本（模块名不是 __main__）；HERE 不在 sys.path 时插到首位。
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location("_self_check_entry", os.path.join(HERE, ENTRY_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def render_dashboard(data):
    """按 SKILL.md Step 4 的注入规则填充模板：json.dumps(ensure_ascii=False) 后把 < 替换为 \\u003c。"""
    tpl = open(os.path.join(HERE, "dashboard_template.html"), encoding="utf-8").read()
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("<", "\\u003c")
    return tpl.replace("{{DATA_JSON}}", payload)


def self_check():
    eng = _load_entry()
    expected = json.load(open(os.path.join(SAMPLES, "expected.json"), encoding="utf-8"))
    fails, gaps, passed = [], [], 0

    def eq(case, name, got, exp):
        nonlocal passed
        if isinstance(exp, float) and isinstance(got, (int, float)) and abs(got - exp) <= 0.005:
            passed += 1
        elif got == exp:
            passed += 1
        else:
            fails.append(f"✗ [{case}] {name}: 期望 {exp} 实得 {got}")

    for case in ("clean", "error"):
        exp = expected[case]
        d = os.path.join(SAMPLES, case)
        # 回单来源.csv：模拟邮件发件人与 file_read_pdf 读出的正文（正文留空 = 未提供）
        src = {r["文件名"]: r for r in csv.DictReader(open(os.path.join(d, "回单", "回单来源.csv"), encoding="utf-8"))}
        receipts = [{"path": os.path.join(d, "回单", f), "sender": src[f]["发件人"], "text": src[f]["正文"] or None}
                    for f in sorted(os.listdir(os.path.join(d, "回单"))) if f.endswith(".pdf")]
        trusted = expected["trusted_senders"]
        with tempfile.TemporaryDirectory() as out, contextlib.redirect_stdout(io.StringIO()):
            res = eng.run_full_pipeline(os.path.join(d, "财务月末数据包.xlsx"), receipts, out, trusted_senders=trusted)
            res_no_receipts = eng.run_full_pipeline(os.path.join(d, "财务月末数据包.xlsx"), None, out)
            parsed = eng.parse_bank_receipts(receipts, trusted)
            parsed_no_list = eng.parse_bank_receipts(receipts)   # 不传受信任清单 = 默认拒绝
            xlsx = os.path.join(out, "对账结果.xlsx")
            import openpyxl
            wb = openpyxl.load_workbook(xlsx)
            cells = [c for ws in wb.worksheets for row in ws.iter_rows() for c in row]
        rr, dash = res["recon_result"], res["dashboard_data"]
        st = rr["stats"]
        for k, v in exp["stats"].items():
            eq(case, f"stats.{k}", st.get(k), v)
        got_pairs = sorted([r["ERP凭证号"], r["银行流水号"]] for r in rr["matched_df"].to_dict("records"))
        eq(case, "matched_pairs", got_pairs, exp["matched_pairs"])
        eq(case, "erp_only_ids", sorted(rr["erp_only_df"]["凭证号"].tolist()), exp["erp_only_ids"])
        eq(case, "bank_only_ids", sorted(rr["bank_only_df"]["交易流水号"].tolist()), exp["bank_only_ids"])
        for k, v in exp["kpi"].items():
            eq(case, f"kpi.{k}", dash["kpi"].get(k), v)
        eq(case, "ar_aging", dash["ar_aging"], exp["ar_aging"])
        eq(case, "ap_aging", dash["ap_aging"], exp["ap_aging"])
        eq(case, "receipts(文件名解析)", [{"日期": r["日期"], "收款方": r["收款方"], "金额": r["金额"]}
                                       for r in parsed.sort_values("文件名").to_dict("records")], exp["receipts"])
        # 回单来源核验与正文核对：不受信任发件人、正文不符的回单都不作为对账凭证
        for k, v in exp["receipt_check"].items():
            eq(case, f"receipt_check.{k}", res.get("receipt_check", {}).get(k), v)
        eq(case, "未传受信任清单时无回单可作凭证", int(parsed_no_list["可作凭证"].sum()), 0)
        # 文件名金额为 nan / inf / 0 时不能作凭证（NaN 比较恒为 False，会绕过金额核对）
        with contextlib.redirect_stdout(io.StringIO()):
            odd = eng.parse_bank_receipts([{"path": f"银行回单_2026-08-03_示例供应商A_{v}元.pdf", "sender": trusted[0], "text": t}
                                           for v in ("nan", "inf", "0") for t in (None, "交易日期：2026-08-03\n交易金额：¥52,000.00")],
                                          trusted)
        eq(case, "金额 nan/inf/0 的回单不可作凭证", int(odd["可作凭证"].sum()), 0)

        # 仪表盘注入：外部文本只能以 JSON 字符串形式出现，且模板只用 textContent 写 DOM
        html = render_dashboard(dash)
        script = html.split("const DASHBOARD_DATA = ", 1)[1]
        for p in expected["xss_payloads"]:
            if p in html:
                fails.append(f"✗ [{case}] 仪表盘 HTML 含未转义载荷 {p!r}")
            else:
                passed += 1
        if "</script>" in script.split(";\n", 1)[0] or "innerHTML" in html or "insertAdjacentHTML" in html:
            fails.append(f"✗ [{case}] 仪表盘数据段可闭合 <script> 或模板仍用 innerHTML")
        else:
            passed += 1
        # Excel 公式注入：以 = 开头的文本按字符串写出（前置单引号），不得成为公式
        formula_cells = [c for c in cells if c.data_type == "f"]
        quoted = [c for c in cells if isinstance(c.value, str) and c.value == "'" + expected["formula_payload"]]
        if formula_cells:
            fails.append(f"✗ [{case}] 对账结果.xlsx 出现公式单元格 {[c.coordinate for c in formula_cells]}")
        else:
            passed += 1
        if case == "error":
            eq(case, "公式载荷已加单引号", len(quoted), 1)

        # 已知缺口：引擎当前行为与 SKILL.md 应有行为不一致（未修复，需使用方决定）
        for g in exp["known_gaps"]:
            if g["id"] == "match_rate_denominator":
                got = st["match_rate"]
                ok = abs(got - g["expected"]) <= 0.05
            elif g["id"] == "halt_gate":
                got = st.get("gate")
                ok = got == g["expected"]
            elif g["id"] == "receipts_unused":
                got = "结果相同" if rr["stats"] == res_no_receipts["recon_result"]["stats"] else "结果不同"
                ok = got == "结果不同"
            else:
                fails.append(f"✗ [{case}] 未知 known_gap {g['id']}")
                continue
            if ok:
                fails.append(f"✗ [{case}] KNOWN GAP {g['id']} 已不再复现（期望 {g['expected']} 实得 {got}）：请更新 expected.json 与文档")
            else:
                gaps.append(f"⚠ KNOWN GAP [{case}] {g['id']}：{g['说明']}｜期望 {g['expected']}，引擎 {got}｜{g['finding']}")

    for line in fails + gaps:
        print(line)
    if fails:
        print(f"自检失败 ✗ 脚本可能已退化（通过 {passed} 项，失败 {len(fails)} 项，KNOWN GAP {len(gaps)} 项）")
        return 1
    if gaps:
        print(f"自检未完全通过：{passed} 项与黄金答案一致，{len(gaps)} 项为 KNOWN GAP（已知未修复缺陷，见上）")
        return 2
    print(f"自检通过 ✓ 脚本行为与黄金答案一致（{passed} 项）")
    return 0


if __name__ == "__main__":
    sys.exit(self_check())
