# -*- coding: utf-8 -*-
"""report-tieout-check 自检（回归测试）——仅用内置 samples/ 夹具验证脚本本身是否健康。
这不是业务运行：正式运行请把用户当期四表路径显式传给 check_report_tieout()。

用法：python self_check.py
退出码 0=自检通过，1=自检失败（脚本可能已退化）。
"""
import os, sys, json, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)              # scripts/ 的上一级 = skill 根
SAMPLES = os.path.join(SKILL_ROOT, "samples")

# 载入校验函数（同目录 v2 脚本；模块名不是 __main__，故其 if __name__ == "__main__" 段不会执行）
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_spec = importlib.util.spec_from_file_location("_self_check_entry", os.path.join(HERE, "report_tieout_check_v2.py"))
_v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_v2)
check_report_tieout = getattr(_v2, "check_report_tieout")
_spec_w = importlib.util.spec_from_file_location("_self_check_render", os.path.join(HERE, "render_worksheet.py"))
_rw = importlib.util.module_from_spec(_spec_w)
_spec_w.loader.exec_module(_rw)
render_tieout_worksheet = getattr(_rw, "render_tieout_worksheet")


def _run(ver):
    d = os.path.join(SAMPLES, ver)
    return check_report_tieout(
        os.path.join(d, "balance_sheet.csv"),
        os.path.join(d, "income_statement.csv"),
        os.path.join(d, "cash_flow.csv"),
        os.path.join(d, "retained_earnings.csv"),
    )


def self_check():
    expected = json.load(open(os.path.join(SAMPLES, "expected.json"), encoding="utf-8"))
    ok = True
    for ver in ("clean", "error"):
        got = _run(ver)
        exp = expected[ver]
        for k in ("passed", "total", "n_fail", "all_tie"):
            if got[k] != exp[k]:
                print(f"✗ [{ver}] {k}: 期望 {exp[k]} 实得 {got[k]}")
                ok = False
        if "data_issues~len" in exp and len(got.get("data_issues", [])) != exp["data_issues~len"]:
            print(f"✗ [{ver}] data_issues~len: 期望 {exp['data_issues~len']} 实得 {len(got.get('data_issues', []))}")
            ok = False
        if ver == "error" and "fail_items" in exp:
            got_items = sorted(c["勾稽项"] for c in got["fails"])
            if got_items != sorted(exp["fail_items"]):
                print(f"✗ [error] 不符项清单不一致\n  期望 {exp['fail_items']}\n  实得 {got_items}")
                ok = False
        if "fail_missing" in exp:
            # 缺数据必须挂在引用该项目的勾稽项上（含分区和），且没有别的勾稽项被错标缺数据
            got_miss = {c["勾稽项"]: c["缺数据"] for c in got["checks"] if c.get("缺数据")}
            if got_miss != exp["fail_missing"]:
                print(f"✗ [{ver}] 缺数据归属不一致\n  期望 {exp['fail_missing']}\n  实得 {got_miss}")
                ok = False
        # 底稿渲染：数据质量问题必须让结论变为「不具备对外报出条件」并列出问题项目
        html = render_tieout_worksheet(got, {})
        need = ["具备对外报出条件"] if exp["all_tie"] else ["不具备对外报出条件", "数据质量问题"] + [d["项目"] for d in got.get("data_issues", [])]
        bad = [w for w in need if w not in html] + (["不具备对外报出条件"] if exp["all_tie"] and "不具备对外报出条件" in html else [])
        if bad:
            print(f"✗ [{ver}] 底稿渲染缺少/多出: {bad}")
            ok = False
    print("自检通过 ✓ 脚本行为与黄金答案一致" if ok else "自检失败 ✗ 脚本可能已退化")
    return ok


if __name__ == "__main__":
    sys.exit(0 if self_check() else 1)
