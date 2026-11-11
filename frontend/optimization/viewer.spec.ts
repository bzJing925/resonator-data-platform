import { test, expect, type Page, type Download } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const build = process.env.ALN_P5_BUILD || path.resolve(here, '../../backend/surrogate_data/optimization_p5_20261016/release');
const p4 = path.resolve(here, '../../backend/surrogate_data/optimization_p4_20261016');
const targets = ['k2eff_pct', 'qs', 'qp', 'bodeq_fitted'];
const errors = new WeakMap<Page, string[]>();
const requests = new WeakMap<Page, string[]>();

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [], field = '', quoted = false;
  text = text.replace(/^\uFEFF/, '');
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { field += '"'; i++; }
      else quoted = !quoted;
    } else if (c === ',' && !quoted) { row.push(field); field = ''; }
    else if ((c === '\n' || c === '\r') && !quoted) {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); if (row.some(x => x.length)) rows.push(row); row = []; field = '';
    } else field += c;
  }
  row.push(field); if (row.some(x => x.length)) rows.push(row);
  return rows;
}

async function csvRecords(file: string) {
  const [columns, ...rows] = parseCsv(await fs.readFile(file, 'utf8'));
  return rows.map(row => Object.fromEntries(columns.map((c, i) => [c, row[i]])));
}

async function saveDownload(download: Download, name: string) {
  const output = test.info().outputPath(name);
  await download.saveAs(output);
  return output;
}

async function exported(page: Page, button: string, name: string) {
  const waiting = page.waitForEvent('download');
  await page.locator(button).click();
  return saveDownload(await waiting, name);
}

test.beforeEach(async ({ page }) => {
  const captured: string[] = [], network: string[] = [];
  errors.set(page, captured); requests.set(page, network);
  page.on('pageerror', error => captured.push(error.message));
  page.on('request', request => { if (/^https?:/.test(request.url())) network.push(request.url()); });
  await page.goto(pathToFileURL(path.join(build, 'AlN结构寻优.html')).href);
  await expect(page.locator('#feasible-count')).toHaveText('576');
});

test.afterEach(async ({ page }) => {
  expect(errors.get(page)).toEqual([]);
  expect(requests.get(page)).toEqual([]);
});

test('offline startup, all four rankings, Pareto and full precision exports agree with P4', async ({ page }) => {
  await expect(page.locator('#pareto-count')).toHaveText('125');
  for (const target of targets) {
    await page.locator('#target').selectOption(target);
    const source = await csvRecords(path.join(p4, 'measured', `top_${target}.csv`));
    expect(await page.locator('#ranking tbody tr').evaluateAll(rows => rows.map(r => r.getAttribute('data-id')))).toEqual(source.map(r => r.structure_id));
    await page.locator('#ranking tbody tr').first().locator('[data-select]').click();
    await expect(page.locator('#detail')).toContainText(source[0].structure_id);
  }
  const downloaded = await exported(page, '#export-json', 'measured.json');
  const data = JSON.parse(await fs.readFile(downloaded, 'utf8'));
  expect(data.rows).toHaveLength(576);
  const all = await csvRecords(path.join(p4, 'measured/candidates.csv'));
  const byId = new Map(all.map(r => [r.structure_id, r]));
  for (const row of data.rows) for (const target of targets) expect(Math.abs(row[target] - Number(byId.get(row.structure_id)![target]))).toBeLessThan(1e-9);
  const expectedPareto = await csvRecords(path.join(p4, 'measured/pareto.csv'));
  expect([...data.pareto_ids].sort()).toEqual(expectedPareto.map(r => r.structure_id).sort());
  const csv = await csvRecords(await exported(page, '#export-csv', 'measured.csv'));
  expect(csv).toHaveLength(576);
  expect(csv.every(r => r.view_mode === 'measured' && r.trusted_for_unmeasured_optimization === 'false')).toBe(true);
  for (const row of csv) for (const target of targets) expect(Math.abs(Number(row[target]) - Number(byId.get(row.structure_id)![target]))).toBeLessThan(1e-9);
  await page.locator('#pareto-only').check();
  await page.locator('#top-n').selectOption('all');
  expect(await page.locator('#ranking tbody tr').count()).toBe(125);
  const paretoCsv = await csvRecords(await exported(page, '#export-pareto', 'pareto.csv'));
  expect(paretoCsv.map(r => r.structure_id).sort()).toEqual(expectedPareto.map(r => r.structure_id).sort());
});

