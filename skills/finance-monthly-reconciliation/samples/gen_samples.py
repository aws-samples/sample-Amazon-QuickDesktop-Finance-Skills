# -*- coding: utf-8 -*-
"""月末财务对账 · 自检样例生成器（构造数据 + 按构造推导黄金答案）。

用法：python gen_samples.py        # 在 samples/ 下生成 clean/、error/ 与 expected.json

- 全部名称为虚构（示例集团、示例供应商A、示例客户B……），不含真实企业、人员、账号或税号。
- expected.json 由本脚本按「数据是怎么造的」直接写出，不调用 reconciliation_engine.py，避免循环验证。
- 银行回单 PDF：环境中无 PDF 解析库，引擎实际走的是「文件名解析」路径
  （银行回单_<日期>_<收款方>_<金额>元.pdf），因此回单为 0 字节占位文件。
  回单/回单来源.csv 模拟每份回单的邮件发件人和 file_read_pdf 读出的正文；植错版有一份发件人不在受信任清单、一份受信任发件人但未提供正文（不作为凭证，列入「未提供正文」）、
  一份正文金额与文件名不符；这三份都不应作为银行侧凭证（expected.json 的 receipt_check）。
- 已知缺口（engine 当前行为与 SKILL.md 规定或安全扫描建议的行为不一致、本轮未修复）写入 expected.json 的 known_gaps，
  self_check 会把它们报告为 KNOWN GAP，不会算作通过。
"""
import json
import os

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 构造：ERP 与银行流水
# 每条记录注明它属于哪一类匹配，黄金答案按这些标注计数。
# ERP 金额 = 借方金额（>0 时）否则贷方金额；银行金额 = 收入（>0 时）否则支出。
CLEAN_PAIRS = [
    # (类别, ERP行, 银行行)
    ("ref", dict(凭证号="PZ-0801", 日期="2026-08-03", 摘要="支付示例供应商A货款", 借方金额=0.0, 贷方金额=52000.00,
                 科目代码="1002", 供应商="示例供应商A", 部门="采购部"),
            dict(交易流水号="SN-0801", 交易日期="2026-08-03", 交易摘要="货款", 收入=0.0, 支出=52000.00,
                 对方账户="示例账户-A", 参考号="BK-0801")),
    ("ref", dict(凭证号="PZ-0802", 日期="2026-08-05", 摘要="收示例客户B回款", 借方金额=80000.00, 贷方金额=0.0,
                 科目代码="1002", 供应商="示例客户B", 部门="销售部"),
            dict(交易流水号="SN-0802", 交易日期="2026-08-05", 交易摘要="汇入", 收入=80000.00, 支出=0.0,
                 对方账户="示例账户-B", 参考号="BK-0802")),
    ("ref", dict(凭证号="PZ-0803", 日期="2026-08-10", 摘要="支付8月办公室租金", 借方金额=0.0, 贷方金额=30000.00,
                 科目代码="1002", 供应商="示例物业C", 部门="行政部"),
            dict(交易流水号="SN-0803", 交易日期="2026-08-10", 交易摘要="租金", 收入=0.0, 支出=30000.00,
                 对方账户="示例账户-C", 参考号="BK-0803")),
    # 日期+金额：同日，金额差 0.5%（<1% 容差），参考号缺失
    ("amount", dict(凭证号="PZ-0811", 日期="2026-08-12", 摘要="支付示例物流D运费", 借方金额=0.0, 贷方金额=10000.00,
                    科目代码="1002", 供应商="示例物流D", 部门="物流部"),
               dict(交易流水号="SN-0811", 交易日期="2026-08-12", 交易摘要="运费", 收入=0.0, 支出=10050.00,
                    对方账户="示例账户-D", 参考号="")),
    ("amount", dict(凭证号="PZ-0812", 日期="2026-08-15", 摘要="收示例客户E货款", 借方金额=25000.00, 贷方金额=0.0,
                    科目代码="1002", 供应商="示例客户E", 部门="销售部"),
               dict(交易流水号="SN-0812", 交易日期="2026-08-15", 交易摘要="汇入款", 收入=25000.00, 支出=0.0,
                    对方账户="示例账户-E", 参考号="")),
    # 摘要相似度：银行次日入账（日期不同）、无参考号，摘要高度相似（相似度约 0.92 > 0.6）
    ("text", dict(凭证号="PZ-0821", 日期="2026-08-20", 摘要="支付示例供应商F设备维修费", 借方金额=0.0, 贷方金额=6800.00,
                  科目代码="1002", 供应商="示例供应商F", 部门="生产部"),
             dict(交易流水号="SN-0821", 交易日期="2026-08-21", 交易摘要="示例供应商F设备维修费", 收入=0.0, 支出=6800.00,
                  对方账户="示例账户-F", 参考号="")),
]

