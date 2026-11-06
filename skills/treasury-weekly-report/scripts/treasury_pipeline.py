#!/usr/bin/env python3
"""
treasury_pipeline.py — 资金周报核心计算引擎
从5份Excel数据源提取16维度指标，输出结构化JSON供报告渲染。
"""
import pandas as pd
import numpy as np
import json
import math
from datetime import datetime


def load_and_validate(file_paths: dict) -> dict:
    data = {}
    issues = []
    try:
        data['flow'] = pd.read_excel(file_paths['flow'], sheet_name='流水明细')
        data['accounts'] = pd.read_excel(file_paths['flow'], sheet_name='账户信息')
        data['flow']['日期'] = pd.to_datetime(data['flow']['日期'])
        required_cols = ['流水号','日期','子公司名称','收付方向','金额','摘要/用途']
        missing = [c for c in required_cols if c not in data['flow'].columns]
        if missing: issues.append(f"流水明细缺少列: {missing}")
    except Exception as e:
        issues.append(f"银行流水读取失败: {e}")
    try:
        data['invest'] = pd.read_excel(file_paths['invest'], sheet_name='在投理财')
        data['maturity'] = pd.read_excel(file_paths['invest'], sheet_name='本周到期')
    except Exception as e:
        issues.append(f"理财台账读取失败: {e}")
    try:
        data['plan'] = pd.read_excel(file_paths['plan'], sheet_name='本周资金计划')
    except Exception as e:
        issues.append(f"资金计划读取失败: {e}")
    try:
        data['alerts'] = pd.read_excel(file_paths['alert'], sheet_name='账户预警规则')
    except Exception as e:
        issues.append(f"预警规则读取失败: {e}")
    try:
        data['last_daily'] = pd.read_excel(file_paths['last_week'], sheet_name='上周每日汇总')
        data['last_kpi'] = pd.read_excel(file_paths['last_week'], sheet_name='上周关键指标')
    except Exception as e:
        issues.append(f"上周汇总读取失败: {e}")
    data['_issues'] = issues
    return data


def compute_core_metrics(data: dict) -> dict:
    df_flow = data['flow']
    df_plan = data['plan']
    last_kpi = data['last_kpi']
    dates = sorted(df_flow['日期'].dt.date.unique())
    weekdays_map = {0:'周一',1:'周二',2:'周三',3:'周四',4:'周五',5:'周六',6:'周日'}
    plan_totals = df_plan[df_plan['类别'] == '【合计】'].iloc[0] if '【合计】' in df_plan['类别'].values else None
    daily_income = df_flow[df_flow['收付方向']=='收'].groupby(df_flow[df_flow['收付方向']=='收']['日期'].dt.date)['金额'].sum()
    daily_expense = df_flow[df_flow['收付方向']=='付'].groupby(df_flow[df_flow['收付方向']=='付']['日期'].dt.date)['金额'].sum()
    if plan_totals is not None:
        actual_income = plan_totals.get('实际收入(万)', daily_income.sum())
        actual_expense = plan_totals.get('实际支出(万)', daily_expense.sum())
        if pd.notna(actual_income) and daily_income.sum() > 0:
            income_ratio = daily_income / daily_income.sum()
            expense_ratio = daily_expense / daily_expense.sum()
        else:
            actual_income = daily_income.sum()
            actual_expense = daily_expense.sum()
            income_ratio = daily_income / daily_income.sum()
            expense_ratio = daily_expense / daily_expense.sum()
    else:
        actual_income = daily_income.sum()
        actual_expense = daily_expense.sum()
        income_ratio = daily_income / daily_income.sum()
        expense_ratio = daily_expense / daily_expense.sum()
    last_balance = 0
    if 'last_daily' in data:
        last_row = data['last_daily'].iloc[-1]
        last_balance = last_row.get('总资金余额(亿)', 0) * 10000
    daily_trend = []
    cum_balance = last_balance
    for d in dates:
        inc = actual_income * income_ratio.get(d, 0)
        exp = actual_expense * expense_ratio.get(d, 0)
        net = inc - exp
        cum_balance += net
        daily_trend.append({'\u65e5\u671f': str(d), '\u661f\u671f': weekdays_map.get(d.weekday(), ''), '\u6536\u5165_\u4e07': round(inc, 2), '\u652f\u51fa_\u4e07': round(exp, 2), '\u51c0\u6d41\u91cf_\u4e07': round(net, 2), '\u4f59\u989d_\u4e07': round(cum_balance, 2)})
    plan_clean = df_plan[df_plan['类别'] != '【合计】'].copy() if '【合计】' in df_plan['类别'].values else df_plan.copy()
    income_items = plan_clean[plan_clean['实际收入(万)'].notna()][['\u7c7b\u522b','实际收入(万)','备注']].to_dict('records')
    expense_items = plan_clean[plan_clean['实际支出(万)'].notna()][['\u7c7b\u522b','实际支出(万)','备注']].to_dict('records')
    last_kpi_dict = dict(zip(last_kpi['指标'], last_kpi['上周(W23)值']))
    return {'daily_trend': daily_trend, 'actual_income': actual_income, 'actual_expense': actual_expense, 'income_items': income_items, 'expense_items': expense_items, 'last_kpi': last_kpi_dict, 'dates': [str(d) for d in dates], 'last_balance_wan': last_balance}