test('filters apply atomically; constrained example and no-solution match P4', async ({ page }) => {
  for (const [key, v] of Object.entries({ k2eff_pct: 12, qs: 400, qp: 1000, bodeq_fitted: 1000 })) await page.locator(`#min-${key}`).fill(String(v));
  await page.locator('#lo-fs_ghz').fill('5'); await page.locator('#hi-fs_ghz').fill('5.2');
  await expect(page.locator('#feasible-count')).toHaveText('576');
  await expect(page.locator('#pending')).toContainText('条件已更改');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await expect(page.locator('#feasible-count')).toHaveText('3');
  const actual = JSON.parse(await fs.readFile(await exported(page, '#export-json', 'constrained.json'), 'utf8'));
  const expected = await csvRecords(path.join(p4, 'constrained_feasible_example/feasible.csv'));
  expect(actual.rows.map((r: { structure_id: string }) => r.structure_id).sort()).toEqual(expected.map(r => r.structure_id).sort());
  await page.locator('#lo-area_um2').fill('5000'); await page.locator('#hi-area_um2').fill('700');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await expect(page.locator('#message')).toContainText('最小值不能大于最大值');
  await expect(page.locator('#feasible-count')).toHaveText('3');
  await page.locator('#reset').click(); await page.locator('#min-k2eff_pct').fill('99');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await expect(page.locator('#empty')).toBeVisible();
  await expect(page.locator('#pareto-count')).toHaveText('0');
  const empty = JSON.parse(await fs.readFile(await exported(page, '#export-json', 'empty.json'), 'utf8'));
  expect(empty.rows).toEqual([]); expect(empty.pareto_ids).toEqual([]);
  await page.locator('#reset').click(); await expect(page.locator('#feasible-count')).toHaveText('576');
});

test('plot, table, comparison and filter selection share structure IDs', async ({ page }) => {
  const point = page.locator('#plot circle[data-select]').last();
  const id = await point.getAttribute('data-select');
  await point.click();
  await expect(page.locator('#detail')).toContainText(id!);
  await page.locator('#detail-compare').click();
  await expect(page.locator('#compare-count')).toHaveText('1 / 4');
  await page.locator('#clear-comparison').click();
  for (let i = 0; i < 4; i++) await page.locator('#ranking tbody input[type=checkbox]').nth(i).check();
  await expect(page.locator('#compare-count')).toHaveText('4 / 4');
  await page.locator('#ranking tbody input[type=checkbox]').nth(4).click();
  await expect(page.locator('#ranking tbody input[type=checkbox]').nth(4)).not.toBeChecked();
  await expect(page.locator('#message')).toContainText('最多比较 4 个');
  await expect(page.locator('#comparison .compare-card')).toHaveCount(4);
  const rows = await csvRecords(path.join(p4, 'measured/top_k2eff_pct.csv'));
  for (let i = 0; i < 4; i++) for (const target of targets) await expect(page.locator('#comparison .compare-card').nth(i)).toContainText(Number(rows[i][target]).toFixed(target === 'k2eff_pct' ? 4 : 2));
  await page.locator('#pf').selectOption('1');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await expect(page.locator('#compare-count')).toHaveText('0 / 4');
  await expect(page.locator('#feasible-count')).toHaveText('96');
});

test('exploration retains all rejection gates and forbids frequency constraints', async ({ page }) => {
  await page.locator('#lo-fs_ghz').fill('5');
  await page.locator('#mode-exploratory').click();
  await expect(page.locator('#feasible-count')).toHaveText('1,436');
  await expect(page.locator('#pareto-count')).toHaveText('287');
  await expect(page.locator('#scope-banner')).toContainText('仅探索，未验证');
  await expect(page.locator('#lo-fs_ghz')).toBeDisabled();
  await expect(page.locator('#lo-fs_ghz')).toHaveValue('');
  const source = await csvRecords(path.join(p4, 'exploratory/feasible.csv'));
  const downloaded = JSON.parse(await fs.readFile(await exported(page, '#export-json', 'exploration.json'), 'utf8'));
  expect(downloaded.rows.map((r: { structure_id: string }) => r.structure_id).sort()).toEqual(source.map(r => r.structure_id).sort());
  expect(downloaded.rows.every((r: Record<string, unknown>) => r.trusted_for_unmeasured_optimization === false && r.value_source === 'frozen_p3_surrogate_exploratory')).toBe(true);
  for (const target of targets) {
    await page.locator('#target').selectOption(target);
    const expected = await csvRecords(path.join(p4, 'exploratory', `top_${target}.csv`));
    expect(await page.locator('#ranking tbody tr').evaluateAll(rows => rows.map(r => r.getAttribute('data-id')))).toEqual(expected.map(r => r.structure_id));
  }
  const rejected = await csvRecords(await exported(page, '#export-rejected', 'rejected.csv'));
  expect(rejected).toHaveLength(1498);
  expect(rejected.every(r => r.eligible === 'false' && r.rejection_reasons)).toBe(true);
  await page.screenshot({ path: test.info().outputPath('exploratory-desktop.png'), fullPage: true });
  await page.locator('#mode-measured').click();
  await expect(page.locator('#lo-fs_ghz')).toBeEnabled();
  await expect(page.locator('#feasible-count')).toHaveText('576');
});

