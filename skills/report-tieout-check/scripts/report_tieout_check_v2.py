"""报表勾稽自检 v2 · 结构驱动确定性脚本
用法: python report_tieout_check_v2.py <balance_sheet.csv> <income_statement.csv> <cash_flow.csv> <retained_earnings.csv> [out.json] [--tol 容差]
容差：默认 TOL=0.5 元；调用方可显式传入 0 < tol ≤ TOL_MAX（1.0 元），越界抛 ValueError。实际采用的容差写入结果 tol 字段
  并在底稿中披露；每次调用独立，不改写模块级默认值。
判据：会计恒等式 + 表内小计（按「分区」列动态求和，兼容丰富报表结构）+ 三表间勾稽 + 跨期衔接
数据质量（fail-closed）：金额无法解析（如千分位 "1,234.00"）、留空，或 REQUIRED 中的必需项目缺失 → 登记 data_issues，
  引用该项目的勾稽项判不通过（不按 0 计），all_tie=False；底稿结论随之为「不具备对外报出条件」。
CSV 三列：项目, 金额, 分区（分区∈ 流动资产/非流动资产/流动负债/非流动负债/所有者权益/利润表/现金流量/滚动/小计/合计）
向后兼容：无「分区」列时回退到 v1 的固定科目模式。
"""
import sys, json, csv, os, math

TOL = 0.5       # 只读默认容差（元）；不得在运行时改写，调用方容差经 check(tol=...) 逐次传入
TOL_MAX = 1.0   # 调用方可放宽的上限（元）


def _valid_tol(tol):
    """返回本次采用的容差：None 或 0 → 默认 TOL；否则须为有限数且 0 < tol ≤ TOL_MAX，否则抛 ValueError。"""
    if tol is None or tol == 0:
        return TOL
    try:
        t = float(tol)
    except (TypeError, ValueError):
        t = float("nan")
    if not (math.isfinite(t) and 0 < t <= TOL_MAX):
        raise ValueError(f"容差 tol={tol!r} 超出允许范围 (0, {TOL_MAX}]")
    return t
# 必需项目：缺失时不按 0 计，相关勾稽项判不通过并登记 data_issues（其余项目缺失时按 0 计，如研发费用、投资收益）
REQUIRED = {
    "balance_sheet.csv": ("货币资金", "未分配利润", "资产总计", "负债合计", "所有者权益合计"),
    "income_statement.csv": ("营业收入", "营业成本", "利润总额", "所得税费用", "净利润"),
    "cash_flow.csv": ("经营活动现金流量净额", "投资活动现金流量净额", "筹资活动现金流量净额", "现金净增加额", "期初现金余额", "期末现金余额"),
    "retained_earnings.csv": ("未分配利润_期初", "本期净利润", "未分配利润_期末"),
}
DQ = []      # 本次 check() 的数据质量问题：金额无法解析、留空或必需项目缺失
MISS = []    # 当前勾稽项求值过程中引用到的不可用项目，由 chk() 取走并清空


class Items(dict):
    """项目→金额。bad 为本表不可用的项目（金额无法解析 / 留空 / 必需项目缺失）。读取 bad 项目时记入 MISS，
    返回 0.0 只为让表达式能算完；引用它的勾稽项一律判不通过，不会因为按 0 计而「相符」。"""
    def __init__(self):
        super().__init__()
        self.bad = set()

    def __contains__(self, k):
        return dict.__contains__(self, k) or k in self.bad

    def get(self, k, default=0.0):
        if k in self.bad:
            MISS.append(k)
            return 0.0
        return dict.get(self, k, default)

    def __getitem__(self, k):
        if k in self.bad:
            MISS.append(k)
            return 0.0
        return dict.__getitem__(self, k)


