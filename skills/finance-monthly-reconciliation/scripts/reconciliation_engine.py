"""
月末财务对账引擎 v2.0
=====================
核心对账逻辑模块，可直接在 Amazon Quick Desktop 的 run_python 中执行。

数据流：
  OneDrive (ERP数据) + Outlook (银行回单邮件) → 对账 → 仪表盘 + Excel + 通知

依赖：pandas, difflib, xlsxwriter (均为 Quick Desktop 内置)
"""

import pandas as pd
import json
import re
import math
import os
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional


# ============================================================
# Step 1: 数据加载
# ============================================================

def load_erp_data(file_path: str) -> Dict[str, pd.DataFrame]:
    """从 Excel 数据包加载 ERP 数据（5个sheet）。

    Excel 文件应包含以下 sheet：
      - ERP总账明细: 凭证号, 日期, 摘要, 借方金额, 贷方金额, 科目代码, 供应商, 部门
      - 银行对账单: 交易流水号, 交易日期, 交易摘要, 收入, 支出, 余额, 对方账户, 参考号
      - 应收账款: 发票号, 客户名称, 开票日期, 发票金额, 已收金额, 未收金额, 账龄(天), 状态
      - 应付账款: 账单号, 供应商, 账单日期, 账单金额, 已付金额, 未付金额, 账龄(天), 状态
      - 资金日报: 日期, 期初余额, 当日收入, 当日支出, 期末余额, 银行存款, 理财产品

    Args:
        file_path: Excel 文件的本地路径

    Returns:
        dict: {
            'erp_df': ERP总账明细 DataFrame,
            'bank_df': 银行对账单 DataFrame,
            'ar_df': 应收账款 DataFrame,
            'ap_df': 应付账款 DataFrame,
            'daily_df': 资金日报 DataFrame,
        }
    """
    sheet_map = {
        'erp_df': 'ERP总账明细',
        'bank_df': '银行对账单',
        'ar_df': '应收账款',
        'ap_df': '应付账款',
        'daily_df': '资金日报',
    }

    result = {}
    xls = pd.ExcelFile(file_path)
    available_sheets = xls.sheet_names

    for key, sheet_name in sheet_map.items():
        if sheet_name in available_sheets:
            df = pd.read_excel(xls, sheet_name=sheet_name)
            result[key] = df
            print(f"  ✅ {sheet_name}: {len(df)} 条记录")
        else:
            print(f"  ⚠️ 未找到 sheet: {sheet_name}")
            result[key] = pd.DataFrame()

    xls.close()
    return result


