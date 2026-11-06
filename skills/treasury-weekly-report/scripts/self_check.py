# -*- coding: utf-8 -*-
"""treasury-weekly-report 自检（回归测试）——仅用内置 samples/ 构造数据验证脚本本身是否健康。
这不是业务运行：正式运行请按 SKILL.md 把当期 5 份 Excel 路径传给 run_pipeline()。
用法：python self_check.py
退出码 0=全部通过；1=自检失败（脚本可能已退化）；2=无失败但存在 KNOWN GAP（已知未修复缺陷，见输出）。
黄金答案 samples/expected.json 由 samples/gen_samples.py 按构造推导，不由引擎生成。
"""
import os, sys, json, math, importlib.util, contextlib, io
HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
SAMPLES = os.path.join(SKILL_ROOT, "samples")
ENTRY_SCRIPT = "treasury_pipeline.py"
FILES = {"flow": "银行流水明细.xlsx", "invest": "理财台账.xlsx", "plan": "资金计划表.xlsx",
         "alert": "预警规则表.xlsx", "last_week": "上周资金汇总.xlsx"}
# 标准正态分布参考值（N(x)，保留 9 位）
NORM_REF = {0.0: 0.5, 1.0: 0.841344746, -1.0: 0.158655254, 1.96: 0.975002105, -1.96: 0.024997895}


def _load_entry():
    # 以普通模块方式载入入口脚本（模块名不是 __main__）；HERE 不在 sys.path 时插到首位。
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location("_self_check_entry", os.path.join(HERE, ENTRY_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alerts(anoms, code):
    return sorted(f"{a['date']} {a['subsidiary']} {a['amount']}" for a in anoms if a["type"] == code)


def self_check():
    eng = _load_entry()
    expected = json.load(open(os.path.join(SAMPLES, "expected.json"), encoding="utf-8"))
    fails, gaps, passed = [], [], 0

    def eq(case, name, got, exp, tol=0.005):
        nonlocal passed
        if isinstance(exp, float) and isinstance(got, (int, float)):
            ok = abs(got - exp) <= tol
        else:
            ok = got == exp
        if ok:
            passed += 1
        else:
            fails.append(f"✗ [{case}] {name}: 期望 {exp} 实得 {got}")

    for x, ref in NORM_REF.items():
        eq("norm_cdf", f"N({x})", eng.norm_cdf(x), ref, tol=1e-7)

    for case in ("clean", "error"):
        exp = expected[case]
        d = os.path.join(SAMPLES, case)
        with contextlib.redirect_stdout(io.StringIO()):
            res = eng.run_pipeline({k: os.path.join(d, v) for k, v in FILES.items()})
        core = res["core"]
        gap_keys = {g.get("key") for g in exp["known_gaps"]}
        eq(case, "data_issues", res["data_issues"], [])
        for k in ("transaction_count", "subsidiary_count", "account_count"):
            eq(case, k, res[k], exp[k])
        for k in ("actual_income", "actual_expense", "last_balance_wan"):
            eq(case, k, float(core[k]), float(exp[k]))
        eq(case, "daily_trend", [{k: (round(float(v), 2) if isinstance(v, (int, float)) else v) for k, v in r.items()}
                                 for r in core["daily_trend"]], exp["daily_trend"])
        eq(case, "income_categories", sorted(r["类别"] for r in core["income_items"]), exp["income_categories"])
        eq(case, "expense_categories", sorted(r["类别"] for r in core["expense_items"]), exp["expense_categories"])
        eq(case, "plan_deviations", sorted(r["类别"] for r in res["plan_execution"]["deviations"]), exp["plan_deviations"])
        for code in ("W003", "W004", "W005"):
            if code not in gap_keys:
                eq(case, code, _alerts(res["anomalies"], code), exp[code])
        if "W003_must_include" in exp:
            got = _alerts(res["anomalies"], "W003")
            eq(case, "W003_must_include", [x for x in exp["W003_must_include"] if x in got], exp["W003_must_include"])
        hm = res["risk"]["subsidiary_heatmap"]
        eq(case, "heatmap_shape", [len(hm), len(hm[0]["data"]) if hm else 0], exp["heatmap_shape"])

        fx = exp["fx"]
        args = (fx["S"], fx["K"], fx["T"], fx["rd"], fx["rf"], fx["sigma"])
        call, put = eng.gk_option(*args, "call"), eng.gk_option(*args, "put")
        eq(case, "fx.call(GK)", call, fx["call"], tol=1e-9)
        eq(case, "fx.put(GK)", put, fx["put"], tol=1e-9)
        eq(case, "fx.平价误差<1e-12", fx["parity_gap_abs"] < 1e-12, True)
        parity = (call - put) - (fx["S"] * math.exp(-fx["rf"] * fx["T"]) - fx["K"] * math.exp(-fx["rd"] * fx["T"]))
        eq(case, "fx.引擎平价误差<1e-12", abs(parity) < 1e-12, True)
        eq(case, "fx.IRP远期", eng.forward_rate(fx["S"], fx["rd"], fx["rf"], fx["T"]), fx["irp_forward_simple"], tol=1e-9)

        # 已知缺口：引擎当前行为与 SKILL.md 应有行为不一致（未修复，需使用方决定）
        for g in exp["known_gaps"]:
            if g["id"] == "W001_not_implemented":
                got = sorted({a["subsidiary"] for a in res["anomalies"] if a["type"] == "W001"})
            elif g["id"] == "W003_name_mismatch":
                got = _alerts(res["anomalies"], "W003")
            else:
                fails.append(f"✗ [{case}] 未知 known_gap {g['id']}")
                continue
            if got == g["expected"]:
                fails.append(f"✗ [{case}] KNOWN GAP {g['id']} 已不再复现（实得 {got}）：请更新 expected.json 与文档")
            else:
                gaps.append(f"⚠ KNOWN GAP [{case}] {g['id']}：{g['说明']}｜应有 {g['expected']}，引擎 {got}｜{g['finding']}")

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
