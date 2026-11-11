// Offline projection of frozen P4 catalogs. No browser model or inference endpoint.
(() => {
  const targets = ['k2eff_pct', 'qs', 'qp', 'bodeq_fitted'] as const;
  type Target = typeof targets[number];
  type Mode = 'measured' | 'exploratory';
  type Scalar = string | number | boolean | null;
  interface Row { [key: string]: Scalar; structure_id: string; eligible: boolean }
  interface Dataset {
    columns: string[]; rows: Scalar[][]; source_sha256: string;
    summary: { mode: Mode; config: Record<string, unknown>; n_candidates: number };
  }
  interface BrowserPackage {
    schema: string; version: string; model_version: string; source: string;
    new_blind_test: boolean; datasets: Record<Mode, Dataset>;
    joint_coverage: { scope: string; all_four_group_coverage: number }[];
    d2_reference?: D2Reference;
    p11_candidates?: P11Block;
    p12_candidates?: P12Block;
  }
  interface P11Entry {
    candidate_id: string; group: string; origin?: string; eg_um: number; fl_um: number; area_um2: number;
    has_pf: number; branch_id: string;
    qp: { prediction: number; lower: number; upper: number };
    k2eff_pct: { prediction: number; lower: number; upper: number };
    p6c_qp: number; p6c_k2eff_pct: number;
    p3_support: string; extrapolation_axis: string; extrapolation_distance: number;
    qp_delta_vs_qp_baseline: number; k2_delta_vs_k2_baseline: number;
    risk_flags: string; recommendation: string;
  }
  interface P11Block {
    schema: string; experimental: boolean; frozen_config: boolean; new_blind_test: boolean;
    retrospective_evidence: boolean; trusted_for_unmeasured_optimization: boolean;
    targets: string[]; baselines: Record<string, { marks: string; qp: number; k2eff_pct: number }>;
    evidence_doc: string; entries: P11Entry[];
  }
  interface P12Block extends Omit<P11Block, 'entries'> { n_entries: number; entries: P11Entry[] }
  interface D2Entry {
    structure_id: string; fl_um: number; qp_actual: number;
    c0_scaled: [number, number]; d2_scaled: [number, number];
    c0_global: [number, number]; d2_global: [number, number];
  }
  interface D2Reference {
    schema: string; experimental: boolean; frozen_config: boolean; new_blind_test: boolean;
    retrospective_evidence: boolean; trusted_for_unmeasured_optimization: boolean;
    target: string; subset_rule: string; selected_arm_p10: string;
    inner_subset_qp_coverage: Record<string, number>;
    outer_subset_qp_coverage: Record<string, string>;
    evidence_doc: string; entries: D2Entry[];
  }
  interface Filters { minimums: Partial<Record<Target, number>>; bounds: Record<string, [number | null, number | null]>; pf: number | null }
  const labels: Record<Target, string> = { k2eff_pct: 'k² (%)', qs: 'Qs', qp: 'Qp', bodeq_fitted: 'Bode Q' };
  const shortLabels: Record<Target, string> = { k2eff_pct: 'k²', qs: 'Qs', qp: 'Qp', bodeq_fitted: 'Bode Q' };
  const rangeLabels: Record<string, string> = { eg_um: 'EG · μm', fl_um: 'FL · μm', area_um2: '面积 · μm²', fs_ghz: 'fs', fp_ghz: 'fp' };
  const geometry = ['eg_um', 'fl_um', 'area_um2'];
  const frequencies = ['fs_ghz', 'fp_ghz'];
  const allowedLocal = new Set(['exact_fitted_structure', 'complete_local_cell']);
  const supportLabels: Record<string, string> = { exact_fitted_structure: '精确拟合结构', complete_local_cell: '相邻单元完整', missing_local_corners: '局部角点缺失', outside_training_bounds: '训练范围外', unknown_branch: '未知分支' };
  const get = <T extends HTMLElement = HTMLElement>(id: string): T => {
    const node = document.getElementById(id);
    if (!node) throw new Error(`Missing element: ${id}`);
    return node as T;
  };
  const value = (id: string) => get<HTMLInputElement | HTMLSelectElement>(id).value;
  const esc = (v: unknown) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
  const numeric = (row: Row, key: string): number | null => typeof row[key] === 'number' && Number.isFinite(row[key]) ? row[key] as number : null;
  const fmt = (v: unknown, digits = 2) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : '—';
  const geom = (row: Row) => `EG ${row.eg_um} · FL ${row.fl_um} · ${row.area_um2} μm² · PF ${row.has_pf}`;
  const name = (row: Row) => row.marks ? String(row.marks) : row.structure_id;
  function assert(condition: unknown, message: string): asserts condition {
    if (!condition) throw new Error(message);
  }
  function validate(input: unknown): { pack: BrowserPackage; rows: Record<Mode, Row[]> } {
    assert(input && typeof input === 'object', '数据包必须是 JSON 对象');
    const pack = input as BrowserPackage;
    assert(pack.schema === 'aln_optimization_browser_v1', '仅接受构建器生成的 browser_data.json 浏览数据包');
    assert(pack.new_blind_test === false, '不能将本结果标记为新盲测');
    for (const key of ['version', 'model_version', 'source'] as const) assert(typeof pack[key] === 'string', `缺少 ${key}`);
    assert(pack.model_version === 'optimization_scalar_p3_v1', '此页面只支持冻结 P3 标量模型版本');
    assert(pack.datasets && typeof pack.datasets === 'object', '缺少结果目录');
    const result = {} as Record<Mode, Row[]>;
    for (const mode of ['measured', 'exploratory'] as const) {
      const ds = pack.datasets[mode];
      assert(ds && Array.isArray(ds.columns) && ds.columns.every(c => typeof c === 'string'), '无效列定义');
      assert(new Set(ds.columns).size === ds.columns.length, '列名重复');
      assert(ds.columns.length <= 150 && Array.isArray(ds.rows) && ds.rows.length <= 10000, '目录超过浏览预算');
      assert(ds.summary?.mode === mode && ds.summary.n_candidates === ds.rows.length, '模式或候选数量不匹配');
      assert(typeof ds.source_sha256 === 'string' && /^[a-f0-9]{64}$/.test(ds.source_sha256), '缺少来源哈希');
      for (const c of ['structure_id', 'eligible', 'cohort', 'has_pf', 'trusted_for_unmeasured_optimization', 'value_source', ...targets, ...geometry]) assert(ds.columns.includes(c), `缺少列 ${c}`);
      const ids = new Set<string>();
      result[mode] = ds.rows.map(values => {
        assert(Array.isArray(values) && values.length === ds.columns.length, '行列长度不符');
        assert(values.every(v => v === null || ['string', 'boolean'].includes(typeof v) || (typeof v === 'number' && Number.isFinite(v))), '存在非有限值或对象单元格');
        const row = Object.fromEntries(ds.columns.map((c, i) => [c, values[i]])) as Row;
        assert(typeof row.structure_id === 'string' && row.structure_id.length > 0 && !ids.has(row.structure_id), '结构 ID 缺失或重复');
        ids.add(row.structure_id);
        assert(row.cohort === 'standard_eg_fl_unrecorded_ag', '不支持该结构群体');
        assert(row.trusted_for_unmeasured_optimization === false, '未测结构可信标志必须为 false');
        assert(typeof row.eligible === 'boolean', 'eligible 必须为布尔值');
        assert(row.has_pf === 0 || row.has_pf === 1, 'PF 必须为 0 或 1');
        for (const c of geometry) assert(numeric(row, c) !== null && Number(row[c]) >= 0, '无效结构尺寸');
        assert(Number(row.area_um2) > 0 && (row.ag_um == null), '面积或 AG 不受支持');
        assert(row.value_source === (mode === 'measured' ? 'observed_deembedded_structure_typical' : 'frozen_p3_surrogate_exploratory'), '不能混用实测/预测或 raw/de');
        if (row.eligible) for (const t of targets) {
          assert(numeric(row, t) !== null && Number(row[t]) > 0, '可行候选缺少有效目标');
          if (mode === 'measured') assert(numeric(row, `${t}_n_coords`) !== null && Number(row[`${t}_n_coords`]) >= 10, '有效坐标不足');
          else assert(allowedLocal.has(String(row[`${t}_local_support`])), '不可绕过局部支持规则');
        }
        return row;
      });
    }
    if (pack.d2_reference !== undefined) validateD2(pack.d2_reference, result.measured);
    const allIds = new Set([...result.measured, ...result.exploratory].map(r => r.structure_id));
    if (pack.p11_candidates !== undefined) {
      validateCandidateBlock(pack.p11_candidates, allIds, 'aln_optimization_p11_candidates_v1', 10);
    }
    if (pack.p12_candidates !== undefined) {
      const block = pack.p12_candidates;
      assert(Number.isInteger(block.n_entries) && block.n_entries > 0 && block.n_entries <= 100, 'P12 候选块缺少有效 n_entries');
      validateCandidateBlock(block, allIds, 'aln_optimization_p12_candidates_v1', block.n_entries);
    }
    return { pack, rows: result };
  }
  function validateCandidateBlock(ref: unknown, catalogIds: Set<string>, schema: string, nEntries: number): void {
    assert(ref && typeof ref === 'object', '外推候选块必须是对象');
    const block = ref as P11Block;
    assert(block.schema === schema, '未知外推候选格式');
    assert(block.experimental === true && block.retrospective_evidence === true, '外推候选必须是实验性回顾证据');
    assert(block.frozen_config === false && block.new_blind_test === false, '外推候选不得自称冻结配置或新盲测');
    assert(block.trusted_for_unmeasured_optimization === false, '外推候选可信标志必须为 false');
    assert(Array.isArray(block.targets) && block.targets.join(',') === 'qp,k2eff_pct', '外推候选仅支持 Qp 与 k²');
    assert(typeof block.evidence_doc === 'string', '外推候选缺少证据文档');
    assert(Array.isArray(block.entries) && block.entries.length === nEntries, `外推候选必须为 ${nEntries} 个条目`);
    for (const entry of block.entries) assert(entry.group === 'A' || entry.group === 'B' || entry.group === 'P11' || entry.group === 'P12', '外推候选组标识非法');
    const ids = new Set<string>();
    for (const entry of block.entries) {
      assert(typeof entry.candidate_id === 'string' && !ids.has(entry.candidate_id), '外推候选 ID 缺失或重复');
      ids.add(entry.candidate_id);
      assert(!catalogIds.has(entry.candidate_id), '外推候选不得与实测/探索目录撞 ID');
      for (const v of [entry.eg_um, entry.fl_um, entry.area_um2]) assert(typeof v === 'number' && Number.isFinite(v) && v >= 0, '外推候选结构尺寸无效');
      for (const t of ['qp', 'k2eff_pct'] as const) {
        const m = entry[t];
        assert(Number.isFinite(m.prediction) && Number.isFinite(m.lower) && Number.isFinite(m.upper), '外推候选预测值非有限');
        assert(m.lower > 0 && m.lower < m.upper, '外推候选区间上下界无效');
        assert(Number.isFinite(entry[t === 'qp' ? 'p6c_qp' : 'p6c_k2eff_pct']), '外推候选缺少敏感性对照值');
      }
      assert(typeof entry.p3_support === 'string' && typeof entry.risk_flags === 'string', '外推候选缺少支持标签或风险标注');
    }
  }
  function validateD2(ref: unknown, measured: Row[]): void {
    assert(ref && typeof ref === 'object', 'D2 参考层必须是对象');
    const d2 = ref as D2Reference;
    assert(d2.schema === 'aln_optimization_d2_reference_v1', '未知 D2 参考层格式');
    assert(d2.experimental === true && d2.retrospective_evidence === true, 'D2 参考层必须是实验性回顾证据');
    assert(d2.frozen_config === false && d2.new_blind_test === false, 'D2 参考层不得自称冻结配置或新盲测');
    assert(d2.trusted_for_unmeasured_optimization === false, 'D2 可信标志必须为 false');
    assert(d2.target === 'qp', 'D2 参考层仅支持 Qp');
    assert(typeof d2.subset_rule === 'string' && typeof d2.evidence_doc === 'string', 'D2 缺少适用范围或证据文档');
    assert(Array.isArray(d2.entries) && d2.entries.length === 8, 'D2 参考层必须恰好覆盖 8 个子集结构');
    const ids = new Set<string>();
    const measuredById = new Map(measured.map(r => [r.structure_id, r]));
    for (const entry of d2.entries) {
      assert(typeof entry.structure_id === 'string' && !ids.has(entry.structure_id), 'D2 结构 ID 缺失或重复');
      ids.add(entry.structure_id);
      const row = measuredById.get(entry.structure_id);
      assert(row !== undefined, 'D2 结构不在实测目录');
      assert(Number(row.has_pf) === 1 && Number(row.eg_um) === 0 && Number(row.area_um2) === 700, 'D2 结构不满足子集几何规则');
      assert(Number.isFinite(entry.qp_actual) && Number.isFinite(entry.fl_um), 'D2 缺少数值');
      for (const key of ['c0_scaled', 'd2_scaled', 'c0_global', 'd2_global'] as const) {
        const pair = entry[key];
        assert(Array.isArray(pair) && pair.length === 2 && pair.every(v => typeof v === 'number' && Number.isFinite(v)), 'D2 区间必须是两个有限数');
        assert(pair[0] > 0 && pair[0] < pair[1], 'D2 区间上下界无效');
      }
    }
  }

  let { pack, rows: catalogs } = validate(JSON.parse(get('data').textContent || '{}'));
  const d2For = (id: string) => pack.d2_reference?.entries.find(e => e.structure_id === id);
  let showD2 = false;
  let showCand = true;
  let selectedCand = '';
  let mode: Mode = 'measured';
  let imported = false;
  let filters: Filters = { minimums: {}, bounds: {}, pf: null };
  let feasible: Row[] = [];
  let pareto = new Set<string>();
  let ordered: Row[] = [];
  let selected = '';
  let compared: string[] = [];
  const options = targets.map(t => `<option value="${t}">${labels[t]}</option>`).join('');
  for (const id of ['target', 'x-axis', 'y-axis']) get(id).innerHTML = options;
  get<HTMLSelectElement>('x-axis').value = 'k2eff_pct';
  get<HTMLSelectElement>('y-axis').value = 'qp';
  get('metric-filters').innerHTML = targets.map(t => `<label>${labels[t]} ≥<input id="min-${t}" type="number" step="any" aria-label="${labels[t]}下限" placeholder="不限"></label>`).join('');
  function rangeInput(key: string): string {
    return `<div class="range"><div class="range-name">${rangeLabels[key]}</div><div class="range-pair"><label>最小<input id="lo-${key}" type="number" step="any" aria-label="${rangeLabels[key]}最小值" placeholder="不限"></label><label>最大<input id="hi-${key}" type="number" step="any" aria-label="${rangeLabels[key]}最大值" placeholder="不限"></label></div></div>`;
  }
  get('geometry-filters').innerHTML = geometry.map(rangeInput).join('');
  get('frequency-ranges').innerHTML = frequencies.map(rangeInput).join('');
  function notify(message = ''): void {
    get('message').hidden = !message;
    get('message').textContent = message;
  }
  function readNumber(id: string): number | null {
    const input = get<HTMLInputElement>(id);
    if (input.validity.badInput) throw new Error('请输入有效数字。');
    if (!input.value.trim()) return null;
    const n = Number(input.value);
    assert(Number.isFinite(n), '请输入有限数字。');
    return n;
  }
  function readFilters(): Filters {
    const next: Filters = { minimums: {}, bounds: {}, pf: value('pf') === '' ? null : Number(value('pf')) };
    for (const t of targets) { const n = readNumber(`min-${t}`); if (n !== null) next.minimums[t] = n; }
    for (const key of [...geometry, ...(mode === 'measured' ? frequencies : [])]) {
      const lo = readNumber(`lo-${key}`), hi = readNumber(`hi-${key}`);
      assert(lo === null || hi === null || lo <= hi, `${rangeLabels[key]}：最小值不能大于最大值。当前结果未改变。`);
      if (lo !== null || hi !== null) next.bounds[key] = [lo, hi];
    }
    return next;
  }
  function recompute(): void {
    feasible = catalogs[mode].filter(row => row.eligible &&
      (filters.pf === null || row.has_pf === filters.pf) &&
      targets.every(t => filters.minimums[t] === undefined || Number(row[t]) >= filters.minimums[t]!) &&
      Object.entries(filters.bounds).every(([key, [lo, hi]]) => {
        const n = numeric(row, key);
        return n !== null && (lo === null || n >= lo) && (hi === null || n <= hi);
      }));
    pareto = new Set(feasible.filter(a => !feasible.some(b =>
      targets.every(t => Number(b[t]) >= Number(a[t])) && targets.some(t => Number(b[t]) > Number(a[t]))
    )).map(r => r.structure_id));
    if (!feasible.some(r => r.structure_id === selected)) selected = '';
    compared = compared.filter(id => feasible.some(r => r.structure_id === id));
    render();
  }
  function render(): void {
    const target = value('target') as Target;
    const fullOrder = [...feasible].sort((a, b) => Number(b[target]) - Number(a[target]) || (a.structure_id < b.structure_id ? -1 : a.structure_id > b.structure_id ? 1 : 0));
    ordered = get<HTMLInputElement>('pareto-only').checked ? fullOrder.filter(r => pareto.has(r.structure_id)) : fullOrder;
    if (!selected && fullOrder.length) selected = fullOrder[0].structure_id;
    get('feasible-count').textContent = feasible.length.toLocaleString();
    get('pareto-count').textContent = pareto.size.toLocaleString();
    get('leader-label').textContent = `${labels[target]} · 当前集合第一名`;
    get('leader-value').textContent = fullOrder.length ? fmt(fullOrder[0][target], target === 'k2eff_pct' ? 4 : 2) : '—';
    get('leader-structure').textContent = fullOrder.length ? geom(fullOrder[0]) : '当前条件无解';
    const shown = value('top-n') === 'all' ? ordered : ordered.slice(0, Number(value('top-n')));
    get('table-caption').textContent = `显示 ${shown.length} / ${ordered.length} 个${get<HTMLInputElement>('pareto-only').checked ? ' Pareto' : ''}候选 · 同分按结构 ID 排序`;
    get('ranking').querySelector('tbody')!.innerHTML = shown.map((r, i) => `<tr data-id="${esc(r.structure_id)}" class="${r.structure_id === selected ? 'selected' : ''}"><td><input type="checkbox" data-compare="${esc(r.structure_id)}" aria-label="比较 ${esc(name(r))}" ${compared.includes(r.structure_id) ? 'checked' : ''}></td><td>${i + 1}</td><td><button class="row-link" data-select="${esc(r.structure_id)}">${esc(name(r))}</button></td><td>${r.eg_um}</td><td>${r.fl_um}</td><td>${r.area_um2}</td><td>${r.has_pf}</td>${targets.map(t => `<td class="${t === target ? 'target-cell' : ''}">${fmt(r[t], t === 'k2eff_pct' ? 4 : 2)}</td>`).join('')}<td>${pareto.has(r.structure_id) ? '●' : '—'}</td></tr>`).join('');
    get('empty').hidden = feasible.length > 0;
    get('export-note').textContent = `导出完整筛选集 ${feasible.length} 行（不受 Top-N 或“仅 Pareto”显示限制）`;
    renderPlot(); renderDetail(); renderComparison();
  }
  const candVal = (e: P11Entry, t: Target) => t === 'qp' ? e.qp : t === 'k2eff_pct' ? e.k2eff_pct : null;
  function renderPlot(): void {
    if (!feasible.length) { get('plot').innerHTML = '<div class="empty">当前条件下没有可绘制候选</div>'; return; }
    const xKey = value('x-axis') as Target, yKey = value('y-axis') as Target;
    const xs = feasible.map(r => Number(r[xKey])), ys = feasible.map(r => Number(r[yKey]));
    const candEntries = (pack.p12_candidates ?? pack.p11_candidates)?.entries ?? [];
    const candDrawable = showCand && candEntries.length > 0 && candVal(candEntries[0], xKey) !== null && candVal(candEntries[0], yKey) !== null;
    if (candDrawable) {
      for (const e of candEntries) { xs.push(candVal(e, xKey)!.prediction); ys.push(candVal(e, yKey)!.prediction); }
    }
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const dx = maxX - minX || Math.max(1, Math.abs(minX) * .1), dy = maxY - minY || Math.max(1, Math.abs(minY) * .1);
    const loX = minX - dx * .06, hiX = maxX + dx * .06, loY = minY - dy * .08, hiY = maxY + dy * .08;
    const px = (x: number) => 56 + (x - loX) / (hiX - loX) * 470;
    const py = (y: number) => 278 - (y - loY) / (hiY - loY) * 245;
    let drawing = '<svg viewBox="0 0 560 325" role="group" aria-label="当前筛选集散点图">';
    for (let i = 0; i <= 4; i++) {
      const x = loX + (hiX - loX) * i / 4, y = loY + (hiY - loY) * i / 4;
      drawing += `<path d="M56 ${py(y)} H526 M${px(x)} 33 V278" stroke="#e9eee7"/><text x="46" y="${py(y) + 4}" text-anchor="end" fill="#78877c" font-size="10">${fmt(y, yKey === 'k2eff_pct' ? 1 : 0)}</text><text x="${px(x)}" y="297" text-anchor="middle" fill="#78877c" font-size="10">${fmt(x, xKey === 'k2eff_pct' ? 1 : 0)}</text>`;
    }
    drawing += `<text x="291" y="319" text-anchor="middle" font-size="11" fill="#536d5c">${labels[xKey]}</text><text x="57" y="16" font-size="11" fill="#536d5c">${labels[yKey]}</text>`;
    const layered = [...feasible].sort((a, b) => Number(pareto.has(a.structure_id)) - Number(pareto.has(b.structure_id)));
    for (const r of layered) {
      const p = pareto.has(r.structure_id), active = r.structure_id === selected;
      drawing += `<circle class="dot" data-select="${esc(r.structure_id)}" cx="${px(Number(r[xKey]))}" cy="${py(Number(r[yKey]))}" r="${active ? 6 : p ? 3.5 : 2.5}" fill="${active ? '#d79232' : p ? '#217c60' : '#b7cbbd'}" opacity="${p || active ? 1 : .65}"><title>${esc(name(r))} · ${esc(geom(r))}\n${labels[xKey]} ${fmt(r[xKey], 4)} · ${labels[yKey]} ${fmt(r[yKey], 4)}</title></circle>`;
    }
    if (candDrawable) {
      for (const e of candEntries) {
        const cx = px(candVal(e, xKey)!.prediction), cy = py(candVal(e, yKey)!.prediction);
        const active = e.candidate_id === selectedCand;
        drawing += `<polygon class="cand-dot${active ? ' active' : ''}" data-cand="${esc(e.candidate_id)}" points="${cx},${cy - 6} ${cx + 6},${cy} ${cx},${cy + 6} ${cx - 6},${cy}"><title>${esc(e.candidate_id)}（外推候选，无实测器件）\n${labels[xKey]} ${fmt(candVal(e, xKey)!.prediction, xKey === 'k2eff_pct' ? 4 : 1)} · ${labels[yKey]} ${fmt(candVal(e, yKey)!.prediction, 1)}（模型外推预测）</title></polygon>`;
      }
    }
    get('cand-legend').hidden = !candDrawable;
    get('plot-note').textContent = candDrawable
      ? '二维投影不代表二维 Pareto；高亮依据四项目标共同计算。红色菱形为外推候选（无实测器件），点击显示结构参数与外推预测。'
      : (showCand && candEntries.length && '外推候选仅有 Qp/k² 预测，仅在 X/Y 轴均为这两者时显示。' || '二维投影不代表二维 Pareto；高亮依据四项目标共同计算。');
    get('plot').innerHTML = drawing + '</svg>';
  }
  function d2Block(row: Row): string {
    const entry = d2For(row.structure_id);
    if (!entry) return '';
    const body = showD2 ? `<div class="d2-body"><p><b>Qp 参考区间（实验性，仅报告）</b></p><p>实测典型值 ${fmt(entry.qp_actual, 1)}</p><p>D2 异方差 · 联合归一化口径：[${fmt(entry.d2_scaled[0], 1)}, ${fmt(entry.d2_scaled[1], 1)}]</p><p>D2 异方差 · 联合全局口径：[${fmt(entry.d2_global[0], 1)}, ${fmt(entry.d2_global[1], 1)}]</p><p>同口径冻结基线 C0：[${fmt(entry.c0_scaled[0], 1)}, ${fmt(entry.c0_scaled[1], 1)}] / [${fmt(entry.c0_global[0], 1)}, ${fmt(entry.c0_global[1], 1)}]</p><p class="detail-note">实验性参考层：P10 回顾性证据，非冻结配置、非新盲测；与上方区间口径不同（联合校准）。仅适用于 PF·EG0·700 μm² 子集的 Qp，其余结构与目标无此参考；不改变排名与点值。内层子集覆盖 14/19；外层 8 结构回顾性覆盖 6/8。证据：docs/superpowers/plans/2026-10-26-surrogate-p10-results.md。所有代理结果 trusted=false；新尺寸外推需独立实测。</p></div>` : '';
    return `<div class="d2-ref"><label class="check-label"><input id="d2-toggle" type="checkbox"${showD2 ? ' checked' : ''}>显示实验性 D2 参考区间（小面积 PF · 700 μm² · Qp）</label>${body}</div>`;
  }
  function renderCandidateDetail(entry: P11Entry): void {
    get('detail-badge').textContent = '外推候选';
    const q = entry.qp, k = entry.k2eff_pct;
    get('detail').innerHTML = `<h3>${esc(entry.candidate_id)}</h3><p class="detail-id">外推候选（无实测器件） · ${esc(entry.group)} 组${entry.origin ? ` · 来源 ${esc(entry.origin)}` : ''} · ${esc(entry.recommendation)}</p><p class="geometry-line">EG ${entry.eg_um} · FL ${entry.fl_um} · ${entry.area_um2} μm² · PF ${entry.has_pf} · ${esc(entry.branch_id)}</p><div class="detail-metrics"><div class="metric"><span class="label">Qp（模型外推）</span><strong>${fmt(q.prediction, 1)}</strong><small>P3 区间 [${fmt(q.lower, 1)}, ${fmt(q.upper, 1)}]<br>P6C 敏感性对照 ${fmt(entry.p6c_qp, 1)}</small></div><div class="metric"><span class="label">k² %（模型外推）</span><strong>${fmt(k.prediction, 4)}</strong><small>P3 区间 [${fmt(k.lower, 3)}, ${fmt(k.upper, 3)}]<br>P6C 敏感性对照 ${fmt(entry.p6c_k2eff_pct, 3)}</small></div></div><p class="detail-note">支持标签 ${esc(entry.p3_support)} · 外推轴 ${esc(entry.extrapolation_axis)} · 距离 ${fmt(entry.extrapolation_distance, 2)}<br>相对基线：Qp ${entry.qp_delta_vs_qp_baseline >= 0 ? '+' : ''}${fmt(entry.qp_delta_vs_qp_baseline, 1)}（C6-1=1395.96）· k² ${entry.k2_delta_vs_k2_baseline >= 0 ? '+' : ''}${fmt(entry.k2_delta_vs_k2_baseline, 3)}（E1-1=14.6745%）</p><p class="detail-note">外推风险：${esc(entry.risk_flags)}</p><p class="detail-note"><b>待实测验证假设，非已证实更优。</b>Qp 无仿真先验（COMSOL 无电学损耗）；外推区区间不可靠（700-PF 0/8 失败史）。证据：${esc((pack.p12_candidates ?? pack.p11_candidates)?.evidence_doc ?? '')}；验证方案见对应轮次 falsification_plan。不进入排名、比较或导出。</p><button id="cand-close" class="secondary">返回结构详情</button>`;
    get('cand-close').onclick = () => { selectedCand = ''; render(); };
  }
  function renderDetail(): void {
    const cand = selectedCand ? (pack.p12_candidates ?? pack.p11_candidates)?.entries.find(e => e.candidate_id === selectedCand) : undefined;
    if (cand) { renderCandidateDetail(cand); return; }
    const row = feasible.find(r => r.structure_id === selected);
    get('detail-badge').textContent = row ? (mode === 'measured' ? '实测典型值' : '仅探索') : '';
    if (!row) { get('detail').innerHTML = '<p class="muted">筛选结果为空。</p>'; return; }
    const measured = mode === 'measured';
    get('detail').innerHTML = `<h3>${esc(name(row))}</h3><p class="detail-id">${esc(row.structure_id)}</p><p class="geometry-line">${esc(geom(row))}</p><div class="detail-metrics">${targets.map(t => {
      const lo = row[`${t}_${measured ? 'ci_low' : 'lower'}`], hi = row[`${t}_${measured ? 'ci_high' : 'upper'}`];
      return `<div class="metric"><span class="label">${labels[t]}</span><strong>${fmt(row[t], t === 'k2eff_pct' ? 4 : 2)}</strong><small>${measured ? '95% bootstrap' : 'P3 单目标区间'}<br>[${fmt(lo, t === 'k2eff_pct' ? 3 : 1)}, ${fmt(hi, t === 'k2eff_pct' ? 3 : 1)}]</small></div>`;
    }).join('')}</div><p class="detail-note">${measured ? '区间为结构典型值估计，未经排名选择校正，不是单器件范围。' : '区间沿用 P3；不保证四目标联合覆盖、优化后赢家或新尺寸。'}</p>${measured ? `<p class="detail-note">有效坐标：${targets.map(t => `${shortLabels[t]} ${esc(row[`${t}_n_coords`])}`).join(' / ')}<br>fs ${fmt(row.fs_ghz, 5)} GHz · fp ${fmt(row.fp_ghz, 5)} GHz</p>` : `<details><summary>逐目标支持依据</summary><div class="support-table">${targets.map(t => `<p><b>${labels[t]}</b>：${esc(supportLabels[String(row[`${t}_local_support`])] || row[`${t}_local_support`])} · 角点 ${esc(row[`${t}_present_corners`])}/${esc(row[`${t}_required_corners`])}<br>最近结构 ${esc(row[`${t}_nearest_structure_id`])} · 归一距离 ${fmt(row[`${t}_nearest_scaled_distance`], 4)}<br>P3 标签 ${esc(row[`${t}_support`])}</p>`).join('')}</div></details><p class="detail-note">${row.in_measured_catalog ? '坐标在实测目录中，本视图仍全用代理预测。' : '此坐标不在实测目录，尚无独立实测验证。'}</p>`}${d2Block(row)}<button id="detail-compare" class="secondary">${compared.includes(row.structure_id) ? '移出比较' : '加入比较'}</button>`;
    get('detail-compare').onclick = () => toggleCompare(row.structure_id);
    const d2Toggle = document.getElementById('d2-toggle') as HTMLInputElement | null;
    if (d2Toggle) d2Toggle.onchange = () => { showD2 = d2Toggle.checked; renderDetail(); };
  }
  function toggleCompare(id: string): void {
    if (compared.includes(id)) compared = compared.filter(x => x !== id);
    else if (compared.length >= 4) { notify('最多比较 4 个结构，请先移除一个。'); render(); return; }
    else compared.push(id);
    notify(); render();
  }
  function renderComparison(): void {
    get('compare-count').textContent = `${compared.length} / 4`;
    const selectedRows = compared.map(id => feasible.find(r => r.structure_id === id)!);
    get('comparison').innerHTML = selectedRows.length ? `<div class="compare-grid">${selectedRows.map(r => `<article class="compare-card"><h3>${esc(name(r))}</h3><p>${esc(geom(r))}</p><dl>${targets.map(t => `<dt>${labels[t]}</dt><dd>${fmt(r[t], t === 'k2eff_pct' ? 4 : 2)}</dd>`).join('')}</dl><p>${mode === 'measured' ? '实测结构典型值' : '未验证代理预测'}</p><button class="text-button" data-remove="${esc(r.structure_id)}">移除</button></article>`).join('')}</div>` : '<p class="muted">勾选排名表中的结构，或在详情中加入比较（最多 4 个）。</p>';
  }
  function reset(): void {
    get<HTMLFormElement>('filters').reset();
    filters = { minimums: {}, bounds: {}, pf: null };
    compared = []; selected = '';
    get<HTMLInputElement>('pareto-only').checked = false;
    get('pending').textContent = '显示已应用的筛选结果。';
    notify(); recompute();
  }
  function updateMode(): void {
    for (const key of ['measured', 'exploratory'] as const) get(`mode-${key}`).setAttribute('aria-pressed', String(mode === key));
    get<HTMLFieldSetElement>('frequency-filters').disabled = mode === 'exploratory';
    get('frequency-note').textContent = mode === 'measured' ? '约束典型值，不代表所有器件均落在频带内。' : '无频率代理，探索模式不支持频率约束。';
    get('scope-banner').className = `scope ${mode}`;
    get('scope-banner').textContent = mode === 'measured' ? '实测目录内寻优 · 使用去嵌结构典型值。排名不代表唯一显著冠军，也不代表新尺寸已验证。' : '仅探索，未验证 · 所有候选均为冻结代理的既有网格预测；局部支持检查不构成准确性或外推保证。';
    get('source-line').textContent = `${pack.source}${imported ? ' · 本地导入，来源声明未独立校验' : ' · 构建时已校验 P4 清单'}`;
    const accepted = catalogs[mode].filter(r => r.eligible).length;
    get('catalog-count').textContent = `${catalogs[mode].length.toLocaleString()} 个记录 · ${accepted.toLocaleString()} 个可参与本模式筛选 · ${(catalogs[mode].length - accepted).toLocaleString()} 个预先排除`;
    get('version').textContent = `${pack.version} / ${pack.model_version}`;
    get('show-cand-label').hidden = !(pack.p12_candidates ?? pack.p11_candidates);
    get('cand-legend').hidden = true;
    reset();
  }
  function download(filename: string, content: string, type: string): void {
    const url = URL.createObjectURL(new Blob([content], { type }));
    const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  }
  function exportCsv(exportRows: Row[], kind: string): void {
    const extra = ['view_mode', 'view_pareto', 'source_catalog_sha256'];
    const columns = [...pack.datasets[mode].columns, ...extra];
    function cell(v: Scalar | undefined): string {
      let text = v == null ? '' : String(v);
      if (typeof v === 'string' && /^[=+@\-\t\r]/.test(text)) text = "'" + text;
      return '"' + text.replace(/"/g, '""') + '"';
    }
    const data = exportRows.map(r => ({ ...r, view_mode: mode, view_pareto: pareto.has(r.structure_id), source_catalog_sha256: pack.datasets[mode].source_sha256 }));
    const csv = [columns.map(cell).join(','), ...data.map(r => columns.map(c => cell((r as Row)[c])).join(','))].join('\r\n');
    download(`ALN_${mode}_${kind}.csv`, '\uFEFF' + csv, 'text/csv;charset=utf-8');
  }
  get('filters').onsubmit = event => {
    event.preventDefault();
    try { const next = readFilters(); filters = next; notify(); recompute(); get('pending').textContent = '筛选已应用。'; }
    catch (error) { notify((error as Error).message); }
  };
  get('filters').oninput = () => { get('pending').textContent = '条件已更改，点击“应用筛选”更新结果。'; };
  get('reset').onclick = reset;
  for (const key of ['measured', 'exploratory'] as const) get(`mode-${key}`).onclick = () => { if (mode !== key) { mode = key; updateMode(); } };
  for (const id of ['target', 'top-n', 'pareto-only', 'x-axis', 'y-axis']) get(id).onchange = render;
  get('plot').onclick = event => {
    const candNode = (event.target as Element).closest('[data-cand]');
    if (candNode) {
      selectedCand = candNode.getAttribute('data-cand')!;
      selected = '';
      renderPlot(); renderDetail();
      return;
    }
    const button = (event.target as Element).closest('[data-select]');
    if (button) { selected = button.getAttribute('data-select')!; selectedCand = ''; render(); }
  };
  get('ranking').onclick = event => {
    const button = (event.target as Element).closest('[data-select]');
    if (button) { selected = button.getAttribute('data-select')!; selectedCand = ''; render(); }
  };
  get('ranking').onchange = event => { const node = event.target as HTMLInputElement; if (node.dataset.compare) toggleCompare(node.dataset.compare); };
  get('comparison').onclick = event => { const button = (event.target as Element).closest('[data-remove]'); if (button) toggleCompare(button.getAttribute('data-remove')!); };
  get('clear-comparison').onclick = () => { compared = []; render(); };
  get('show-cand').onchange = () => {
    showCand = get<HTMLInputElement>('show-cand').checked;
    if (!showCand) selectedCand = '';
    renderPlot(); renderDetail();
  };
  get('export-csv').onclick = () => exportCsv(feasible, 'filtered');
  get('export-pareto').onclick = () => exportCsv(feasible.filter(r => pareto.has(r.structure_id)), 'pareto');
  get('export-rejected').onclick = () => exportCsv(catalogs[mode].filter(r => !r.eligible), 'rejected');
  get('export-json').onclick = () => download(`ALN_${mode}_filtered.json`, JSON.stringify({ schema: 'aln_optimization_view_v1', mode, source_version: pack.version, source_catalog_sha256: pack.datasets[mode].source_sha256, imported_source_unverified: imported, filters, target: value('target'), top_n_display: value('top-n'), pareto_only_display: get<HTMLInputElement>('pareto-only').checked, scope: 'filter_of_precomputed_catalog_not_new_prediction', trusted_for_unmeasured_optimization: false, rows: feasible, pareto_ids: [...pareto], comparison_ids: compared }, null, 2), 'application/json');
  get('help-button').onclick = () => {
    get('help-body').innerHTML = `<p>① 选择实测或探索目录。② 填写条件并应用筛选。③ 切换排名目标，点击点或结构名称查看详情。④ 勾选最多四个结构比较，或导出完整筛选集。</p><p><b>本页只筛选已计算目录，不调用模型、不重新预测新尺寸。</b>新网格需运行 P4 程序，再由 P5 构建器生成 browser_data.json。导入浏览包后会标记来源声明未独立校验；筛选导出的 JSON 是结果记录，不是可重载的浏览包。</p><p>实测为坐标中位数的中位数，Bode Q 固定为 BodeQ_fitted。目标下限、fs/fp 范围约束典型点值，不保证器件良率。探索模式不支持频率约束。所有代理结果均未获新尺寸验证；相同实测坐标在探索视图中仍只显示预测值。</p><p>四目标同时覆盖设计组：普通留出 23/34（67.6%），面积边界 8/48（16.7%），FL 上边界 7/36（19.4%）。区间不保证优化后赢家、单器件或外推。新尺寸外推最终需独立实测。这些是冻结 P3 回顾性证据，不是新盲测。</p>${pack.d2_reference ? '<p><b>实验性 D2 参考区间层</b>：本包内嵌 P10 的 D2 异方差参考区间，仅覆盖 PF·EG0·700 μm² 子集 8 个结构的 Qp。在候选详情中勾选开关后显示；默认关闭，不参与排名、筛选、比较或导出。它是回顾性证据、非冻结配置、非新盲测，证据见 docs/superpowers/plans/2026-10-26-surrogate-p10-results.md。</p>' : ''}<p>彩色点依据四目标共同计算 Pareto。它们在二维投影里可能互相支配。Top-N 只是显示行数，筛选集导出包含全部可行点；CSV 文本公式前缀会加单引号以避免电子表格执行。</p><p>版本与来源</p><pre>${esc(pack.version)}\n${esc(pack.model_version)}\n当前目录 SHA256: ${esc(pack.datasets[mode].source_sha256)}\n${imported ? '本地导入：未独立校验来源真实性' : '内嵌目录：构建器已核对 P4 交付清单'}</pre>`;
    get<HTMLDialogElement>('help').showModal();
  };
  get('close-help').onclick = () => get<HTMLDialogElement>('help').close();
  get('import-file').onchange = async () => {
    const input = get<HTMLInputElement>('import-file'), file = input.files?.[0];
    if (!file) return;
    try {
      assert(file.size <= 30 * 1024 * 1024, '浏览数据包超过 30 MiB');
      const next = validate(JSON.parse(await file.text()));
      pack = next.pack; catalogs = next.rows; imported = true; selectedCand = ''; mode = 'measured'; updateMode();
    } catch (error) { notify(`导入失败，原结果保持不变：${(error as Error).message}`); }
    input.value = '';
  };
  updateMode();
})();