def parse_bank_receipts(receipts: List, trusted_senders=()) -> pd.DataFrame:
    """解析银行回单，并核验来源与内容后才认作银行侧凭证。

    交易日期、收款方、金额取自回单文件名（银行回单_<日期>_<收款方>_<金额>元.pdf）。文件名和邮件标题
    任何人都能编造，因此：
      1. 来源核验：发件人必须在 trusted_senders 允许清单内（完整邮箱地址，不区分大小写）。
         不在清单内或发件人未知的回单标为「未验证来源回单」，不作为对账凭证。
      2. 内容核对：从调用方提供的 PDF 正文（Quick Desktop 中用 file_read_pdf 读取）提取交易日期与
         交易金额，与文件名比对；不一致或正文中找不到这两项 → 不作为对账凭证。
         未提供正文（缺失或空白）的回单无法核对内容，同样不作为对账凭证。
      3. 文件名中的金额无法解析 → 不作为对账凭证（不按 0 计）。

    Args:
        receipts: 回单列表。每项为 dict {'path': PDF 路径, 'sender': 发件人邮箱, 'text': PDF 正文（可选）}；
                  也可直接给 PDF 路径字符串，此时发件人未知，按未验证来源处理。
        trusted_senders: 受信任的银行发件人邮箱清单。默认为空，即没有任何回单能通过来源核验。

    Returns:
        DataFrame: 列 = [日期, 收款方, 金额, 摘要, 银行, 文件名, 发件人, 来源, 正文核对, 问题, 可作凭证]
          来源: '受信任发件人' / '未验证来源回单'
          正文核对: '一致' / '未提供正文' / '不一致'
          可作凭证: 来源受信任、金额可解析、提供了正文且正文核对一致
    """
    trusted = {str(s).strip().lower() for s in (trusted_senders or ()) if str(s).strip()}
    records = []

    # 常见匹配模式
    date_pattern = re.compile(r'交易日期[：:]\s*(\d{4}-\d{2}-\d{2})')
    amount_pattern = re.compile(r'交易金额[：:]\s*[¥￥]?\s*([\d,]+\.?\d*)')
    payee_pattern = re.compile(r'收款账户名[：:]\s*(.+)')
    memo_pattern = re.compile(r'摘要[/／附言]*[：:]\s*(.+)')
    bank_names = ['中国工商银行', '中国建设银行', '中国银行', '招商银行', '交通银行',
                  '中国农业银行', '中信银行', '浦发银行', '民生银行', '光大银行']

    for i, item in enumerate(receipts, 1):
        rec = item if isinstance(item, dict) else {'path': item}
        pdf_path = rec.get('path', '')
        sender = str(rec.get('sender') or '').strip()
        text = rec.get('text')
        fname = os.path.basename(pdf_path)
        try:
            # 从文件名提取信息，格式: 银行回单_2026-08-14_示例公司30_242100元.pdf
            parts = fname.replace('.pdf', '').split('_')
            if len(parts) < 4:
                print(f"  ⚠️ 第 {i} 份回单文件名不符合回单格式，已跳过")
                continue
            date_str, payee = parts[1], parts[2]
            amount_str = parts[3].replace('元', '').replace(',', '')
            problems = []
            try:
                amount = float(amount_str)
                if not math.isfinite(amount) or amount <= 0:      # nan / inf / 0 / 负数都不能作凭证
                    raise ValueError(amount_str)
            except ValueError:
                amount = None
                problems.append(f"文件名金额无法解析({parts[3]})")

            source = '受信任发件人' if sender.lower() in trusted else '未验证来源回单'
            if source != '受信任发件人':
                problems.append(f"发件人不在受信任清单({sender or '未知'})")

            memo = None
            if text is None or not str(text).strip():
                content = '未提供正文'
                problems.append('未提供回单正文，无法核对交易日期与金额')
            else:
                n_before = len(problems)
                dm, am = date_pattern.search(text), amount_pattern.search(text)
                if not dm or not am:
                    problems.append('正文缺少交易日期或交易金额')
                else:
                    try:
                        t_amt = float(am.group(1).replace(',', ''))
                        if not math.isfinite(t_amt) or t_amt <= 0:
                            raise ValueError(am.group(1))
                    except ValueError:
                        t_amt = None
                        problems.append(f"正文金额无法解析({am.group(1)})")
                    if dm.group(1) != date_str:
                        problems.append(f"日期不符(文件名{date_str}/正文{dm.group(1)})")
                    if amount is not None and t_amt is not None and abs(t_amt - amount) > 0.005:
                        problems.append(f"金额不符(文件名{amount:,.2f}/正文{t_amt:,.2f})")
                content = '一致' if len(problems) == n_before else '不一致'
                memo = memo_pattern.search(text)
            bank = next((b for b in bank_names if text and b in text), '')

            records.append({
                '日期': date_str,
                '收款方': payee,
                '金额': amount,
                '摘要': memo.group(1).strip() if memo else '',
                '银行': bank,
                '文件名': fname,
                '发件人': sender,
                '来源': source,
                '正文核对': content,
                '问题': '；'.join(problems),
                '可作凭证': not problems,
            })

        except Exception as e:
            print(f"  ⚠️ 第 {i} 份回单解析失败: {type(e).__name__}")

    df = pd.DataFrame(records, columns=['日期', '收款方', '金额', '摘要', '银行', '文件名',
                                        '发件人', '来源', '正文核对', '问题', '可作凭证'])
    n_ok = int(df['可作凭证'].sum()) if len(df) else 0
    print(f"  📄 共解析 {len(df)} 份回单，可作凭证 {n_ok} 份，未验证来源 {int((df['来源'] == '未验证来源回单').sum()) if len(df) else 0} 份")
    return df


