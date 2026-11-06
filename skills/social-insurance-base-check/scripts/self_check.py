# -*- coding: utf-8 -*-
"""social-insurance-base-check 自检（回归测试）——仅用内置 samples/ 夹具验证脚本本身是否健康。
这不是业务运行：正式运行请把用户当期数据路径显式传给校验函数。

用法：python self_check.py
退出码 0=自检通过，1=自检失败（脚本可能已退化）。
"""
import os, sys, json, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)
SAMPLES = os.path.join(SKILL_ROOT, "samples")

ENTRY_SCRIPT = "social_insurance_check_v2.py"
ENTRY_FUNC = "check"
CALL_MODE = "dir"          # dir | files
FILES = []                      # 仅 files 模式用；按位置传参

LEN = "~len"; KEYS = "~keys"


def _load_entry():
    # 以普通模块方式载入入口脚本（模块名不是 __main__，故其 if __name__ == "__main__" 段不会执行）；
    # HERE 不在 sys.path 时插到首位，入口脚本若 import 同目录 blindspot_* 模块也能找到。
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location("_self_check_entry", os.path.join(HERE, ENTRY_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, ENTRY_FUNC)


def _call(fn, ver_dir):
    if CALL_MODE == "files":
        return fn(*[os.path.join(ver_dir, f) for f in FILES])
    return fn(ver_dir)


def _snap(d):
    out = {}
    for k, v in d.items():
        if "timestamp" in k.lower():
            continue
        if isinstance(v, bool):
            out[k] = v
        elif isinstance(v, int):
            out[k] = v
        elif isinstance(v, float):
            out[k] = round(v, 2)
        elif isinstance(v, str):
            if len(v) >= 4 and v[:4].isdigit() and "T" in v and ":" in v:
                continue
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k + LEN] = len(v)
        elif isinstance(v, dict):
            out[k + KEYS] = sorted(map(str, v.keys()))
    return out


def self_check():
    fn = _load_entry()
    expected = json.load(open(os.path.join(SAMPLES, "expected.json"), encoding="utf-8"))
    ok = True
    for ver in ("clean", "error"):
        got = _snap(_call(fn, os.path.join(SAMPLES, ver)))
        exp = expected.get(ver, {})
        for k, ev in exp.items():
            gv = got.get(k, "<缺失>")
            if isinstance(ev, float) and isinstance(gv, (int, float)):
                if abs(gv - ev) > 0.05:
                    print(f"✗ [{ver}] {k}: 期望 {ev} 实得 {gv}"); ok = False
            elif gv != ev:
                print(f"✗ [{ver}] {k}: 期望 {ev} 实得 {gv}"); ok = False
    # 参数用例：在全平版副本上把基数上下限改成无效值（0、下限高于上限），引擎必须报错，不能照常给出核定结果
    import shutil, tempfile, csv
    for case in expected.get("param_cases", []):
        with tempfile.TemporaryDirectory() as tmp:
            d = os.path.join(tmp, "data")
            shutil.copytree(os.path.join(SAMPLES, "clean"), d)
            p = os.path.join(d, "params.csv")
            rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
            for r in rows:
                if r["参数"] in case:
                    r["值"] = case[r["参数"]]
            with open(p, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["参数", "值"]); w.writeheader(); w.writerows(rows)
            try:
                _call(fn, d)
                print(f"✗ [param_cases] {case}: 期望 ValueError，引擎照常返回结果"); ok = False
            except ValueError as e:
                if "取值无效" not in str(e):   # 须命中上下限取值校验，而不是“缺失或无法解析”分支
                    print(f"✗ [param_cases] {case}: 报错不是取值无效：{e}"); ok = False
    print("自检通过 ✓ 脚本行为与黄金答案一致" if ok else "自检失败 ✗ 脚本可能已退化")
    return ok


if __name__ == "__main__":
    sys.exit(0 if self_check() else 1)