# 植错版在全平版基础上追加：
ERROR_EXTRA_PAIRS = [
    # 参考号匹配，但银行金额只有 ERP 的四分之一：单笔差异 600,000 > SKILL.md 规定的 50 万单独告警线
    ("ref_big_diff", dict(凭证号="PZ-0805", 日期="2026-08-18", 摘要="支付示例供应商G设备款", 借方金额=0.0, 贷方金额=800000.00,
                          科目代码="1002", 供应商="示例供应商G", 部门="生产部"),
                     dict(交易流水号="SN-0805", 交易日期="2026-08-18", 交易摘要="设备款", 收入=0.0, 支出=200000.00,
                          对方账户="示例账户-G", 参考号="BK-0805")),
]
ERROR_ERP_ONLY = [  # 企业已记、银行未记
    dict(凭证号="PZ-0831", 日期="2026-08-28", 摘要="支付示例咨询H服务费", 借方金额=0.0, 贷方金额=15000.00,
         科目代码="1002", 供应商="示例咨询H", 部门="财务部"),
    dict(凭证号="PZ-0832", 日期="2026-08-29", 摘要="支付示例保险J保费", 借方金额=0.0, 贷方金额=9600.00,
         科目代码="1002", 供应商="示例保险J", 部门="行政部"),
]
XSS_IMG = "<img src=x onerror=alert(1)>"
XSS_SCRIPT = "</script><script>alert(2)</script>"
FORMULA = '=HYPERLINK("https://example.com","点击")'
ERROR_BANK_ONLY = [  # 银行已记、企业未记；摘要/对方账户为注入测试载荷
    dict(交易流水号="SN-0841", 交易日期="2026-08-25", 交易摘要=XSS_IMG, 收入=0.0, 支出=1200.00,
         对方账户="示例账户-K", 参考号=""),
    dict(交易流水号="SN-0842", 交易日期="2026-08-26", 交易摘要=XSS_SCRIPT, 收入=300.00, 支出=0.0,
         对方账户=FORMULA, 参考号=""),
]

# ---------------------------------------------------------------- 构造：应收 / 应付 / 资金日报
AR = [  # 发票号, 客户名称, 开票日期, 发票金额, 已收金额, 账龄(天)
    ("AR-01", "示例客户B", "2026-08-21", 50000.00, 30000.00, 10),
    ("AR-02", "示例客户E", "2026-07-17", 30000.00, 0.00, 45),
    ("AR-03", "示例客户L", "2026-06-17", 20000.00, 8000.00, 75),
    ("AR-04", "示例客户M", "2026-05-03", 8000.00, 0.00, 120),
]
AP = [  # 账单号, 供应商, 账单日期, 账单金额, 已付金额, 账龄(天)
    ("AP-01", "示例供应商A", "2026-08-26", 15000.00, 0.00, 5),
    ("AP-02", "示例供应商F", "2026-07-27", 12000.00, 3000.00, 35),
    ("AP-03", "示例供应商G", "2026-05-23", 6000.00, 0.00, 100),
]
DAILY = [  # 日期, 期初余额, 当日收入, 当日支出（期末 = 期初 + 收入 − 支出，逐日衔接）
    ("2026-08-27", 500000.00, 80000.00, 52000.00),
    ("2026-08-28", None, 25000.00, 30000.00),
    ("2026-08-29", None, 0.00, 16800.00),
]
AGING_BINS = [("0-30天", 0, 30), ("30-60天", 30, 60), ("60-90天", 60, 90), ("90天以上", 90, float("inf"))]

# ---------------------------------------------------------------- 构造：回单来源（模拟邮件发件人 + file_read_pdf 读出的正文）
# 回单来源.csv 每行对应一份回单：文件名, 发件人, 正文（留空 = 未提供正文）。self_check 把它转成
# parse_bank_receipts() 的 {'path','sender','text'} 输入，trusted_senders 取 expected.json 的同名字段。
TRUSTED = ["e-receipt@bank.example.com"]
# 植错版：三份回单不应作为银行侧凭证
ERROR_RECEIPT_OVERRIDES = {
    # 仅 ERP 有、银行未记的付款，却收到一封「回单」：发件人是形似银行的域名，不在受信任清单
    "PZ-0831": {"发件人": "e-receipt@bank-mail.example.com"},
    # 受信任发件人，但 PDF 正文金额（960.00）与文件名金额（9600）不一致
    "PZ-0832": {"正文金额": 960.00},
    # 受信任发件人但未提供正文：不作为凭证，列入 receipt_check 的「未提供正文」
    "PZ-0811": {"无正文": True},
}