# ============================================================
# Step 3: 自动对账
# ============================================================

def reconcile(
    erp_df: pd.DataFrame,
    bank_df: pd.DataFrame,
    tolerance: float = 0.01,
    similarity_threshold: float = 0.6,
) -> Dict:
    """三维度自动对账引擎。

    匹配优先级：
      1. 参考号精确匹配（ERP 凭证号 PZ-xxxx ↔ 银行参考号 BK-xxxx）
      2. 日期+金额模糊匹配（同一天，金额差异在 tolerance 以内）
      3. 摘要相似度匹配（SequenceMatcher ratio > similarity_threshold）

    Args:
        erp_df: ERP 总账 DataFrame（需含: 凭证号, 日期, 摘要, 借方金额, 贷方金额, 供应商, 部门）
        bank_df: 银行对账单 DataFrame（需含: 交易流水号, 交易日期, 交易摘要, 收入, 支出, 参考号）
        tolerance: 金额匹配容差比例（默认 1%）
        similarity_threshold: 摘要相似度阈值（默认 0.6）

    Returns:
        dict: {
            'matched_df': 匹配成功明细 DataFrame,
            'erp_only_df': 仅 ERP 有的记录,
            'bank_only_df': 仅银行有的记录,
            'stats': {
                'total_erp', 'total_bank', 'matched', 'erp_only', 'bank_only',
                'match_rate', 'match_by_ref', 'match_by_amount', 'match_by_text'
            }
        }
    """
    # 标准化金额列
    erp = erp_df.copy()
    bank = bank_df.copy()
    erp['金额'] = erp.apply(
        lambda r: r['借方金额'] if r['借方金额'] > 0 else r['贷方金额'], axis=1
    )
    bank['金额'] = bank.apply(
        lambda r: r['收入'] if r['收入'] > 0 else r['支出'], axis=1
    )

    matched = []
    erp_matched_idx = set()
    bank_matched_idx = set()
    match_by_ref = 0
    match_by_amount = 0
    match_by_text = 0

    def _add_match(ei, erow, bi, brow, method):
        matched.append({
            'ERP凭证号': erow['凭证号'],
            '银行流水号': brow['交易流水号'],
            '匹配方式': method,
            '日期': erow['日期'],
            'ERP金额': erow['金额'],
            '银行金额': brow['金额'],
            '差异': round(abs(erow['金额'] - brow['金额']), 2),
            'ERP摘要': erow['摘要'],
            '银行摘要': brow['交易摘要'],
            '供应商': erow.get('供应商', ''),
            '部门': erow.get('部门', ''),
        })
        erp_matched_idx.add(ei)
        bank_matched_idx.add(bi)

    # ---- 第一轮：参考号精确匹配 ----
    for ei, erow in erp.iterrows():
        if ei in erp_matched_idx:
            continue
        erp_ref = str(erow['凭证号']).replace('PZ', 'BK')
        for bi, brow in bank.iterrows():
            if bi in bank_matched_idx:
                continue
            if str(brow['参考号']) == erp_ref:
                _add_match(ei, erow, bi, brow, '参考号精确匹配')
                match_by_ref += 1
                break

    # ---- 第二轮：日期+金额匹配 ----
    for ei, erow in erp.iterrows():
        if ei in erp_matched_idx:
            continue
        for bi, brow in bank.iterrows():
            if bi in bank_matched_idx:
                continue
            if str(erow['日期']) == str(brow['交易日期']):
                if erow['金额'] > 0 and abs(erow['金额'] - brow['金额']) / erow['金额'] < tolerance:
                    _add_match(ei, erow, bi, brow, '日期+金额匹配')
                    match_by_amount += 1
                    break

    # ---- 第三轮：摘要相似度匹配 ----
    for ei, erow in erp.iterrows():
        if ei in erp_matched_idx:
            continue
        best_score = 0
        best_match = None
        for bi, brow in bank.iterrows():
            if bi in bank_matched_idx:
                continue
            sim = SequenceMatcher(
                None, str(erow['摘要']), str(brow['交易摘要'])
            ).ratio()
            if sim > similarity_threshold and sim > best_score:
                best_score = sim
                best_match = (bi, brow)
        if best_match:
            bi, brow = best_match
            _add_match(ei, erow, bi, brow, f'摘要模糊匹配({best_score:.0%})')
            match_by_text += 1

    # 构造结果
    matched_df = pd.DataFrame(matched)
    erp_only_df = erp[~erp.index.isin(erp_matched_idx)][
        ['凭证号', '日期', '摘要', '金额', '供应商', '部门']
    ].copy()
    bank_only_df = bank[~bank.index.isin(bank_matched_idx)][
        ['交易流水号', '交易日期', '交易摘要', '金额', '对方账户']
    ].copy()

    total_comparable = max(len(bank), 1)
    stats = {
        'total_erp': len(erp),
        'total_bank': len(bank),
        'matched': len(matched_df),
        'erp_only': len(erp_only_df),
        'bank_only': len(bank_only_df),
        'match_rate': round(len(matched_df) / total_comparable * 100, 1),
        'match_by_ref': match_by_ref,
        'match_by_amount': match_by_amount,
        'match_by_text': match_by_text,
    }

    print(f"\n📊 对账结果:")
    print(f"  ✅ 匹配成功: {stats['matched']} 笔 ({stats['match_rate']}%)")
    print(f"     参考号匹配: {match_by_ref} | 金额匹配: {match_by_amount} | 摘要匹配: {match_by_text}")
    print(f"  📌 仅ERP有: {stats['erp_only']} 笔")
    print(f"  📌 仅银行有: {stats['bank_only']} 笔")

    return {
        'matched_df': matched_df,
        'erp_only_df': erp_only_df,
        'bank_only_df': bank_only_df,
        'stats': stats,
    }