def detect_anomalies(data: dict) -> list:
    df_flow = data['flow']
    df_alerts = data['alerts']
    anomalies = []
    merged = df_flow.merge(df_alerts[['子公司','单笔支付上限(万)']], left_on='子公司名称', right_on='子公司', how='left')
    over_limit = merged[(merged['收付方向']=='付') & (merged['金额'] > merged['单笔支付上限(万)'])]
    for _, row in over_limit.iterrows():
        anomalies.append({'type': 'W003', 'level': '红色', 'description': '单笔超限', 'date': str(row['日期'].date()), 'subsidiary': row['子公司名称'], 'amount': row['金额'], 'limit': row['单笔支付上限(万)']})
    large_no_summary = df_flow[(df_flow['金额'] > 1000) & (df_flow['摘要/用途'].isna() | (df_flow['摘要/用途'] == ''))]
    for _, row in large_no_summary.iterrows():
        anomalies.append({'type': 'W004', 'level': '橙色', 'description': '大额摘要缺失', 'date': str(row['日期'].date()), 'subsidiary': row['子公司名称'], 'amount': row['金额'], 'counterparty': row.get('对手方名称',''), 'direction': row['收付方向']})
    df_flow['_hour'] = pd.to_datetime(df_flow['时间'], format='%H:%M:%S', errors='coerce').dt.hour
    after_hours = df_flow[(df_flow['_hour'] >= 22) | (df_flow['_hour'] < 6)]
    for _, row in after_hours.iterrows():
        anomalies.append({'type': 'W005', 'level': '橙色', 'description': '异常时间交易', 'date': str(row['日期'].date()), 'time': str(row['时间']), 'subsidiary': row['子公司名称'], 'amount': row['金额']})
    return anomalies


def compute_risk_matrix(data: dict) -> dict:
    df_flow = data['flow']
    df_alerts = data['alerts']
    dates = sorted(df_flow['日期'].dt.date.unique())
    all_sub_risk = {}
    for sub in df_alerts['子公司'].tolist():
        sub_data = df_flow[df_flow['子公司名称'] == sub]
        if len(sub_data) == 0:
            matches = df_flow[df_flow['子公司名称'].str.contains(sub[:4], na=False)]
            if len(matches) > 0: sub_data = matches
            else: continue
        daily_risks = []
        for d in dates:
            day = sub_data[sub_data['日期'].dt.date == d]
            if len(day) == 0: daily_risks.append(0); continue
            inc = day[day['收付方向']=='收']['金额'].sum()
            exp = day[day['收付方向']=='付']['金额'].sum()
            net_ratio = (exp - inc) / (exp + inc + 1)
            large_density = len(day[day['金额'] >= 5000]) / max(len(day), 1)
            risk = net_ratio * 40 + large_density * 100 * 30
            daily_risks.append(round(max(0, min(100, risk)), 1))
        all_sub_risk[sub] = daily_risks
    daily_rankings = {i: {} for i in range(len(dates))}
    for sub, risks in all_sub_risk.items():
        for i, r in enumerate(risks):
            daily_rankings[i][sub] = r
    for d_idx in range(len(dates)):
        values = list(daily_rankings[d_idx].values())
        for sub in daily_rankings[d_idx]:
            val = daily_rankings[d_idx][sub]
            pct = (sum(1 for v in values if v <= val) / len(values)) * 100
            daily_rankings[d_idx][sub] = round(pct, 0)
    heatmap = []
    for sub in all_sub_risk.keys():
        pcts = [daily_rankings[i].get(sub, 0) for i in range(len(dates))]
        heatmap.append({'name': sub[:6], 'full_name': sub, 'data': pcts, 'avg': float(np.mean(pcts))})
    heatmap.sort(key=lambda x: -x['avg'])
    return {'subsidiary_heatmap': heatmap[:15]}


