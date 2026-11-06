# -*- coding: utf-8 -*-
"""报表勾稽核对底稿渲染器 —— 将固化进 report-tieout-check skill。
把 check_report_tieout() 的校验结果 + 四表数据渲染成可归档的审计底稿 HTML。"""
from collections import Counter
import datetime as _dt
from html import escape as _html_escape


def render_tieout_worksheet(result, table_data, meta=None, out_path=None, out_dir=None, input_paths=None):
    """把报表勾稽校验结果渲染成可归档的审计底稿 HTML。

    Args:
        result (dict): check_report_tieout() 的返回（含 checks/passed/total/n_fail/all_tie/timestamp）。
        table_data (dict): 四表列示数据，键为 bs/inc/cf/re，值为 [(项目,金额,分区), ...]。
        meta (dict): 可选抬头信息，键可含 index/entity/as_of/basis/currency/source/fingerprint/method。
        out_path (str): 可选，写出 HTML 的文件名（只能直接位于输出目录内，后缀 .html/.htm；越界或符号链接抛 OutputPathError）。
        out_dir (str): 可选，输出目录；未给出时为 当前目录/finance_outputs。写出时同时向该目录 audit_log.jsonl 追加审计记录。
        input_paths (dict): 可选，四表 CSV 路径，键为 bs/inc/cf/re；给出时审计记录写入各表完整 SHA-256，未给出时审计记录的 inputs 为空。

    Returns:
        str: 底稿 HTML 字符串。
    """
    m = {
        "index": "WP-R2R-03", "entity": "示例集团（虚构）", "as_of": "",
        "basis": "企业会计准则", "currency": "人民币元", "source": "财务系统导出四表 CSV",
        "fingerprint": "", "method": "report-tieout-check v2",
    }
    if meta:
        m.update({k: v for k, v in meta.items() if v is not None})

    def esc(v):
        # CSV 单元格与调用方传入的 meta 都是外部输入，写进 HTML 前一律转义
        return _html_escape(str(v), quote=True)

    m = {k: esc(v) for k, v in m.items()}
    ts = esc(result.get("timestamp", "") or _dt.datetime.now().isoformat(timespec="seconds"))
    today = _dt.date.today().isoformat()
    tol_txt = esc(f"{result.get('tol', 0.5):g}")
    total = result["total"]; passed = result["passed"]; nfail = result["n_fail"]
    all_tie = result["all_tie"]
    by = Counter(c["类型"] for c in result["checks"])
    by_desc = "、".join(f"{esc(k)} {v} 项" for k, v in by.items())

    def money(v):
        s = f"{abs(v):,.2f}"
        return f'<span style="color:#dc2626">({s})</span>' if v < 0 else s

    def trows(data):
        out = []
        for name, amt, zone in data:
            is_tot = zone in ("小计", "合计")
            style = "font-weight:700;background:#f6f9fd;" if is_tot else ""
            indent = "" if is_tot else "padding-left:22px;"
            out.append(f'<tr style="{style}"><td style="{indent}">{esc(name)}</td>'
                       f'<td style="text-align:right;font-variant-numeric:tabular-nums">{money(amt)}</td>'
                       f'<td style="color:#8a94a8;font-size:11px">{esc(zone)}</td></tr>')
        return "\n".join(out)

    def crows(checks):
        out = []
        for i, c in enumerate(checks, 1):
            ok = c["通过"]
            rt = ('<span style="color:#16a34a;font-weight:700">✓ 相符</span>' if ok
                  else '<span style="color:#dc2626;font-weight:700">✗ 不符</span>')
            bg = "" if ok else "background:#fef2f2;"
            out.append(f'<tr style="{bg}"><td style="text-align:center;color:#8a94a8">{i}</td>'
                       f'<td>{esc(c["勾稽项"])}</td><td style="color:#64748b;white-space:nowrap">{esc(c["类型"])}</td>'
                       f'<td style="text-align:right;font-variant-numeric:tabular-nums">{c["左值"]:,.2f}</td>'
                       f'<td style="text-align:right;font-variant-numeric:tabular-nums">{c["右值"]:,.2f}</td>'
                       f'<td style="text-align:right;font-variant-numeric:tabular-nums">{c["差异"]:,.2f}</td>'
                       f'<td style="text-align:center">{rt}</td></tr>')
        return "\n".join(out)

    td = table_data
    if all_tie:
        concl_cls = "background:#f0fdf4;border:1px solid #bbf7d0"
        concl_big = ('<div style="font-size:14px;font-weight:800;color:#16a34a;margin-bottom:4px">'
                     f'✓ 全部 {total} 项勾稽关系相符，报表内部一致</div>')
        concl_body = (f'本期四表之间的 {total} 项勾稽关系（{by_desc}）经确定性核对<b>全部相符</b>，差异均为 0；'
                      f'独立自检闭合（相符 {passed} + 不符 {nfail} = 总计 {total}）。'
                      '<div style="margin-top:7px"><b>处理意见：</b>报表勾稽自检通过，<b>具备对外报出条件</b>。'
                      '建议连同本底稿一并归档备查。</div>')
    else:
        concl_cls = "background:#fef2f2;border:1px solid #fecaca"
        dq = result.get("data_issues") or []
        concl_big = ('<div style="font-size:14px;font-weight:800;color:#dc2626;margin-bottom:4px">'
                     f'✗ {nfail} 项勾稽不符' + (f'，数据质量问题 {len(dq)} 处' if dq else '') + '，报表存在内部不一致</div>')
        items = "".join(f'<li>{esc(c["勾稽项"])}（差异 {c["差异"]:,.2f}'
                        + (f'；缺数据：{esc("、".join(c["缺数据"]))}' if c.get("缺数据") else '') + '）</li>'
                        for c in result["fails"])
        dq_items = "".join(f'<li>{esc(d.get("文件", ""))} 第 {esc(d.get("行号") or "—")} 行 {esc(d.get("项目") or "")}：'
                           + f'{esc(d.get("问题", ""))}（原值 {esc(d.get("原值"))}）</li>' for d in dq)
        concl_body = (f'本期 {total} 项勾稽关系中有 <b>{nfail} 项不符</b>（{by_desc}）；'
                      f'独立自检闭合（相符 {passed} + 不符 {nfail} = 总计 {total}）。'
                      '<div style="margin-top:7px"><b>不符项：</b></div>'
                      f'<ul style="margin:4px 0 4px 20px">{items}</ul>'
                      + (('<div style="margin-top:7px"><b>数据质量问题（金额无法解析或必需项目缺失，未按 0 计）：</b></div>'
                          f'<ul style="margin:4px 0 4px 20px">{dq_items}</ul>') if dq else '')
                      + '<div style="margin-top:4px"><b>处理意见：</b>报表<b>不具备对外报出条件</b>，'
                      '须先追查差异来源并修正，重核通过后方可报出。</div>')

    css = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'PingFang SC',-apple-system,'Microsoft YaHei',sans-serif;background:#f1f4f9;color:#1a2436;padding:28px;line-height:1.55}