# ============================================================
# Step 4: 分析
# ============================================================

def analyze_aging(
    df: pd.DataFrame,
    age_col: str = '账龄(天)',
    amount_col: str = '未收金额',
    bins: Optional[List[int]] = None,
) -> List[Dict]:
    """账龄分析。

    按账龄区间汇总金额，输出通用图表数据格式 [{name, y}]（仪表盘模板内转换为 ECharts）。

    Args:
        df: 应收/应付 DataFrame
        age_col: 账龄列名
        amount_col: 金额列名
        bins: 账龄区间边界，默认 [0, 30, 60, 90, +∞]

    Returns:
        list: [{'name': '0-30天', 'y': 金额}, ...]
    """
    if bins is None:
        bins = [0, 30, 60, 90, float('inf')]
    labels = ['0-30天', '30-60天', '60-90天', '90天以上']

    if len(bins) - 1 != len(labels):
        labels = [f'{bins[i]}-{bins[i+1]}天' for i in range(len(bins) - 1)]

    result = {label: 0.0 for label in labels}

    for _, row in df.iterrows():
        age = row[age_col]
        amt = row[amount_col]
        for i in range(len(bins) - 1):
            if bins[i] <= age < bins[i + 1]:
                result[labels[i]] += amt
                break

    return [{'name': k, 'y': round(v, 2)} for k, v in result.items()]