test('import validates before replacing state and safely renders local strings', async ({ page }) => {
  await page.locator('#import-file').setInputFiles(path.join(build, 'browser_data.json'));
  await expect(page.locator('#source-line')).toContainText('来源声明未独立校验');
  const data = JSON.parse(await fs.readFile(path.join(build, 'browser_data.json'), 'utf8'));
  const unsafe = structuredClone(data);
  unsafe.datasets.exploratory.rows[0][unsafe.datasets.exploratory.columns.indexOf('trusted_for_unmeasured_optimization')] = true;
  await page.locator('#import-file').setInputFiles({ name: 'invalid.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(unsafe)) });
  await expect(page.locator('#message')).toContainText('可信标志必须为 false');
  await expect(page.locator('#feasible-count')).toHaveText('576');
  unsafe.datasets.exploratory.rows[0][unsafe.datasets.exploratory.columns.indexOf('trusted_for_unmeasured_optimization')] = false;
  unsafe.datasets.measured.rows.push(unsafe.datasets.measured.rows[0]);
  unsafe.datasets.measured.summary.n_candidates++;
  await page.locator('#import-file').setInputFiles({ name: 'duplicate.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(unsafe)) });
  await expect(page.locator('#message')).toContainText('ID 缺失或重复');
  const columns = data.datasets.measured.columns;
  const idIndex = columns.indexOf('structure_id'), markIndex = columns.indexOf('marks');
  const winner = data.datasets.measured.rows.find((r: unknown[]) => r[idIndex] === 's_f7db3a637f7a4b11');
  winner[markIndex] = '<img src=x onerror="window.injected=true">';
  await page.locator('#import-file').setInputFiles({ name: 'text.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data)) });
  await expect(page.locator('#detail')).toContainText('<img src=x');
  expect(await page.locator('#detail img').count()).toBe(0);
  expect(await page.evaluate(() => 'injected' in window)).toBe(false);
  winner[markIndex] = '=1+1';
  await page.locator('#import-file').setInputFiles({ name: 'formula.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(data)) });
  const csv = await csvRecords(await exported(page, '#export-csv', 'safe.csv'));
  expect(csv.find(r => r.structure_id === 's_f7db3a637f7a4b11')!.marks).toBe("'=1+1");
});

test('desktop and narrow-screen layout, help and keyboard navigation', async ({ page }) => {
  await page.screenshot({ path: test.info().outputPath('measured-desktop.png'), fullPage: true });
  await page.locator('#help-button').click(); await expect(page.locator('#help')).toBeVisible();
  await expect(page.locator('#help-body')).toContainText('16.7%');
  await page.keyboard.press('Escape'); await expect(page.locator('#help')).not.toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('#reset').click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.locator('#target').selectOption('qs');
  await page.locator('#ranking tbody [data-select]').first().focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('#detail')).toContainText('G8-1');
  await page.screenshot({ path: test.info().outputPath('measured-mobile.png'), fullPage: true });
});

test('D2 reference layer is opt-in, subset-only, and tamper-rejected', async ({ page }) => {
  const data = JSON.parse(await fs.readFile(path.join(build, 'browser_data.json'), 'utf8'));
  const ref = data.d2_reference;
  expect(ref.schema).toBe('aln_optimization_d2_reference_v1');
  expect(ref.experimental).toBe(true);
  expect(ref.frozen_config).toBe(false);
  expect(ref.new_blind_test).toBe(false);
  expect(ref.entries).toHaveLength(8);
  const entry = ref.entries.find((e: { structure_id: string }) => e.structure_id === 's_957fa5d930c6092a');
  await page.locator('#pf').selectOption('1');
  await page.locator('#lo-area_um2').fill('700'); await page.locator('#hi-area_um2').fill('700');
  await page.getByRole('button', { name: '应用筛选', exact: true }).click();
  await expect(page.locator('#feasible-count')).toHaveText('8');
  await page.locator('tr[data-id="s_957fa5d930c6092a"] [data-select]').click();
  const toggle = page.locator('#d2-toggle');
  await expect(toggle).toBeVisible();
  await expect(toggle).not.toBeChecked();
  await expect(page.locator('#detail .d2-body')).toHaveCount(0);
  await toggle.check();
  await expect(page.locator('#detail .d2-body')).toContainText('实验性参考层');
  await expect(page.locator('#detail .d2-body')).toContainText(`${entry.d2_scaled[0].toFixed(1)}, ${entry.d2_scaled[1].toFixed(1)}`);
  await expect(page.locator('#detail .d2-body')).toContainText(`${entry.c0_scaled[0].toFixed(1)}, ${entry.c0_scaled[1].toFixed(1)}`);
  await expect(page.locator('#detail .d2-body')).toContainText('非冻结配置');
  await expect(page.locator('#detail .d2-body')).toContainText('独立实测');
  await page.screenshot({ path: test.info().outputPath('d2-detail.png') });
  await toggle.uncheck();
  await expect(page.locator('#detail .d2-body')).toHaveCount(0);
  await page.locator('tr[data-id="s_957fa5d930c6092a"] [data-select]').click();
  await page.locator('#reset').click();
  await page.locator('#target').selectOption('k2eff_pct');
  await page.locator('#ranking tbody [data-select]').first().click();
  await expect(page.locator('#d2-toggle')).toHaveCount(0);
  const tampered = structuredClone(data);
  tampered.d2_reference.experimental = false;
  await page.locator('#import-file').setInputFiles({ name: 'tampered.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(tampered)) });
  await expect(page.locator('#message')).toContainText('实验性回顾证据');
  await expect(page.locator('#feasible-count')).toHaveText('576');
  const extra = structuredClone(data);
  extra.d2_reference.entries.push(extra.d2_reference.entries[0]);
  await page.locator('#import-file').setInputFiles({ name: 'extra.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(extra)) });
  await expect(page.locator('#message')).toContainText('恰好覆盖 8 个');
});


test('extrapolation candidates render as red points with click-through detail', async ({ page }) => {
  const data = JSON.parse(await fs.readFile(path.join(build, 'browser_data.json'), 'utf8'));
  const block = data.p12_candidates ?? data.p11_candidates;
  expect(['aln_optimization_p12_candidates_v1', 'aln_optimization_p11_candidates_v1']).toContain(block.schema);
  expect(block.experimental).toBe(true);
  expect(block.new_blind_test).toBe(false);
  const nEntries = block.n_entries ?? 10;
  expect(block.entries).toHaveLength(nEntries);
  const dots = page.locator('#plot polygon.cand-dot');
  await expect(dots).toHaveCount(nEntries);
  await expect(page.locator('#cand-legend')).toBeVisible();
  await expect(page.locator('#feasible-count')).toHaveText('576');
  await expect(page.locator('#pareto-count')).toHaveText('125');
  const entry = block.entries.find((e: { eg_um: number; fl_um: number }) => e.eg_um === 1.25 && e.fl_um === 1.5);
  await page.locator(`polygon.cand-dot[data-cand="${entry.candidate_id}"]`).click();
  await expect(page.locator('#detail-badge')).toHaveText('外推候选');
  await expect(page.locator('#detail')).toContainText('EG 1.25 · FL 1.5 · 5500 μm² · PF 0');
  await expect(page.locator('#detail')).toContainText(entry.qp.prediction.toFixed(1));
  await expect(page.locator('#detail')).toContainText(`${entry.qp.lower.toFixed(1)}, ${entry.qp.upper.toFixed(1)}`);
  await expect(page.locator('#detail')).toContainText(entry.k2eff_pct.prediction.toFixed(4));
  await expect(page.locator('#detail')).toContainText('待实测验证假设，非已证实更优');
  await expect(page.locator('#detail')).toContainText('无仿真先验');
  await expect(page.locator('#detail')).toContainText(entry.candidate_id);
  await page.screenshot({ path: test.info().outputPath('candidate-detail.png') });
  await page.locator('#cand-close').click();
  await expect(page.locator('#detail-badge')).not.toHaveText('外推候选');
  await page.locator('#show-cand').uncheck();
  await expect(page.locator('#plot polygon.cand-dot')).toHaveCount(0);
  await page.locator('#show-cand').check();
  await expect(page.locator('#plot polygon.cand-dot')).toHaveCount(nEntries);
  await page.locator('#x-axis').selectOption('qs');
  await expect(page.locator('#plot polygon.cand-dot')).toHaveCount(0);
  await expect(page.locator('#plot-note')).toContainText('仅有 Qp/k² 预测');
  await page.locator('#x-axis').selectOption('k2eff_pct');
  await expect(page.locator('#plot polygon.cand-dot')).toHaveCount(nEntries);
  await expect(page.locator('#feasible-count')).toHaveText('576');
  const tampered = structuredClone(data);
  const key = data.p12_candidates ? 'p12_candidates' : 'p11_candidates';
  tampered[key].entries = tampered[key].entries.slice(0, 1);
  await page.locator('#import-file').setInputFiles({ name: 'tampered-cand.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(tampered)) });
  await expect(page.locator('#message')).toContainText('个条目');
  await expect(page.locator('#feasible-count')).toHaveText('576');
  await expect(page.locator('#plot polygon.cand-dot')).toHaveCount(nEntries);
});