.sheet{max-width:1000px;margin:0 auto;background:#fff;border:1px solid #d5deeb;box-shadow:0 2px 12px rgba(20,35,66,.08)}
.hdr{border-bottom:2px solid #1a2436;padding:18px 26px 14px}
.hdr .org{font-size:13px;color:#5f6b82;letter-spacing:.5px}
.hdr h1{font-size:21px;font-weight:800;margin:3px 0 2px}
.hdr .idx{position:absolute;top:18px;right:26px;font-size:12px;font-family:Menlo,monospace;color:#1a2436;border:1.5px solid #1a2436;border-radius:4px;padding:3px 10px;font-weight:700}
.hdr-wrap{position:relative}
.meta{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid #d5deeb}
.meta div{padding:8px 14px;font-size:12px;border-right:1px solid #eef2f7}
.meta div:last-child{border-right:none}
.meta .k{color:#8a94a8;font-size:10.5px;margin-bottom:1px}.meta .v{font-weight:600}
.sec{padding:16px 26px}
.sec h2{font-size:14px;font-weight:700;color:#1a2436;margin-bottom:9px;padding-left:9px;border-left:3px solid #2b6cb0}
table{width:100%;border-collapse:collapse;font-size:12px}
.four{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.four h3{font-size:12.5px;font-weight:700;margin-bottom:5px;color:#2b6cb0}
.four table td{padding:3px 8px;border-bottom:1px solid #f0f3f8}
.four table th{padding:4px 8px;background:#eef3f9;font-size:10.5px;color:#5f6b82;text-align:left;border-bottom:1px solid #d5deeb}
.ck table{font-size:11.5px}
.ck td,.ck th{padding:6px 8px;border-bottom:1px solid #eef2f7}
.ck th{background:#1a2436;color:#fff;font-weight:600;font-size:11px;text-align:left}
.blind{background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:11px 15px;font-size:11.5px;color:#78560a;margin-top:10px}
.sign{display:grid;grid-template-columns:repeat(4,1fr);border-top:2px solid #1a2436;margin-top:4px}
.sign div{padding:12px 14px;font-size:11.5px;border-right:1px solid #eef2f7;min-height:58px}
.sign div:last-child{border-right:none}
.sign .role{color:#8a94a8;font-size:10.5px;margin-bottom:3px}
.sign .line{margin-top:16px;border-bottom:1px solid #c3ccd9}
.tag{display:inline-block;font-size:11px;color:#9c4221;background:#fff4e6;border:1px solid #dd6b20;border-radius:4px;padding:2px 9px;font-weight:700}
.foot{text-align:center;font-size:10.5px;color:#8a94a8;padding:12px}
.note{font-size:11px;color:#8a94a8;margin-top:6px}
.concl{border-radius:6px;padding:13px 16px;font-size:12.5px}
"""

    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>报表勾稽核对底稿 · {m["index"]}</title>'
        f'<style>{css}.concl{{{concl_cls}}}</style></head><body>'
        '<div class="sheet">'
        '<div class="hdr-wrap"><div class="hdr">'
        f'<div class="idx">索引号 {m["index"]}</div>'
        f'<div class="org">{m["entity"]}</div>'
        '<h1>财务报表勾稽核对底稿</h1>'
        '<div style="font-size:12px;color:#5f6b82">Financial Statement Tie-out Working Paper · 对外报出前自检</div>'
        '</div></div>'
        '<div class="meta">'
        f'<div><div class="k">编制主体</div><div class="v">{m["entity"]}</div></div>'
        f'<div><div class="k">报表时点</div><div class="v">{m["as_of"] or "—"}</div></div>'
        f'<div><div class="k">编制基础</div><div class="v">{m["basis"]}</div></div>'
        f'<div><div class="k">货币单位</div><div class="v">{m["currency"]}</div></div>'
        '</div>'
        '<div class="meta">'
        f'<div><div class="k">数据来源</div><div class="v">{m["source"]}</div></div>'
        f'<div><div class="k">输入指纹 SHA-256</div><div class="v" style="font-family:Menlo,monospace">{m["fingerprint"] or "—"}</div></div>'
        f'<div><div class="k">核对方法</div><div class="v">{m["method"]}</div></div>'
        f'<div><div class="k">核对时间</div><div class="v">{ts}</div></div>'
        '</div>'
        '<div class="sec"><h2>一、报表数据列示（本期）</h2><div class="four">'
        '<div><h3>资产负债表 Balance Sheet</h3>'
        '<table><tr><th>项目</th><th style="text-align:right">金额</th><th>分区</th></tr>'
        f'{trows(td.get("bs", []))}</table></div>'
        '<div><h3>利润表 Income Statement</h3>'
        '<table><tr><th>项目</th><th style="text-align:right">金额</th><th>分区</th></tr>'
        f'{trows(td.get("inc", []))}</table>'
        '<h3 style="margin-top:14px">现金流量表 Cash Flow</h3>'
        '<table><tr><th>项目</th><th style="text-align:right">金额</th><th>分区</th></tr>'
        f'{trows(td.get("cf", []))}</table>'
        '<h3 style="margin-top:14px">未分配利润滚动表 Retained Earnings</h3>'
        '<table><tr><th>项目</th><th style="text-align:right">金额</th><th>分区</th></tr>'
        f'{trows(td.get("re", []))}</table>'
        '</div></div>'
        '<div class="note">注：括号内金额表示负数（现金流出/净流出）。各表「小计/合计」行以浅底加粗标示。</div>'
        '</div>'
        '<div class="sec ck">'
        f'<h2>二、勾稽关系逐项核对（共 {total} 项）</h2>'
        '<table><tr><th style="width:34px;text-align:center">序</th><th>勾稽关系</th><th>类型</th>'
        '<th style="text-align:right">左值</th><th style="text-align:right">右值</th>'
        '<th style="text-align:right">差异</th><th style="text-align:center;width:72px">结论</th></tr>'
        f'{crows(result["checks"])}</table>'
        '<div class="note">核对口径：表内小计 = 明细之和；会计恒等式 资产 = 负债 + 所有者权益；'
        f'三表勾稽 = 跨表同源数据一致。差异容差 ±{tol_txt} 元。</div>'
        '</div>'
        '<div class="sec"><h2>三、核对结论与处理意见</h2>'
        f'<div class="concl">{concl_big}{concl_body}</div>'
        '<div class="blind"><b>已知盲区声明（本核对未覆盖）：</b>①<b>口径与时点错误</b>——'
        '勾稽等式自洽但取数口径（时点/主体/准则）若错，等式仍可照样成立，须以跨期衔接（本期期初=上期期末）另行校验；'
        '②<b>三表同源同错</b>——若三表均由同一张错误试算表生成，跨表勾稽会全部通过但源头即错，'
        '须引入银行对账单/纳税申报/审计调整等外部独立源交叉验证。本底稿仅就报表<b>内部勾稽一致性</b>发表结论，不替代实质性程序。</div>'
        '</div>'
        '<div class="sign">'
        f'<div><div class="role">编制人 / 日期</div><div class="line"></div>'
        f'<div style="font-size:10px;color:#aab4c6;margin-top:3px">{m["method"]} · {today}</div></div>'
        '<div><div class="role">复核人 / 日期</div><div class="line"></div></div>'
        '<div><div class="role">财务负责人 / 日期</div><div class="line"></div></div>'
        f'<div><div class="role">归档索引</div><div style="font-family:Menlo,monospace;font-weight:700;margin-top:14px">{m["index"]}</div></div>'
        '</div>'
        '<div class="foot"><span class="tag">⚠️ 以下数据及企业均为构造，非真实数据</span><br>'
        f'报表勾稽核对底稿 · {m["entity"]} · 由 Amazon Quick 确定性脚本生成 · {ts}</div>'
        '</div></body></html>'
    )
    if out_path:
        import os, sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import output_guard as og
        path = og.resolve_output(out_path, out_dir, "tieout_worksheet.html", (".html", ".htm"))
        og.write_text(path, html)
        inputs = [og.file_fingerprint(p, r) for r, p in (input_paths or {}).items()]
        og.append_audit(os.path.dirname(path), __file__, {"tol": result.get("tol")}, inputs, [path],
                        {"all_tie": result.get("all_tie"), "data_issues": len(result.get("data_issues", []))},
                        og.sha256_json(result))
    return html