def generate_dashboard_data(
    recon_result: Dict,
    ar_df: pd.DataFrame,
    ap_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    erp_df: pd.DataFrame,
) -> Dict:
    """生成仪表盘 JSON 数据。

    Args:
        recon_result: reconcile() 的返回值
        ar_df: 应收账款 DataFrame
        ap_df: 应付账款 DataFrame
        daily_df: 资金日报 DataFrame
        erp_df: ERP 总账 DataFrame

    Returns:
        dict: 包含 kpi, recon_pie, ar_aging, ap_aging, daily, dept_expense,
              vendor_top10, unmatched_items
    """
    stats = recon_result['stats']

    # KPI
    kpi = {
        'match_rate': stats['match_rate'],
        'unmatched_count': stats['erp_only'] + stats['bank_only'],
        'ar_overdue': float(ar_df[ar_df['账龄(天)'] > 30]['未收金额'].sum()) if len(ar_df) > 0 else 0,
        'ap_overdue': float(ap_df[ap_df['账龄(天)'] > 30]['未付金额'].sum()) if len(ap_df) > 0 else 0,
        'cash_balance': float(daily_df['期末余额'].iloc[-1]) if len(daily_df) > 0 else 0,
    }

    # 对账饼图
    recon_pie = [
        {'name': '匹配成功', 'y': stats['matched']},
        {'name': '仅ERP有', 'y': stats['erp_only']},
        {'name': '仅银行有', 'y': stats['bank_only']},
    ]

    # 账龄分析
    ar_aging = analyze_aging(ar_df, '账龄(天)', '未收金额') if len(ar_df) > 0 else []
    ap_aging = analyze_aging(ap_df, '账龄(天)', '未付金额') if len(ap_df) > 0 else []

    # 资金走势
    daily = {
        'dates': daily_df['日期'].astype(str).tolist(),
        'balance': daily_df['期末余额'].tolist(),
        'income': daily_df['当日收入'].tolist(),
        'expense': daily_df['当日支出'].tolist(),
    } if len(daily_df) > 0 else {'dates': [], 'balance': [], 'income': [], 'expense': []}

    # 部门费用
    erp_df_calc = erp_df.copy()
    erp_df_calc['金额'] = erp_df_calc.apply(
        lambda r: r['借方金额'] if r['借方金额'] > 0 else r['贷方金额'], axis=1
    )
    dept_data = erp_df_calc.groupby('部门')['金额'].sum().sort_values(ascending=False)
    dept_expense = [{'name': k, 'y': round(v, 2)} for k, v in dept_data.items()]

    # 供应商 Top10
    vendor_data = erp_df_calc.groupby('供应商')['金额'].sum().sort_values(ascending=False).head(10)
    vendor_top10 = [{'name': k, 'y': round(v, 2)} for k, v in vendor_data.items()]

    # 未匹配明细
    bank_only = recon_result['bank_only_df']
    unmatched = bank_only.head(20).to_dict('records') if len(bank_only) > 0 else []

    return {
        'kpi': kpi,
        'recon_pie': recon_pie,
        'ar_aging': ar_aging,
        'ap_aging': ap_aging,
        'daily': daily,
        'dept_expense': dept_expense,
        'vendor_top10': vendor_top10,
        'unmatched_items': unmatched,
    }


# ============================================================
# Step 5: 报告生成
# ============================================================

# 电子表格公式注入防护：以 = + - @ 制表符 回车 开头的文本会被 Excel/WPS 当作公式执行，
# 写出前在前面加单引号使其按文本显示。只处理字符串单元格，数值单元格不变。
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')


def _neutralize_formula(value):
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """返回副本：文本列（object / string 类型）里的字符串做公式注入防护，其余列原样保留。"""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_object_dtype(out[col]) or pd.api.types.is_string_dtype(out[col]):
            out[col] = out[col].map(_neutralize_formula)
    return out


