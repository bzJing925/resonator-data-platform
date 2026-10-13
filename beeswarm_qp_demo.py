# -*- coding: utf-8 -*-
"""Beeswarm demo: Qp vs Area/FL, style extracted from reference chart."""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_excel('#16_results_de.xlsx')
df = df[['Area', 'FL', 'Qp']].dropna()
df['Qp'] = df['Qp'].clip(upper=df['Qp'].quantile(0.999))  # tame extreme tail

areas = sorted(df['Area'].unique())
fls = sorted(df['FL'].unique())

# ---- build category positions: group = Area, sub-position = FL ----
cats = []          # (x_center, area, fl)
x = 0.0
for i, a in enumerate(areas):
    for f in fls:
        if len(df[(df.Area == a) & (df.FL == f)]):
            cats.append((x, a, f))
            x += 1.0
    x += 0.8       # gap between Area groups

MAIN = '#2a9d8f'   # swarm color
LINE = '#1b6b60'   # median line color


def beeswarm_offsets(ys, bin_width, max_width=0.42):
    """Horizontal offsets: points at the same y-level stack sideways,
    so denser y-levels look wider."""
    ys = np.asarray(ys)
    order = np.argsort(ys)
    ys_sorted = ys[order]
    offs = np.zeros(len(ys))
    bins = np.floor(ys_sorted / bin_width)
    for b in np.unique(bins):
        idx = np.where(bins == b)[0]
        n = len(idx)
        seq = np.arange(n)
        offsets = np.where(seq % 2 == 0, seq // 2, -(seq // 2 + 1)).astype(float)
        step = 2 * max_width / max(n, 12)
        offs[order[idx]] = offsets * step
    return offs


fig, ax = plt.subplots(figsize=(26, 7), dpi=110)
yrange = df['Qp'].max() - df['Qp'].min()
bin_w = yrange / 40

medians, xs_med = [], []
for xc, a, f in cats:
    ys = df.loc[(df.Area == a) & (df.FL == f), 'Qp'].values
    off = beeswarm_offsets(ys, bin_w)
    ax.scatter(xc + off, ys, s=16, color=MAIN, alpha=0.7, linewidths=0)
    m = np.median(ys)
    medians.append(m)
    xs_med.append(xc)

# median trend line
ax.plot(xs_med, medians, color=LINE, lw=1.6, zorder=5)

# median value annotations at panel top
ytop = df['Qp'].max() + yrange * 0.02
for xc, m in zip(xs_med, medians):
    ax.text(xc, ytop, f'{m:.0f}', rotation=90, fontsize=4.5,
            ha='center', va='bottom', color=LINE)

# ---- axes cosmetics ----
ax.set_ylim(df['Qp'].min() - yrange * 0.03, ytop + yrange * 0.14)
ax.set_ylabel('Qp', fontsize=13)
ax.grid(True, color='#e5e5e5', lw=0.6)
ax.set_axisbelow(True)
ax.set_facecolor('white')
for s in ('top', 'right'):
    ax.spines[s].set_visible(False)

# two-row x labels: FL per category, Area per group
ax.set_xticks([c[0] for c in cats])
ax.set_xticklabels([f'{c[2]:g}' for c in cats], fontsize=6)
# group separators + Area labels
edges = []
for i in range(len(cats) - 1):
    if cats[i + 1][1] != cats[i][1]:
        edges.append((cats[i][0] + cats[i + 1][0]) / 2)
for e in edges:
    ax.axvline(e, color='#bbbbbb', ls='--', lw=0.8)
ymin, ymax = ax.get_ylim()
y_area = ymin - (ymax - ymin) * 0.14
groups = {}
for xc, a, f in cats:
    groups.setdefault(a, []).append(xc)
for a, xs_ in groups.items():
    ax.text(np.mean(xs_), y_area, f'Area {a:g}', ha='center', va='top',
            fontsize=9, fontweight='bold')
ax.text(np.mean([c[0] for c in cats]), y_area - (ymax - ymin) * 0.12,
        'Area / FL', ha='center', va='top', fontsize=12)

ax.set_xlim(min(c[0] for c in cats) - 0.7, max(c[0] for c in cats) + 0.7)
plt.tight_layout()
plt.savefig('beeswarm_qp_demo.png', bbox_inches='tight', facecolor='white')
print('saved beeswarm_qp_demo.png')