def load(path):
    d = Items(); zones = {}
    fn = os.path.basename(path)
    with open(path, encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        has_zone = "分区" in (rd.fieldnames or [])
        for i, r in enumerate(rd, 2):     # 行号按 CSV 文件行计（表头为第 1 行）
            k = r["项目"]
            s = (r.get("金额") or "").strip()
            try:
                v = float(s)
                ok = math.isfinite(v)
            except ValueError:
                ok = False
            if has_zone: zones[k] = r.get("分区","")
            if not ok:
                DQ.append({"文件": fn, "行号": i, "字段": "金额", "项目": k, "原值": r.get("金额"),
                           "问题": "数值缺失" if not s else "数值无法解析"})
                d.bad.add(k)
                continue
            d[k] = v
    for k in REQUIRED.get(fn, ()):
        if k not in d:
            DQ.append({"文件": fn, "行号": None, "字段": "项目", "项目": k, "原值": None, "问题": "必需项目缺失"})
            d.bad.add(k)
    return d, zones, ("分区" in (zones and {"x"} or {}) or bool(zones))

def check(bs_path, inc_path, cf_path, re_path, tol=None):
    tol = _valid_tol(tol)
    DQ.clear(); MISS.clear()
    bs, bsz, bs_has = load(bs_path)
    inc, incz, _ = load(inc_path)
    cf, cfz, _ = load(cf_path)
    re, rez, _ = load(re_path)
    checks = []
    def chk(name, lhs, rhs, kind, uses=()):
        # uses：本勾稽项引用的分区和所排除的不可用项目（分区和先于勾稽项算出，不能经 MISS 传递）
        diff = round(lhs - rhs, 2)
        miss = sorted(set(MISS) | set(uses)); MISS.clear()
        c = {"勾稽项": name, "类型": kind, "左值": round(lhs,2), "右值": round(rhs,2),
             "差异": diff, "通过": abs(diff) <= tol and not miss}
        if miss:
            c["缺数据"] = miss      # 引用了无法解析或缺失的项目：不判相符
        checks.append(c)
    def zone_sum(zmap, dmap, zone):
        """返回（分区内可用项目之和, 分区内不可用项目）；不可用项目由引用该分区和的 chk() 列入缺数据。"""
        bad = [k for k,z in zmap.items() if z==zone and k in dmap.bad]
        return sum(dmap[k] for k,z in zmap.items() if z==zone and k not in dmap.bad), bad

    # ── 一、资产负债表：表内小计（结构驱动）──
    if bs_has and bsz:
        流资, 流资缺 = zone_sum(bsz,bs,"流动资产"); 非流资, 非流资缺 = zone_sum(bsz,bs,"非流动资产")
        流负, 流负缺 = zone_sum(bsz,bs,"流动负债"); 非流负, 非流负缺 = zone_sum(bsz,bs,"非流动负债")
        权益, 权益缺 = zone_sum(bsz,bs,"所有者权益")
        if "流动资产合计" in bs: chk("流动资产合计=各流动资产之和", bs["流动资产合计"], 流资, "表内小计", 流资缺)
        if "非流动资产合计" in bs: chk("非流动资产合计=各非流动资产之和", bs["非流动资产合计"], 非流资, "表内小计", 非流资缺)
        if "流动负债合计" in bs: chk("流动负债合计=各流动负债之和", bs["流动负债合计"], 流负, "表内小计", 流负缺)
        if "非流动负债合计" in bs: chk("非流动负债合计=各非流动负债之和", bs["非流动负债合计"], 非流负, "表内小计", 非流负缺)
        chk("资产总计=流动+非流动资产", bs.get("资产总计",0), 流资+非流资, "表内小计", 流资缺+非流资缺)
        chk("负债合计=流动+非流动负债", bs.get("负债合计",0), 流负+非流负, "表内小计", 流负缺+非流负缺)
        chk("所有者权益合计=各权益科目之和", bs.get("所有者权益合计",0), 权益, "表内小计", 权益缺)
    else:
        # v1 回退
        chk("资产总计=各资产之和", bs.get("资产总计",0),
            bs.get("货币资金",0)+bs.get("应收账款",0)+bs.get("存货",0)+bs.get("固定资产",0), "表内小计")
        chk("负债合计=应付+短借", bs.get("负债合计",0),
            bs.get("应付账款",0)+bs.get("短期借款",0), "表内小计")
        chk("所有者权益=实收资本+未分配利润", bs.get("所有者权益合计",0),
            bs.get("实收资本",0)+bs.get("未分配利润",0), "表内小计")

    # 会计恒等式（通用）
    chk("会计恒等式:资产=负债+所有者权益", bs.get("资产总计",0),
        bs.get("负债合计",0)+bs.get("所有者权益合计",0), "会计恒等式")

    # ── 二、利润表 ──
    if "营业利润" in inc:
        chk("营业利润=收入-成本-税金-费用+投资收益", inc.get("营业利润",0),
            inc.get("营业收入",0)-inc.get("营业成本",0)-inc.get("税金及附加",0)
            -inc.get("销售费用",0)-inc.get("管理费用",0)-inc.get("研发费用",0)
            -inc.get("财务费用",0)+inc.get("投资收益",0), "表内小计")
        chk("利润总额=营业利润+营业外收支", inc.get("利润总额",0),
            inc.get("营业利润",0)+inc.get("营业外收入",0)-inc.get("营业外支出",0), "表内小计")
    else:
        chk("利润总额=收入-成本-费用", inc.get("利润总额",0),
            inc.get("营业收入",0)-inc.get("营业成本",0)-inc.get("期间费用",0), "表内小计")
    chk("净利润=利润总额-所得税", inc.get("净利润",0),
        inc.get("利润总额",0)-inc.get("所得税费用",0), "表内小计")

    # ── 三、现金流量表 ──
    chk("现金净增加额=经营+投资+筹资", cf.get("现金净增加额",0),
        cf.get("经营活动现金流量净额",0)+cf.get("投资活动现金流量净额",0)+cf.get("筹资活动现金流量净额",0), "表内小计")
    chk("期末现金=期初+净增加", cf.get("期末现金余额",0),
        cf.get("期初现金余额",0)+cf.get("现金净增加额",0), "表内小计")

    # ── 四、三表间勾稽 ──
    chk("[跨表]CF期末现金=BS货币资金", cf.get("期末现金余额",0), bs.get("货币资金",0), "三表勾稽")
    chk("[跨表]净利润:利润表=未分配利润滚动", inc.get("净利润",0), re.get("本期净利润",0), "三表勾稽")
    # RE 期末（含盈余公积、分红）
    re_expect = re.get("未分配利润_期初",0)+re.get("本期净利润",0)-re.get("提取盈余公积",0)-re.get("分配股利",0)
    chk("[跨表]未分配利润期末=期初+净利-盈余公积-分红", re.get("未分配利润_期末",0), re_expect, "三表勾稽")
    chk("[跨表]BS未分配利润=滚动表期末", bs.get("未分配利润",0), re.get("未分配利润_期末",0), "三表勾稽")

    passed = sum(1 for c in checks if c["通过"])
    total = len(checks)
    fails = [c for c in checks if not c["通过"]]
    assert passed + len(fails) == total, "独立自检失败：通过数+不平数≠总数"
    # 全平 = 全部勾稽项相符，且没有数据质量问题（任一金额无法解析或必需项目缺失都不能报「全平」）
    return {"checks": checks, "passed": passed, "total": total,
            "pass_rate": round(passed/total*100, 1),
            "n_fail": len(fails), "fails": fails, "data_issues": list(DQ),
            "all_tie": len(fails) == 0 and not DQ, "tol": tol}


def check_report_tieout(bs_path, inc_path, cf_path, re_path, tol=0):
    """结构驱动版三大报表勾稽自检（v2）兼容入口——带 ISO8601 可回溯时间戳。
    与 check() 同逻辑，额外注入 timestamp 字段，供 skill 驱动与预加载命名空间统一调用。
    tol：0 或 None 用默认 TOL（0.5 元）；显式值须 0 < tol ≤ TOL_MAX（1.0 元），否则抛 ValueError。
    实际容差写入结果 tol 字段，仅作用于本次调用。"""
    from datetime import datetime
    out = check(bs_path, inc_path, cf_path, re_path, tol=tol)
    out["timestamp"] = datetime.now().isoformat(timespec="seconds")
    return out


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    try:
        argv, opts = og.parse_cli(sys.argv, value_opts=("--tol",))
        try:
            tol = _valid_tol(opts.get("--tol"))
        except ValueError as e:
            print(f"参数无效：{e}", file=sys.stderr)
            sys.exit(2)
        out = check(argv[1], argv[2], argv[3], argv[4], tol=tol)
        dest = argv[5] if len(argv) > 5 else "analysis.json"
        inputs = [og.file_fingerprint(p, r) for p, r in zip(argv[1:5], ("bs", "inc", "cf", "re"))]
        og.finish_cli(__file__, out, dest, opts.get("--out-dir"), "analysis.json", {"tol": out["tol"]}, inputs, ok_key="all_tie")
    except og.OutputPathError as e:
        og.reject(e)
