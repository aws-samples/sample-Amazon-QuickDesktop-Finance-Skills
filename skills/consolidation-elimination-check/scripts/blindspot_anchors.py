"""🟡 接源补丁库 blindspot_anchors.py — 需外部锚点的盲区补丁（半自动：检查+接源建议）
与 blindspot_patches.py（🟢可自补）的区别：这些盲区缺的不是代码，是"第二个独立源"。
每个函数支持两种模式：
  - 有第二源：做双向交叉验证，抓出单边（内部标记有/外部源无，反之亦然）
  - 无第二源：不假装通过，而是结构化输出「需接入 X 源」的缺口声明（gap_declared）

补丁清单：
  1. scope_completeness   范围完备性交叉验证（关联方清单/合并范围/任何"名单是否齐全"）
  2. ratio_independent_check  比例独立校验（股权比例/持股比例 与 内部标记一致性）
"""

# ── 🟡补丁1：范围完备性交叉验证（+接源建议）──────────────────────
def scope_completeness(internal_ids, external_ids=None, *, source_name="工商股权登记",
                       internal_label="财务系统标记", external_label="外部权威源"):
    """交叉验证「名单是否齐全」。防范围盲区——未标记项根本不进检查。
    internal_ids: 内部系统标记的 ID 集合（如财务系统标记的关联方/合并范围主体）。
    external_ids: 外部独立源的 ID 集合（如工商股权登记）。None=第二源未接入。
    Returns: dict
      有第二源: matched / internal_only（内部有外部无）/ external_only（外部有内部无，最危险：漏登记）/ complete
      无第二源: gap_declared=True + 接源建议（不判 complete，诚实声明无法验证完备性）
    """
    si = set(internal_ids)
    if external_ids is None:
        return {"mode": "gap_declared", "gap_declared": True, "verifiable": False,
                "n_internal": len(si),
                "advice": f"范围完备性无法自校验——需接入「{source_name}」作为第二独立源，"
                          f"与{internal_label}做双向比对，才能发现未标记项。当前仅能确认已标记的 {len(si)} 项。",
                "closable_tier": "🟡需外部锚点"}
    se = set(external_ids)
    internal_only = sorted(si - se); external_only = sorted(se - si)
    return {"mode": "cross_verified", "verifiable": True,
            "matched": sorted(si & se),
            "internal_only": internal_only,   # 内部标了、外部源没有 → 可能误标
            "external_only": external_only,   # 外部源有、内部没标 → ⚠️漏登记（范围盲区实体）
            "n_matched": len(si & se), "n_internal_only": len(internal_only),
            "n_external_only": len(external_only),
            "complete": len(external_only) == 0,
            "risk": f"{len(external_only)} 个实体在{external_label}存在但{internal_label}未标记（范围盲区）"
                    if external_only else "范围完备"}

# ── 🟡补丁2：比例独立校验（+接源建议）──────────────────────
def ratio_independent_check(internal_ratios, external_ratios=None, *, tol=0.001,
                            source_name="工商股权比例", threshold=0.5):
    """校验持股/股权比例，并据此独立判定合并范围（>threshold 应纳入合并）。
    internal_ratios: {主体: 内部登记比例(小数)}。
    external_ratios: {主体: 外部源比例(小数)}。None=第二源未接入。
    threshold: 纳入合并的控股线，默认 0.5。
    Returns: dict
      有第二源: mismatches（比例不符）/ scope_conflict（合并判定冲突：内部纳入但外部比例不够，反之）
      无第二源: gap_declared + 接源建议
    """
    if external_ratios is None:
        should = sorted([k for k, v in internal_ratios.items() if v > threshold])
        return {"mode": "gap_declared", "gap_declared": True, "verifiable": False,
                "internal_should_consolidate": should,
                "advice": f"合并范围无法独立校验——需接入「{source_name}」核对持股比例。"
                          f"当前仅按内部登记比例推断应纳入合并 {len(should)} 家，无法排除内部比例本身错误。",
                "closable_tier": "🟡需外部锚点"}
    mismatches = []; scope_conflict = []
    for k in sorted(set(internal_ratios) | set(external_ratios)):
        iv = internal_ratios.get(k); ev = external_ratios.get(k)
        if iv is None or ev is None:
            scope_conflict.append({"主体": k, "内部比例": iv, "外部比例": ev, "问题": "单边缺失"}); continue
        if abs(iv - ev) > tol:
            mismatches.append({"主体": k, "内部比例": iv, "外部比例": ev, "差异": round(iv-ev, 4)})
        # 合并判定冲突：两个源跨越控股线不一致
        if (iv > threshold) != (ev > threshold):
            scope_conflict.append({"主体": k, "内部比例": iv, "外部比例": ev,
                                   "问题": f"合并判定冲突（阈值{threshold}）"})
    return {"mode": "cross_verified", "verifiable": True,
            "mismatches": mismatches, "scope_conflict": scope_conflict,
            "n_mismatch": len(mismatches), "n_conflict": len(scope_conflict),
            "consistent": len(mismatches) == 0 and len(scope_conflict) == 0}


if __name__ == "__main__":
    # 自检：每个补丁验证「有第二源」和「无第二源」两条路径
    # 补丁1 无第二源 → 诚实声明
    r = scope_completeness(["A", "B", "C"])
    assert r["gap_declared"] and not r["verifiable"] and r["n_internal"] == 3, r
    # 补丁1 有第二源 → 抓出漏登记
    r = scope_completeness(["A", "B"], ["A", "B", "C", "D"])
    assert r["external_only"] == ["C", "D"] and not r["complete"], r
    # 补丁2 无第二源 → 声明 + 内部推断
    r = ratio_independent_check({"子1": 0.8, "子2": 0.3})
    assert r["gap_declared"] and r["internal_should_consolidate"] == ["子1"], r
    # 补丁2 有第二源 → 抓出比例不符 + 合并判定冲突
    r = ratio_independent_check({"子1": 0.8, "子2": 0.55}, {"子1": 0.8, "子2": 0.45})
    assert r["n_mismatch"] == 1 and r["n_conflict"] == 1, r
    print("🟡接源补丁库自检全部通过 ✓（2 补丁 × 有源/无源两条路径）")
