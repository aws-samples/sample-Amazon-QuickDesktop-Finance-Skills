"""盲区补丁库 blindspot_patches.py — 六类判据的通用盲区补丁（确定性、域无关）
每个补丁抓的是「判据全绿但实际有错」的那类错误。所有函数纯 stdlib，可跨场景复用。

补丁清单：
  1. bidirectional_singleside  双向单边列示（防范围盲区/单向漏检）
  2. count_conservation         笔数守恒（配合金额守恒，防成对反向错误）
  3. signed_bucketing           分向列示+分向限额（防净额掩盖）
  4. normalize_key / fuzzy_fingerprint  归一化+模糊指纹（防唯一性绕过）
  5. unit_consistency           单位换算独立校验（防错单位下守恒成立）
  6. cross_whitelist            交叉白名单（业务类型×允许值，防合法但不适用）
"""
from collections import defaultdict, Counter
import unicodedata, re

# ── 补丁1：双向单边列示 ──────────────────────────────
def bidirectional_singleside(keys_a, keys_b, label_a="A", label_b="B"):
    """双向名单比对：A有B无 / B有A无 分别列示。防「未进检查范围」永远全绿。
    Returns: dict(only_a, only_b, both, n_only_a, n_only_b)"""
    sa, sb = set(keys_a), set(keys_b)
    only_a = sorted(sa - sb); only_b = sorted(sb - sa)
    return {"only_a": only_a, "only_b": only_b, "both": sorted(sa & sb),
            "n_only_a": len(only_a), "n_only_b": len(only_b),
            f"{label_a}有{label_b}无": only_a, f"{label_b}有{label_a}无": only_b}

# ── 补丁2：笔数守恒 ──────────────────────────────
def count_conservation(n_left, n_right, n_matched):
    """笔数守恒：左表笔数、右表笔数、匹配笔数的一致性。金额守恒抓不到成对错误，笔数能。
    Returns: dict(balanced, left_unmatched, right_unmatched)"""
    lu = n_left - n_matched; ru = n_right - n_matched
    return {"n_left": n_left, "n_right": n_right, "n_matched": n_matched,
            "left_unmatched": lu, "right_unmatched": ru,
            "balanced": lu == 0 and ru == 0}

# ── 补丁3：分向列示+分向限额 ──────────────────────────────
def signed_bucketing(diffs, limit=None):
    """把差异按正/负分向汇总，分别设限。防净额掩盖（+100 与 -100 净额为0）。
    diffs: list[float]. Returns: dict(pos_sum, neg_sum, net, gross, pos_n, neg_n, breach)"""
    pos = [d for d in diffs if d > 0]; neg = [d for d in diffs if d < 0]
    pos_sum = round(sum(pos), 2); neg_sum = round(sum(neg), 2)
    gross = round(sum(abs(d) for d in diffs), 2); net = round(sum(diffs), 2)
    breach = None
    if limit is not None:
        breach = {"pos_breach": pos_sum > limit, "neg_breach": abs(neg_sum) > limit}
    return {"pos_sum": pos_sum, "neg_sum": neg_sum, "net": net, "gross": gross,
            "pos_n": len(pos), "neg_n": len(neg), "breach": breach,
            "net_masks_gross": abs(net) < gross * 0.5 and gross > 0}

# ── 补丁4：归一化 + 模糊指纹 ──────────────────────────────
def normalize_key(s):
    """归一化：全角→半角、去空格、去前导零、大写。防唯一性绕过变形。"""
    if s is None: return ""
    s = unicodedata.normalize("NFKC", str(s)).strip().upper()
    s = re.sub(r"\s+", "", s)
    # 只去掉每段数字开头的前导零（如 FP-00123 → FP-123），不动数字中间的 0（FP-31000070 保持不变）
    s = re.sub(r"(?<!\d)0+(?=\d)", "", s)
    return s

def fuzzy_fingerprint(*fields):
    """模糊指纹：多字段组合键（如供应商+金额+日期）。抓「改号但要素相同」的拆分/变造重复。"""
    return "|".join(normalize_key(f) for f in fields)

def find_fuzzy_dups(records, fp_fields):
    """records: list[dict]; fp_fields: list[str] 用于组指纹的字段名。
    Returns: dict(fingerprint -> [记录索引]) 仅含重复组。"""
    groups = defaultdict(list)
    for i, r in enumerate(records):
        fp = fuzzy_fingerprint(*[r.get(f) for f in fp_fields])
        groups[fp].append(i)
    return {fp: idxs for fp, idxs in groups.items() if len(idxs) > 1}

# ── 补丁5：单位换算独立校验 ──────────────────────────────
def unit_consistency(rows, qty_field, unit_field, base_unit_map):
    """校验计量单位一致性。base_unit_map: {unit: factor_to_base}。
    抓「换算系数错时守恒在错单位下依然成立」。
    Returns: dict(bad_units, unit_dist)"""
    unit_dist = Counter(r.get(unit_field) for r in rows)
    bad = [u for u in unit_dist if u not in base_unit_map]
    return {"unit_dist": dict(unit_dist), "bad_units": bad,
            "mixed_units": len([u for u in unit_dist if u]) > 1}

# ── 补丁6：交叉白名单 ──────────────────────────────
def cross_whitelist(rows, type_field, value_field, allow_map):
    """交叉白名单：allow_map = {业务类型: set(允许值)}。
    单字段白名单放行「合法但不适用」，交叉白名单能抓。
    Returns: dict(violations=[(idx, type, value)])"""
    viol = []
    for i, r in enumerate(rows):
        t = r.get(type_field); v = r.get(value_field)
        if t in allow_map and v not in allow_map[t]:
            viol.append((i, t, v))
    return {"violations": viol, "n_violation": len(viol)}


if __name__ == "__main__":
    # 自检：每个补丁用一个「判据全绿但有错」的最小案例验证
    # 补丁3：净额掩盖
    r = signed_bucketing([100.0, -100.0])
    assert r["net"] == 0 and r["gross"] == 200.0 and r["net_masks_gross"], r
    # 补丁2：笔数守恒
    r = count_conservation(10, 10, 9); assert not r["balanced"] and r["left_unmatched"]==1
    # 补丁4：归一化
    assert normalize_key("　００１２３ ") == "123"
    assert fuzzy_fingerprint("示例公司A ", "1000.00", "2026-08-01") == fuzzy_fingerprint("示例公司A","1000.00","2026-08-01")
    # 补丁1：双向
    r = bidirectional_singleside([1,2,3],[2,3,4]); assert r["only_a"]==[1] and r["only_b"]==[4]
    # 补丁6：交叉白名单
    r = cross_whitelist([{"t":"货物","v":0.13},{"t":"服务","v":0.13}], "t","v",
                        {"货物":{0.13},"服务":{0.06}})
    assert r["n_violation"]==1, r
    # 补丁5：单位
    r = unit_consistency([{"q":1,"u":"吨"},{"q":1,"u":"公斤"}], "q","u",{"吨":1000,"公斤":1})
    assert r["mixed_units"], r
    print("盲区补丁库自检全部通过 ✓（6 补丁 × 判据全绿但有错的案例）")