def generate_excel_report(
    recon_result: Dict,
    ar_df: pd.DataFrame,
    ap_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    output_path: str,
    audit: bool = True,
) -> str:
    """生成对账结果 Excel（4 个 sheet）。

    Sheets:
      - 匹配成功明细
      - 仅ERP明细
      - 仅银行明细
      - 汇总统计

    Args:
        recon_result: reconcile() 的返回值
        ar_df: 应收账款 DataFrame
        ap_df: 应付账款 DataFrame
        daily_df: 资金日报 DataFrame
        output_path: Excel 输出路径（只能直接位于其所在输出目录内、后缀 .xlsx；目录为空时为 当前目录/finance_outputs；
                     越界或符号链接抛 OutputPathError；文件权限 0600）
        audit: True（单独调用时的默认）= 写出后向同目录 audit_log.jsonl 追加一行审计
               （匹配统计计数与输出哈希，不含输入文件哈希）；run_full_pipeline 传 False，由其统一记录

    Returns:
        str: 生成的文件路径
    """
    stats = recon_result['stats']

    # 汇总统计 DataFrame
    stats_data = {
        '指标': [
            'ERP记录总数', '银行流水总数', '匹配成功笔数',
            '仅ERP有', '仅银行有', '匹配率',
            '参考号匹配', '金额匹配', '摘要匹配',
            'ERP借方合计', 'ERP贷方合计',
            '应收账款总额', '应收未收金额',
            '应付账款总额', '应付未付金额',
            '资金期末余额',
        ],
        '值': [
            stats['total_erp'], stats['total_bank'], stats['matched'],
            stats['erp_only'], stats['bank_only'], f"{stats['match_rate']}%",
            stats['match_by_ref'], stats['match_by_amount'], stats['match_by_text'],
            '', '',  # ERP 合计由调用方填充
            f"¥{ar_df['发票金额'].sum():,.2f}" if len(ar_df) > 0 else '无数据',
            f"¥{ar_df['未收金额'].sum():,.2f}" if len(ar_df) > 0 else '无数据',
            f"¥{ap_df['账单金额'].sum():,.2f}" if len(ap_df) > 0 else '无数据',
            f"¥{ap_df['未付金额'].sum():,.2f}" if len(ap_df) > 0 else '无数据',
            f"¥{daily_df['期末余额'].iloc[-1]:,.2f}" if len(daily_df) > 0 else '无数据',
        ],
    }
    df_stats = pd.DataFrame(stats_data)

    import io, sys
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
        _excel_safe(recon_result['matched_df']).to_excel(writer, sheet_name='匹配成功明细', index=False)
        _excel_safe(recon_result['erp_only_df']).to_excel(writer, sheet_name='仅ERP明细', index=False)
        _excel_safe(recon_result['bank_only_df']).to_excel(writer, sheet_name='仅银行明细', index=False)
        _excel_safe(df_stats).to_excel(writer, sheet_name='汇总统计', index=False)
    # 受控写出：先在内存生成，再以 O_NOFOLLOW + 0600 写入输出目录内的 .xlsx
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    output_path = og.resolve_output(os.path.basename(output_path), os.path.dirname(output_path) or None,
                                    suffixes=('.xlsx',))
    og.write_bytes(output_path, buf.getvalue())
    if audit:
        # 单独调用：只记匹配统计计数与输出哈希，不记明细、金额、收款方；本函数不接收输入文件路径，故无输入哈希
        og.append_audit(
            os.path.dirname(output_path), __file__,
            {'entry': 'generate_excel_report'}, [], [output_path],
            {k: stats[k] for k in ('matched', 'erp_only', 'bank_only')},
            og.sha256_json(stats),
        )

    print(f"  ✅ 对账结果 Excel 已生成: {output_path}")
    return output_path


def generate_email_body(stats: Dict, recon_result: Dict) -> str:
    """生成对账报告邮件正文（HTML 格式）。

    Args:
        stats: 对账统计 dict
        recon_result: reconcile() 的返回值

    Returns:
        str: HTML 邮件正文
    """
    now = datetime.now().strftime('%Y-%m-%d')
    match_color = '#36B37E' if stats['match_rate'] >= 80 else '#FF9900' if stats['match_rate'] >= 60 else '#DE350B'

    html = f"""<div style="font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif; max-width: 680px;">
<h2 style="color: #0052CC; border-bottom: 2px solid #0052CC; padding-bottom: 8px;">
  📊 月末财务对账报告
</h2>

<h3>📋 对账概况</h3>
<table style="width:100%; border-collapse: collapse; margin: 12px 0; font-size: 13px;">
<tr style="background: #e8f4fd;">
  <td style="padding:8px; border:1px solid #ddd; font-weight:bold;">ERP 记录总数</td>
  <td style="padding:8px; border:1px solid #ddd; text-align:right;">{stats['total_erp']} 笔</td>
</tr>
<tr>
  <td style="padding:8px; border:1px solid #ddd; font-weight:bold;">银行流水总数</td>
  <td style="padding:8px; border:1px solid #ddd; text-align:right;">{stats['total_bank']} 笔</td>
</tr>
<tr style="background: #e8f4fd;">
  <td style="padding:8px; border:1px solid #ddd; font-weight:bold;">✅ 匹配成功</td>
  <td style="padding:8px; border:1px solid #ddd; text-align:right; color:{match_color}; font-weight:bold;">
    {stats['matched']} 笔（{stats['match_rate']}%）
  </td>
</tr>
<tr>
  <td style="padding:8px; border:1px solid #ddd; font-weight:bold;">⚠️ 仅 ERP 有</td>
  <td style="padding:8px; border:1px solid #ddd; text-align:right; color: #FF9900;">{stats['erp_only']} 笔</td>
</tr>
<tr style="background: #e8f4fd;">
  <td style="padding:8px; border:1px solid #ddd; font-weight:bold;">⚠️ 仅银行有</td>
  <td style="padding:8px; border:1px solid #ddd; text-align:right; color: #DE350B;">{stats['bank_only']} 笔</td>
</tr>
</table>

<h3>🔍 需要关注</h3>
<ol style="font-size: 13px;">
  <li>{stats['erp_only'] + stats['bank_only']} 笔未匹配交易需人工确认</li>
  <li>详细对账明细见附件 Excel 或 Quick Desktop 仪表盘</li>
</ol>

<p style="color: #666; font-size: 12px;">
  本报告由 Amazon Quick Desktop 财务对账助手自动生成 | {now}
</p>
</div>"""
    return html


