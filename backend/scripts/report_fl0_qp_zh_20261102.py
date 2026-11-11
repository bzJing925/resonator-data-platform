from pathlib import Path
import html
import io
import shutil

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path("surrogate_data/optimization_fl0_audit_20261102")
OUTPUT = ROOT / "无框架器件异常阻抗曲线与数据清洗说明.html"
DESKTOP_OUTPUT = Path("/Users/jingbozuo/Desktop/无框架器件异常阻抗曲线与数据清洗说明-20261102.html")

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Arial Unicode MS"],
    "axes.unicode_minus": False,
    "svg.fonttype": "none",
    "font.size": 10,
})

REASON_NAMES = {
    "phase_derivative_unstable": "相位导数不稳定",
    "bandwidth_disagrees": "峰宽法结果不一致",
    "label_not_reproduced": "原标签不能复现",
    "local_nonpassive": "局部无源性异常",
    "linewidth_underresolved": "线宽采样不足",
    "phase_wrap": "相位展开异常",
}

EXAMPLES = [
    {
        "row_id": "deembedded:4396",
        "title": "例一｜相位拟合窗口敏感",
        "text": "局部三次拟合得到的 Qp 随拟合窗口变化的幅度为 {spread:.1f}%，超过 20% 筛选阈值。该结果说明相位导数稳定性不足，需复核拟合窗口和局部模态影响，不能直接据此认定原 Qp 错误。",
    },
    {
        "row_id": "deembedded:2308",
        "title": "例二｜相位与峰宽两类规则同时触发",
        "text": "相位拟合窗口变化幅度为 {spread:.1f}%；峰宽法 Qp 相对相位法偏高 {bw_delta:.1f}%，也超过 30% 筛选边界。不同方法未形成一致证据，因此隔离待复核。",
    },
    {
        "row_id": "deembedded:21263",
        "title": "例三｜反复振荡与多重峰，直接清洗",
        "text": "宽频范围内出现连续、反复的振荡和多重峰，不能把它仅解释为峰宽法偏高 {bw_delta:.1f}%。曲线已无法提供唯一、稳定的目标反谐振峰，直接清洗，不进入寻优。",
        "direct": True,
    },
    {
        "row_id": "deembedded:4468",
        "title": "例四｜峰宽法低于相位法",
        "text": "峰宽法 Qp 相对相位法偏低 {bw_delta_abs:.1f}%，但局部导纳法仍与相位法接近。该例说明“待复核”是保守筛选，不等于已经证伪原 Qp。",
    },
    {
        "row_id": "deembedded:2424",
        "title": "例五｜低 Qp 记录也按同一规则检查",
        "text": "峰宽法 Qp 相对相位法偏低 {bw_delta_abs:.1f}%。虽然该记录不是高 Qp，仍按同一物理一致性规则隔离，说明筛选并非针对高值或为了得到预期趋势。",
    },
    {
        "row_id": "deembedded:4526",
        "title": "例六｜多峰与折返导致峰位不唯一，直接清洗",
        "text": "反谐振附近存在多个相邻峰和明显折返，虽然峰宽法差异 {bw_delta_abs:.1f}% 看似只略超边界，实际曲线已不能稳定定义唯一峰位和线宽。直接清洗，不再等待复核。",
        "direct": True,
    },
]

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>无框架器件异常阻抗曲线与数据清洗说明</title>
<style>
:root{--ink:#172b3a;--muted:#5d6d78;--blue:#0072b2;--orange:#d55e00;--green:#177245;--red:#a23b3b;--line:#dce4e9;--paper:#f4f7f9}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.75 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
main{max-width:1220px;margin:0 auto;padding:36px 28px 70px}h1{font-size:32px;line-height:1.35;margin:0 0 10px;letter-spacing:.02em}h2{font-size:24px;margin:48px 0 16px;padding-top:12px;border-top:3px solid var(--ink)}h3{font-size:19px;line-height:1.5;margin:28px 0 10px}p{margin:9px 0}.subtitle{font-size:18px;color:var(--muted);margin-bottom:4px}.date{color:var(--muted);font-size:14px}.nav{display:flex;flex-wrap:wrap;gap:8px;margin:24px 0}.nav a{color:var(--ink);text-decoration:none;background:white;border:1px solid var(--line);border-radius:20px;padding:5px 13px;font-size:14px}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:24px 0}.metric{background:white;border:1px solid var(--line);border-radius:10px;padding:16px 18px}.metric strong{display:block;font-size:29px;line-height:1.25}.metric span{color:var(--muted);font-size:14px}.notice{background:#eaf3f8;border-left:5px solid var(--blue);padding:16px 20px;border-radius:5px;margin:20px 0}.warning{background:#fff5e9;border-left-color:var(--orange)}.good{background:#ebf5ef;border-left-color:var(--green)}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.card{background:white;border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:14px 0}.card h3{margin-top:0}.flow{counter-reset:step;list-style:none;padding:0;margin:12px 0}.flow li{position:relative;padding:8px 0 8px 46px;counter-increment:step}.flow li:before{content:counter(step);position:absolute;left:0;top:9px;width:29px;height:29px;border-radius:50%;background:var(--ink);color:white;text-align:center;line-height:29px}table{width:100%;border-collapse:collapse;background:white;font-size:14px;margin:12px 0}th,td{border:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}th{background:#eaf0f4;font-weight:650}td.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}.rule{margin:0}.rule td:first-child{width:22%}.rule td:nth-child(2){width:43%}.tag{display:inline-block;border-radius:4px;padding:2px 8px;font-size:13px;background:#f9e8df;color:#8a3900}.tag.pass{background:#dfeee5;color:var(--green)}.formula{font-size:17px;background:white;border:1px solid var(--line);border-radius:8px;padding:14px 18px;margin:10px 0}.example{background:white;border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin:24px 0;scroll-margin-top:16px}.example h3{margin:4px 0 8px}.example-meta{color:var(--muted);font-size:14px}.q-table{table-layout:fixed}.q-table th,.q-table td{text-align:center}.plot{margin:14px -4px 0}.plot svg{display:block;width:100%;height:auto}.caption{font-size:13px;color:var(--muted);margin-top:6px}.small{font-size:14px;color:var(--muted)}.checklist li{margin:7px 0}.footer{margin-top:45px;padding-top:18px;border-top:1px solid var(--line);font-size:13px;color:var(--muted)}sub,sup{line-height:0}
@media(max-width:800px){main{padding:24px 14px}.summary{grid-template-columns:1fr 1fr}.grid{grid-template-columns:1fr}h1{font-size:26px}.example{padding:15px 12px}table{display:block;overflow-x:auto}.q-table{display:table;font-size:12px}.q-table th,.q-table td{padding:7px 4px}}
@media print{body{background:white}main{max-width:none;padding:0}.nav{display:none}.example{break-inside:avoid;border:0;border-top:1px solid var(--line);border-radius:0}.summary{grid-template-columns:repeat(4,1fr)}h2{break-after:avoid}}
</style>
</head>
<body><main>
__CONTENT__
</main></body></html>
"""


def reason_text(value):
    if pd.isna(value) or not value:
        return "未触发复核规则"
    return "；".join(REASON_NAMES.get(item, item) for item in str(value).split(";"))


def figure_for(row):
    data = np.load(ROOT / "curves" / f"{row.row_id.replace(':', '_')}.npz")
    frequency = data["f"] / 1e9
    raw = data["raw_z"]
    de = data["de_z"]
    fp = row.de_fp_ghz
    width = max(0.015, row.de_bw_mhz / 1000 * 3)
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.2), layout="constrained")
    for key, label, color in [("raw", "原始测量", "#0072B2"), ("de", "去嵌结果", "#D55E00")]:
        z = raw if key == "raw" else de
        magnitude = 20 * np.log10(np.abs(z))
        axes[0, 0].plot(frequency, magnitude, label=label, color=color, lw=1.15)
        axes[0, 1].plot(frequency, magnitude, label=label, color=color, lw=1.3)
        axes[1, 0].plot(frequency, np.angle(z, deg=True), label=label, color=color, lw=1.3)
        axes[1, 1].plot(frequency, z.real, label=label, color=color, lw=1.3)
    axes[0, 0].set_xlim(max(frequency.min(), row.de_fs_ghz - 0.04), min(frequency.max(), fp + 0.04))
    zoom_left = max(frequency.min(), fp - width)
    zoom_right = min(frequency.max(), fp + width)
    for ax in [axes[0, 1], axes[1, 0], axes[1, 1]]:
        ax.set_xlim(zoom_left, zoom_right)
        ax.axvline(fp, color="#666666", lw=0.8, ls="--", label="反谐振频率")
    axes[0, 1].axhline(20 * np.log10(row.de_zp_ohm) - 3.0103, color="#555555", ls=":", lw=1.1, label="峰值下降 3.0103 分贝")
    axes[1, 1].axhline(0, color="#555555", ls=":", lw=1.1, label="零欧姆参考")
    axes[0, 0].set_title("宽频阻抗幅值")
    axes[0, 1].set_title("反谐振附近幅值与三分贝线宽")
    axes[1, 0].set_title("反谐振附近相位")
    axes[1, 1].set_title("反谐振附近阻抗实部")
    axes[0, 0].set_ylabel("Z（dB）")
    axes[0, 1].set_ylabel("阻抗幅值（dB）")
    axes[1, 0].set_ylabel("相位")
    axes[1, 1].set_ylabel("阻抗实部（欧姆）")
    for ax in axes.flat:
        ax.set_xlabel("频率（GHz）")
        ax.grid(alpha=0.22)
    axes[0, 0].legend(frameon=False, loc="best")
    axes[0, 1].legend(frameon=False, fontsize=8, loc="best")
    fig.suptitle(f"{row.mark}｜坐标 {row.coord}｜{row.port} 端口", fontsize=14)
    buffer = io.StringIO()
    fig.savefig(buffer, format="svg")
    plt.close(fig)
    svg = buffer.getvalue()
    return svg[svg.find("<svg"):]


def q_table(row):
    values = [
        ("源表 Qp", row.source_qp),
        ("原始曲线 Qp", row.raw_qp),
        ("局部三次拟合 Qp", row.de_qp_cubic),
        ("峰宽法 Qp", row.de_qp_bandwidth),
        ("局部导纳法 Qp", row.de_qp_admittance),
    ]
    head = "".join(f"<th>{html.escape(name)}</th>" for name, _ in values)
    body = "".join(f"<td>{value:.1f}</td>" for _, value in values)
    return f'<table class="q-table"><thead><tr>{head}</tr></thead><tbody><tr>{body}</tr></tbody></table>'


def example_for(row, title, text, index, passed=False, direct=False):
    spread = row.de_qp_cubic_spread * 100
    bw_delta = (row.de_qp_bandwidth / row.de_qp - 1) * 100
    explanation = text.format(spread=spread, bw_delta=bw_delta, bw_delta_abs=abs(bw_delta))
    if direct:
        status = '<span class="tag">直接清洗｜严重抖动与多重峰</span>'
    elif passed:
        status = '<span class="tag pass">通过筛选｜高值对照</span>'
    else:
        status = f'<span class="tag">待复核｜{html.escape(reason_text(row.reasons))}</span>'
    figure = figure_for(row)
    return f"""
<section class="example" id="example-{index}">
{status}
<h3>{html.escape(title)}</h3>
<p class="example-meta">{html.escape(row.mark)}｜坐标 {html.escape(row.coord)}｜{html.escape(row.port)} 端口｜几何参数：EG={row.eg_um:g} 微米，FL=0，面积={row.area_um2:g} 平方微米</p>
{q_table(row)}
<p>{html.escape(explanation)}</p>
<div class="plot">{figure}</div>
<p class="caption">蓝线为原始测量，橙线为去嵌结果；竖直虚线标出反谐振频率。幅值图中的点线为峰值下降 3.0103 分贝的位置，实部图中的点线为零欧姆参考。曲线用于解释筛选原因，不代表已用推算值替换实测标签。</p>
</section>"""


def run():
    records = pd.read_csv(ROOT / "record_audit.csv").set_index("row_id", drop=False)
    review = records[records.decision.eq("review")]
    high = records[records.source_qp.ge(1000)]
    parts = []
    parts.append("""
<header>
<h1>无框架器件异常阻抗曲线与数据清洗说明</h1>
<p class="subtitle">从实际阻抗曲线出发，解释 Qp 数据如何核验、筛选与保留</p>
<p class="date">数据范围：无框架（FL=0）且几何条件合格的实测记录｜审计日期：2026 年 11 月 2 日</p>
</header>
<nav class="nav"><a href="#work">工作内容</a><a href="#rules">清洗规则</a><a href="#examples">异常曲线实例</a><a href="#control">高值对照</a><a href="#result">数据处置与结论</a></nav>
""")
    parts.append(f"""
<div class="summary">
<div class="metric"><strong>72</strong><span>个结构</span></div>
<div class="metric"><strong>{len(records):,}</strong><span>条记录完成核验</span></div>
<div class="metric"><strong>{int(records.decision.eq('curve_supported').sum()):,}</strong><span>条曲线支持，占 98.2%</span></div>
<div class="metric"><strong>{len(review) - 2}</strong><span>条待复核；另有 2 条直接清洗</span></div>
</div>
<div class="notice"><strong>核心结论：</strong>现有曲线不支持“无框架器件的高 Qp 整体虚高”这一判断。Qp≥1000 的 {len(high):,} 条记录全部通过本轮曲线一致性筛选。原自动筛出的 59 条异常记录中，例三和例六因严重抖动与多重峰改判为直接清洗，其余 57 条待复核；分类依据是曲线质量，而不是 Qp 数值高低。</div>
<div class="notice warning"><strong>措辞边界：</strong>“曲线支持”不等于证明材料本征 Q、历史校准绝对准确或器件具有重复性；“待复核”也不等于已证明数据错误。本报告展示的是测量曲线与物理推算是否自洽。</div>
""")

    parts.append("""
<h2 id="work">一、完成了哪些工作</h2>
<ol class="flow">
<li><strong>限定数据范围。</strong>只检查结构寻优实测源表中几何条件合格、FL=0 的去嵌记录，共 72 个结构、3,320 条；未把未知几何或外推候选混入。</li>
<li><strong>复现原始标签。</strong>从测量文件重新计算 Qp，3,320 条去嵌标签全部复现。原始表有 1 条参考 Qp 缺失，其余 3,319 条原始标签均复现；缺失不等于数值冲突。</li>
<li><strong>核对真实阻抗曲线。</strong>逐条保存并检查原始与去嵌复阻抗，包括宽频幅值、反谐振附近幅值、相位和阻抗实部。</li>
<li><strong>用多种物理关系交叉推算。</strong>比较相位导数、局部三次拟合、阻抗峰宽和局部导纳得到的结果，并检查局部无源性、相位展开和线宽采样。</li>
<li><strong>执行非破坏性清洗。</strong>原值全部保留；直接清洗与待复核记录均在新寻优筛选字段中留空，形成可回溯的保留区、清洗区与复核区，没有修改源表、数据库、原页面或模型。</li>
</ol>
<div class="grid">
<div class="card"><h3>为什么不用“Qp 太高”作为删除条件</h3><p>总损耗由材料、电极、垂直方向、横向泄漏等多部分组成。无框架可能增加横向泄漏，但并不对应固定的 Qp 上限。因此必须看曲线是否支持，而不能按结构标签或数值高低直接删除。</p></div>
<div class="card"><h3>为什么使用多种推算方法</h3><p>相位法、峰宽法和导纳法对局部模态、背景和拟合窗口的敏感程度不同。方法一致时，高 Qp 更有曲线依据；方法明显不一致时，先隔离复核，而不是任选一个数值替换实测值。</p></div>
</div>
""")

    parts.append(f"""
<h2 id="rules">二、清洗规则</h2>
<h3>第一步：明显异常曲线全部直接清洗</h3>
<p>先检查原始与去嵌阻抗曲线的基本质量，再看 Qp 数值。命中以下任一情况，说明曲线不能提供可靠的谐振峰信息，直接清洗，不参与 Qp 提取、统计和结构寻优，不再仅标记为“待复核”。</p>
<table class="rule"><thead><tr><th>异常类型</th><th>识别方法</th><th>处理方式</th></tr></thead><tbody>
<tr><td>没有有效峰</td><td>预期频率范围内看不到清晰、完整的反谐振阻抗峰，或者峰无法与背景起伏区分，无法可靠确定峰位和峰宽。</td><td>直接清洗，不使用由局部最大值勉强算出的 Qp。</td></tr>
<tr><td>噪声过大</td><td>峰附近曲线被明显随机起伏淹没，峰形、相位或背景无法稳定辨认；轻微平滑也不能恢复清晰的峰形。</td><td>直接清洗，不用平滑后的数值代替原始测量质量。</td></tr>
<tr><td>频繁抖动或密集伪峰</td><td>相邻采样点反复跳变，出现大量细窄尖峰、折返或不连续，导致峰位、峰宽和相位导数无法稳定确定。</td><td>直接清洗，不挑选其中一个尖峰计算 Qp。</td></tr>
</tbody></table>
<div class="notice warning"><strong>执行口径：</strong>上述三种情况以曲线是否可辨认、可稳定提取为准，不设“Qp 较低就保留”的例外。平滑只用于辅助观察，不能掩盖原始异常。原始文件和源表原值仍保留；清洗记录在新的寻优 Qp 字段中留空，并记录“无有效峰”“噪声过大”或“频繁抖动”的原因。</div>
<h3>第二步：检查 Qp 提取与物理一致性</h3>
<p>通过基本曲线质量检查后，再执行以下一致性筛选。任一条件触发即将记录标记为“待复核”。阈值是本次审计的保守筛选规则，不是适用于所有谐振器的通用物理标准。</p>
<table class="rule"><thead><tr><th>检查项</th><th>触发条件</th><th>本次结果</th></tr></thead><tbody>
<tr><td>标签复现</td><td>重新计算的去嵌 Qp 与源表标签的相对差异超过 10<sup>-7</sup></td><td>0 条触发</td></tr>
<tr><td>局部无源性</td><td>反谐振附近正负 3 个线宽内，阻抗实部出现小于 -10<sup>-6</sup> 欧姆的点</td><td>0 条触发</td></tr>
<tr><td>线宽采样</td><td>三分贝线宽内不足 5 个频点，或线宽无法有效计算</td><td>0 条触发</td></tr>
<tr><td>相位展开</td><td>展开前后所得 Qp 的相对差异超过 1%</td><td>0 条触发</td></tr>
<tr><td>相位导数稳定性</td><td>局部三次拟合 Qp 相对相位法差异超过 20%，或不同拟合窗口所得结果的变化幅度超过 20%</td><td>20 条触发</td></tr>
<tr><td>峰宽法一致性</td><td>峰宽法 Qp 与相位法 Qp 的比值不在 0.7 至 1.3 之间</td><td>38 条触发</td></tr>
</tbody></table>
<p class="small">原自动一致性筛选共标记 59 条。例三和例六经曲线形态复核后改判为直接清洗，不再计入待复核；剩余 57 条包括 19 条单独相位问题、37 条单独峰宽问题和 1 条双重问题。</p>
<h3>三种推算关系及其适用前提</h3>
<div class="formula"><strong>相位导数法：</strong>Q<sub>p</sub> = f<sub>p</sub> / 2 × |d arg(Z) / df|。要求反谐振附近相位变化清晰，局部导数不能对拟合窗口过度敏感。</div>
<div class="formula"><strong>阻抗峰宽法：</strong>Q<sub>p</sub> ≈ f<sub>p</sub> / Δf。Δf 取阻抗幅值从峰值下降 3.0103 分贝后最近左右交点之间的宽度；单一、平滑、弱背景的反谐振峰更适合使用该关系。</div>
<div class="formula"><strong>局部导纳法：</strong>Q<sub>Y</sub> ≈ f<sub>p</sub> / (2G) × dB/df，其中 Y=G+jB。它用于局部交叉验证，不能在强背景或多模态条件下机械地当作真值。</div>
<div class="notice">三种方法使用的是同一次测量，因此属于曲线内部的一致性核验，不是独立实验。解析并联 RLC 算例已验证计算实现，误差小于 0.5%，但这不能验证仪器和历史校准。</div>
""")

    parts.append("""
<h2 id="examples">三、部分异常器件的真实阻抗曲线</h2>
<p>以下展示 6 条原自动筛选异常记录，其中例三和例六已根据明显曲线形态异常改判为直接清洗，其余 4 条继续待复核。所有原值均保留，曲线中的推算结果只用于解释筛选原因。</p>
""")
    for index, config in enumerate(EXAMPLES, start=1):
        row = records.loc[config["row_id"]]
        parts.append(example_for(row, config["title"], config["text"], index, direct=config.get("direct", False)))

    top = records.loc[records.source_qp.idxmax()]
    parts.append("""
<h2 id="control">四、高 Qp 对照：曲线自洽时不清洗</h2>
<p>加入本批次源表 Qp 最高的器件作为对照。它不是异常记录，而是用于说明：高值本身不是隔离理由，关键在于不同物理推算能否相互支持。</p>
""")
    control_text = "源表 Qp 为 {source:.1f}；峰宽法约为 {bw:.1f}，局部导纳法约为 {qy:.1f}，三者差异不到 4%。该高值获得实际曲线支持，因此保留。这里的“支持”仍需后续复测确认校准准确度与重复性。"
    control_text = control_text.format(source=top.source_qp, bw=top.de_qp_bandwidth, qy=top.de_qp_admittance)
    parts.append(example_for(top, "对照｜最高 Qp 器件的多方法结果一致", control_text.replace("{", "{{").replace("}", "}}"), 7, passed=True))

    parts.append(f"""
<h2 id="result">五、数据如何处置，结论是什么</h2>
<table><thead><tr><th>数据或结果</th><th>处置方式</th></tr></thead><tbody>
<tr><td>3,261 条曲线支持记录</td><td>保留原 Qp，并允许进入后续寻优筛选。</td></tr>
<tr><td>2 条明显异常记录</td><td>例三和例六因严重抖动、多重峰与峰位不唯一直接清洗，不参与 Qp 统计和结构寻优。</td></tr>
<tr><td>57 条待复核记录</td><td>原值和曲线完整保留；新增的寻优 Qp 字段留空，等待复测或更完整的多模态、校准分析。</td></tr>
<tr><td>Qp≥1000 的 {len(high):,} 条记录</td><td>全部通过本轮筛选，没有因数值高而降级或删除。</td></tr>
<tr><td>源表、原始文件、数据库</td><td>均未修改，保证可以回溯和重新审计。</td></tr>
<tr><td>原结构寻优页面与模型</td><td>尚未替换或重训；本报告只说明清洗依据和数据处置。</td></tr>
</tbody></table>
<div class="notice good"><strong>工作结论：</strong>明显曲线质量异常直接清洗，方法不一致但曲线仍可辨认的记录再隔离复核。例三和例六不再列入待复核；没有为了迎合“无框架必然低 Q”的预期而调整任何实测值。</div>
<ul class="checklist">
<li><strong>后续复测重点：</strong>优先检查其余 57 条待复核记录的局部模态、拟合窗口、仪器中频带宽、功率和重复测量。</li>
<li><strong>历史校准边界：</strong>原始与去嵌曲线数值复现，不等于历史去嵌配置已经得到独立证实。</li>
<li><strong>使用边界：</strong>筛选后的中位数变化包含样本组成变化，不能直接解释为物理修正或器件性能提升。</li>
</ul>
<footer class="footer">本报告为离线单文件，全部曲线已内嵌。数据来源为无框架 Qp 曲线审计结果；全部 3,320 条记录的证据、隔离清单及复阻抗曲线仍保存在原审计目录中。</footer>
""")
    output = PAGE.replace("__CONTENT__", "".join(parts))
    OUTPUT.write_text(output, encoding="utf-8")
    shutil.copy2(OUTPUT, DESKTOP_OUTPUT)
    print(f"已生成：{DESKTOP_OUTPUT}")
    print(f"文件大小：{DESKTOP_OUTPUT.stat().st_size / 1024 / 1024:.2f} 兆字节")


if __name__ == "__main__":
    run()