def analyze_plan_execution(data: dict, threshold: float = 0.15) -> dict:
    df_plan = data['plan']
    plan_clean = df_plan[df_plan['类别'] != '【合计】'].copy() if '【合计】' in df_plan['类别'].values else df_plan.copy()
    deviations = []
    for _, row in plan_clean.iterrows():
        exec_rate_str = str(row.get('执行率', ''))
        if '%' in exec_rate_str:
            rate = float(exec_rate_str.replace('%','')) / 100
            deviation = abs(rate - 1.0)
            if deviation > threshold:
                deviations.append({'类别': row['类别'], '执行率': exec_rate_str, '备注': row.get('备注', ''), '方向': '超额' if rate > 1 else '不足'})
    return {'deviations': deviations}


def norm_cdf(x):
    # 标准正态分布函数 N(x) = 0.5·(1 + erf(x/√2))；math.erf 为标准库函数，无需 scipy
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def forward_rate(spot, r_d, r_f, T):
    return spot * (1 + r_d * T) / (1 + r_f * T)

def gk_option(S, K, T, rd, rf, sigma, option_type='call'):
    d1 = (math.log(S/K) + (rd - rf + sigma**2/2)*T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == 'call':
        price = S * math.exp(-rf*T) * norm_cdf(d1) - K * math.exp(-rd*T) * norm_cdf(d2)
    else:
        price = K * math.exp(-rd*T) * norm_cdf(-d2) - S * math.exp(-rf*T) * norm_cdf(-d1)
    return price


def run_pipeline(file_paths: dict, out_path: str = None, out_dir: str = None) -> dict:
    print("Step 1: Loading data...")
    data = load_and_validate(file_paths)
    if data['_issues']:
        print(f"  Issues: {len(data['_issues'])} 处")
    print("Step 2: Computing core metrics...")
    core = compute_core_metrics(data)
    print("Step 3: Detecting anomalies...")
    anomalies = detect_anomalies(data)
    print("Step 4: Computing risk matrix...")
    risk = compute_risk_matrix(data)
    print("Step 5: Analyzing plan execution...")
    plan_exec = analyze_plan_execution(data)
    print("Step 6: Persisting audit trail...")
    result = {'core': core, 'anomalies': anomalies, 'risk': risk, 'plan_execution': plan_exec, 'data_issues': data['_issues'], 'transaction_count': len(data.get('flow', [])), 'subsidiary_count': data['flow']['子公司名称'].nunique() if 'flow' in data else 0, 'account_count': data['flow']['账户号'].nunique() if 'flow' in data else 0}
    # 过程可回溯：把完整过程记录落盘（时间戳+五步指标+异常明细+数据问题），供审计追溯
    result['_audit'] = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'steps': ['load_and_validate', 'compute_core_metrics', 'detect_anomalies', 'compute_risk_matrix', 'analyze_plan_execution'],
        'data_issues': data['_issues'],
        'anomaly_count': (len(anomalies) if isinstance(anomalies, (list, dict)) else None),
    }
    if out_path:
        # 受控写出：只能写到输出目录（out_dir，默认 当前目录/finance_outputs）内的 .json 文件，0600，并追加审计日志
        import os, sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import output_guard as og
        path = og.resolve_output(out_path, out_dir)
        og.write_json(path, result)
        og.append_audit(os.path.dirname(path), __file__, {},
                        [og.file_fingerprint(v, k) for k, v in file_paths.items()], [path],
                        {'data_issues': len(result['data_issues']), 'anomaly_count': result['_audit']['anomaly_count']},
                        og.sha256_json(result))
        print(f"  Audit trail written to {path}")
    print("Pipeline complete")
    return result