def receipt_text(date, payee, amount):
    return f"交易日期：{date}\n交易金额：¥{amount:,.2f}\n收款账户名：{payee}\n摘要：付款"


def amt_erp(r):
    return r["借方金额"] if r["借方金额"] > 0 else r["贷方金额"]


def amt_bank(r):
    return r["收入"] if r["收入"] > 0 else r["支出"]


def daily_rows():
    rows, bal = [], None
    for d, op, inc, exp in DAILY:
        op = op if op is not None else bal
        bal = round(op + inc - exp, 2)
        rows.append(dict(日期=d, 期初余额=op, 当日收入=inc, 当日支出=exp, 期末余额=bal, 银行存款=bal, 理财产品=0.0))
    return rows


def aging(rows, amt_idx_paid, amount_idx):
    out = {name: 0.0 for name, _, _ in AGING_BINS}
    for r in rows:
        age, unpaid = r[5], round(r[amount_idx] - r[amt_idx_paid], 2)
        for name, lo, hi in AGING_BINS:
            if lo <= age < hi:
                out[name] += unpaid
                break
    return [{"name": k, "y": round(v, 2)} for k, v in out.items()]


def build(case):
    pairs = CLEAN_PAIRS + (ERROR_EXTRA_PAIRS if case == "error" else [])
    erp = [p[1] for p in pairs] + (ERROR_ERP_ONLY if case == "error" else [])
    bank = [p[2] for p in pairs] + (ERROR_BANK_ONLY if case == "error" else [])
    ar = [dict(发票号=a, 客户名称=b, 开票日期=c, 发票金额=d, 已收金额=e, 未收金额=round(d - e, 2), **{"账龄(天)": f},
               状态="未结清" if d > e else "已结清") for a, b, c, d, e, f in AR]
    ap = [dict(账单号=a, 供应商=b, 账单日期=c, 账单金额=d, 已付金额=e, 未付金额=round(d - e, 2), **{"账龄(天)": f},
               状态="未结清" if d > e else "已结清") for a, b, c, d, e, f in AP]
    for r in bank:
        r.setdefault("余额", 0.0)
    daily = daily_rows()

    d = os.path.join(HERE, case)
    os.makedirs(os.path.join(d, "回单"), exist_ok=True)
    # strings_to_formulas=False：让以 = 开头的公式注入载荷按普通文本写入样例（模拟银行导出的原始文本）
    with pd.ExcelWriter(os.path.join(d, "财务月末数据包.xlsx"), engine="xlsxwriter",
                        engine_kwargs={"options": {"strings_to_formulas": False}}) as w:
        pd.DataFrame(erp).to_excel(w, sheet_name="ERP总账明细", index=False)
        pd.DataFrame(bank, columns=["交易流水号", "交易日期", "交易摘要", "收入", "支出", "余额", "对方账户", "参考号"]
                     ).to_excel(w, sheet_name="银行对账单", index=False)
        pd.DataFrame(ar).to_excel(w, sheet_name="应收账款", index=False)
        pd.DataFrame(ap).to_excel(w, sheet_name="应付账款", index=False)
        pd.DataFrame(daily).to_excel(w, sheet_name="资金日报", index=False)

    # 回单：仅为 ERP 中的付款构造（文件名解析路径；0 字节占位）；来源与正文写入 回单来源.csv
    receipts = []
    for r in erp:
        if r["贷方金额"] > 0:
            amt = r["贷方金额"]
            name = f"银行回单_{r['日期']}_{r['供应商']}_{amt:.0f}元.pdf"
            open(os.path.join(d, "回单", name), "wb").close()
            ov = ERROR_RECEIPT_OVERRIDES.get(r["凭证号"], {}) if case == "error" else {}
            sender = ov.get("发件人", TRUSTED[0])
            text = "" if ov.get("无正文") else receipt_text(r["日期"], r["供应商"], ov.get("正文金额", amt))
            receipts.append({"日期": r["日期"], "收款方": r["供应商"], "金额": amt, "文件名": name,
                             "发件人": sender, "正文": text,
                             "未验证来源": sender not in TRUSTED, "正文不符": ov.get("正文金额", amt) != amt})
    receipts.sort(key=lambda x: x["文件名"])
    pd.DataFrame([{"文件名": x["文件名"], "发件人": x["发件人"], "正文": x["正文"]} for x in receipts]
                 ).to_csv(os.path.join(d, "回单", "回单来源.csv"), index=False, encoding="utf-8")

    # ---------------- 黄金答案（按构造推导）
    kinds = [p[0] for p in pairs]
    n_ref = sum(k in ("ref", "ref_big_diff") for k in kinds)
    n_amount = kinds.count("amount")
    n_text = kinds.count("text")
    matched = len(pairs)
    erp_only = len(erp) - matched
    bank_only = len(bank) - matched
    # 安全扫描（f-c2d4674a）建议的口径：分母为去重交易数（匹配对 + 仅ERP + 仅银行），见 known_gaps
    rate_both = round(matched / max(matched + erp_only + bank_only, 1) * 100, 1)
    rate_bank_only = round(matched / max(len(bank), 1) * 100, 1)  # 引擎现行口径，仅用于判断缺口是否可观测
    big_diffs = [p[1]["凭证号"] for p in pairs if abs(amt_erp(p[1]) - amt_bank(p[2])) > 500000]
    gate = "halt" if rate_both < 70 or big_diffs else ("needs_review" if rate_both < 90 else "ok")

    ar_unpaid = [(r[5], round(r[3] - r[4], 2)) for r in AR]
    ap_unpaid = [(r[5], round(r[3] - r[4], 2)) for r in AP]
    exp = {
        "数据": {"ERP行数": len(erp), "银行行数": len(bank), "回单数": len(receipts)},
        "stats": {
            "total_erp": len(erp), "total_bank": len(bank), "matched": matched,
            "erp_only": erp_only, "bank_only": bank_only,
            "match_by_ref": n_ref, "match_by_amount": n_amount, "match_by_text": n_text,
        },
        "matched_pairs": sorted([p[1]["凭证号"], p[2]["交易流水号"]] for p in pairs),
        "erp_only_ids": sorted(r["凭证号"] for r in erp[matched:]),
        "bank_only_ids": sorted(r["交易流水号"] for r in bank[matched:]),
        "kpi": {
            "unmatched_count": erp_only + bank_only,
            "ar_overdue": round(sum(u for a, u in ar_unpaid if a > 30), 2),
            "ap_overdue": round(sum(u for a, u in ap_unpaid if a > 30), 2),
            "cash_balance": daily[-1]["期末余额"],
        },
        "ar_aging": aging(AR, 4, 3),
        "ap_aging": aging(AP, 4, 3),
        "receipts": [{"日期": x["日期"], "收款方": x["收款方"], "金额": x["金额"]} for x in receipts],
        # 回单核验（f-a86a78de）：发件人不在受信任清单 → 未验证来源回单；正文金额/日期与文件名不符 → 正文不符；
        # 未提供正文 → 未提供正文（f-0bafc519）；三者都不作为凭证
        "receipt_check": {
            "回单数": len(receipts),
            "可作凭证": sum(1 for x in receipts if not x["未验证来源"] and not x["正文不符"] and x["正文"]),
            "未验证来源回单": sorted(x["文件名"] for x in receipts if x["未验证来源"]),
            "正文不符": sorted(x["文件名"] for x in receipts if x["正文不符"]),
            "未提供正文": sorted(x["文件名"] for x in receipts if not x["正文"]),
        },
        "known_gaps": ([
            {"id": "match_rate_denominator", "finding": "f-c2d4674a-771e-46d5-964a-fff2fbc7db68",
             "说明": "安全扫描建议：匹配率分母改为去重交易数（匹配对+仅ERP+仅银行，即 ERP 行数+银行行数−匹配数）；引擎只用银行行数作分母",
             "expected": rate_both}] if rate_both != rate_bank_only else []) + [
            {"id": "halt_gate", "finding": "SA「Documented reconciliation halt gates ... have no enforcement point」",
             "说明": "SKILL.md：匹配率<90% 黄色预警、<70% 或单笔差异>50万 红色告警暂停；引擎无此判定（stats 无 gate 字段）",
             "expected": gate, "expected_big_diffs": big_diffs},
            {"id": "receipts_unused", "finding": "SA「Bank e-receipt leg ... receipts_df is parsed then discarded」",
             "说明": "SKILL.md：cloud 模式以回单为银行侧凭证；引擎解析回单后不参与对账，传不传回单结果相同",
             "expected": "传入回单后对账结果应体现回单"},
        ],
    }
    return exp


def main():
    out = {case: build(case) for case in ("clean", "error")}
    out["trusted_senders"] = TRUSTED
    out["xss_payloads"] = [XSS_IMG, XSS_SCRIPT]
    out["formula_payload"] = FORMULA
    with open(os.path.join(HERE, "expected.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("samples generated:", ", ".join(sorted(out)))


if __name__ == "__main__":
    main()