def generate_notification_text(stats: Dict) -> str:
    """生成对账完成通知草稿（Markdown）。只生成文本，由使用者确认后自行在聊天工具中发送。

    Args:
        stats: 对账统计 dict

    Returns:
        str: Markdown 格式通知文本
    """
    return f"""📊 **月末财务对账完成**

✅ **匹配率 {stats['match_rate']}%** | {stats['matched']}笔匹配成功
⚠️ **{stats['erp_only'] + stats['bank_only']}笔未匹配** 待人工确认（ERP独有{stats['erp_only']}笔 + 银行独有{stats['bank_only']}笔）

📎 详细报告见对账报告邮件与对账结果 Excel，交互式仪表盘请在 Quick Desktop 中打开。"""


# ============================================================
# 主流程编排（在 Quick Desktop 中按步骤调用）
# ============================================================

def run_full_pipeline(
    erp_file_path: str,
    receipt_pdf_paths: Optional[List] = None,
    output_dir: Optional[str] = None,
    trusted_senders=(),
) -> Dict:
    """执行完整的对账流程（Step 3-6；Step 6 只写本地分发草稿，不发送）。

    注意：Step 1（OneDrive 下载）和 Step 2（Outlook 提取）需要在
    Quick Desktop 中通过连接器工具完成，本函数从本地文件开始。

    Args:
        erp_file_path: ERP 数据包 Excel 路径
        receipt_pdf_paths: 银行回单列表（可选），格式见 parse_bank_receipts()：
                           {'path', 'sender', 'text'} 字典或 PDF 路径字符串
        output_dir: 输出目录；None = 当前目录下 finance_outputs/（新建为 0700）。Excel 与两份草稿只写到该目录内
                    （0600，目标为符号链接时抛 OutputPathError），并向该目录 audit_log.jsonl 追加审计记录
        trusted_senders: 受信任的银行回单发件人邮箱清单（SKILL.md 输入 trusted_senders）；
                         不在清单内的回单列为「未验证来源回单」，不作为对账凭证

    Returns:
        dict: {
            'recon_result': 对账结果,
            'dashboard_data': 仪表盘数据,
            'excel_path': Excel 文件路径,
            'email_body': 邮件正文 HTML,
            'notification': 通知文本,
            'draft_paths': 本地分发草稿路径 {email_draft, notification_draft}（只写文件，不发送）,
            'receipt_check': 回单核验结果 {回单数, 可作凭证, 未验证来源回单, 正文不符, 未提供正文},
        }
    """
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import output_guard as og
    base = og.resolve_base(output_dir)
    print("=" * 60)
    print("  月末财务对账引擎 v2.0")
    print("=" * 60)

    # Step 1: 加载数据
    print("\n📂 Step 1: 加载 ERP 数据...")
    data = load_erp_data(erp_file_path)

    # Step 2: 解析回单（可选）
    if receipt_pdf_paths:
        print(f"\n📄 Step 2: 解析银行回单 ({len(receipt_pdf_paths)} 份)...")
        receipts_all = parse_bank_receipts(receipt_pdf_paths, trusted_senders)
        # 只有通过来源核验与内容核对的回单才可作为银行侧凭证
        receipts_df = receipts_all[receipts_all['可作凭证']].copy()
    else:
        print("\n📄 Step 2: 无回单 PDF，跳过")
        receipts_all = receipts_df = pd.DataFrame(columns=['文件名', '来源', '正文核对', '问题', '可作凭证'])
    receipt_check = {
        '回单数': len(receipts_all),
        '可作凭证': len(receipts_df),
        '未验证来源回单': sorted(receipts_all.loc[receipts_all['来源'] == '未验证来源回单', '文件名'].tolist()),
        '正文不符': sorted(receipts_all.loc[receipts_all['正文核对'] == '不一致', '文件名'].tolist()),
        # 未提供正文的回单不作为对账凭证，单列
        '未提供正文': sorted(receipts_all.loc[receipts_all['正文核对'] == '未提供正文', '文件名'].tolist()),
    }
    if receipt_check['未验证来源回单'] or receipt_check['正文不符'] or receipt_check['未提供正文']:
        print(f"  ⚠️ 未验证来源回单 {len(receipt_check['未验证来源回单'])} 份、正文不符 {len(receipt_check['正文不符'])} 份、"
              f"未提供正文 {len(receipt_check['未提供正文'])} 份，均不作为对账凭证")

    # Step 3: 自动对账
    print("\n🔄 Step 3: 执行三维度自动对账...")
    recon_result = reconcile(
        data['erp_df'], data['bank_df'],
        tolerance=0.01, similarity_threshold=0.6,
    )

    # Step 4: 生成仪表盘数据
    print("\n📊 Step 4: 生成仪表盘数据...")
    dashboard_data = generate_dashboard_data(
        recon_result, data['ar_df'], data['ap_df'],
        data['daily_df'], data['erp_df'],
    )

    # Step 5: 生成报告
    print("\n📝 Step 5: 生成报告...")
    excel_path = og.resolve_output('对账结果.xlsx', base, suffixes=('.xlsx',))
    # 分发草稿：只写本地文件，本函数和本 Skill 都不发送任何内容；由使用者确认后自行通过邮件或聊天工具分发
    draft_paths = {
        'email_draft': og.resolve_output('对账报告邮件草稿.html', base, suffixes=('.html',)),
        'notification_draft': og.resolve_output('对账通知草稿.md', base, suffixes=('.md',)),
    }
    generate_excel_report(
        recon_result, data['ar_df'], data['ap_df'],
        data['daily_df'], excel_path, audit=False,
    )

    stats = recon_result['stats']
    email_body = generate_email_body(stats, recon_result)
    notification = generate_notification_text(stats)
    og.write_text(draft_paths['email_draft'], email_body)
    og.write_text(draft_paths['notification_draft'], notification + '\n')
    # 审计留痕：只记文件名/角色与完整 SHA-256、计数结论，不记明细、金额、收款方
    og.append_audit(
        base, __file__,
        {'trusted_senders': len(trusted_senders or ()), 'receipts': len(receipt_pdf_paths or [])},
        [og.file_fingerprint(erp_file_path, 'ERP数据包')]
        + [og.file_fingerprint(r.get('path') if isinstance(r, dict) else r, f'回单PDF#{i}')
           for i, r in enumerate(receipt_pdf_paths or [], 1)],
        [excel_path, draft_paths['email_draft'], draft_paths['notification_draft']],
        {k: stats[k] for k in ('matched', 'erp_only', 'bank_only')} | {'可作凭证': receipt_check['可作凭证']},
        og.sha256_json(stats),
    )

    print("\n✅ 全部完成！")
    print(f"  对账结果 Excel: {excel_path}")
    print(f"  分发草稿（未发送）: {draft_paths['email_draft']}、{draft_paths['notification_draft']}")
    print(f"  仪表盘数据: {len(json.dumps(dashboard_data))} bytes")

    return {
        'recon_result': recon_result,
        'dashboard_data': dashboard_data,
        'excel_path': excel_path,
        'email_body': email_body,
        'notification': notification,
        'draft_paths': draft_paths,
        'receipt_check': receipt_check,
    }
