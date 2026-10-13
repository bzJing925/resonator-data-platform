import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { createAnalysisDataset } from '../model/dataset';
import { sourceA, sourceB } from '../__fixtures__/platformFixtures';
import { emptyProject } from '../project/projectBackup';
import { unverifiedManifest } from '../result/resultManifest';
const mocks = vi.hoisted(() => ({ inspect: vi.fn(), update: vi.fn(), workspace: {} }));
vi.mock('../../../api/endpoints', () => ({ inspectComparisonConditions: mocks.inspect }));
vi.mock('../workspace', () => ({ useAnalysisWorkspace: () => mocks.workspace }));
import ComparisonSetup from './ComparisonSetup';
const fields = [{ name: 'qs', unit: '1' }];
beforeEach(() => {
  mocks.inspect.mockReset(); mocks.update.mockReset();
  const spec = emptyProject().version.spec;
  const datasets = [sourceA, sourceB].map((source, index) => {
    const manifest = { ...unverifiedManifest({ filters: {}, fields: ['qs'], order_by: null }, 1, 1),
      source_refs: [{ ...source, source_device_key: null }], consistency: 'verified' as const };
    return createAnalysisDataset({ id: `d${index}`, name: '同名批次', kind: 'query', rows: [{ qs: 10, _source_ref: source }], manifest });
  });
  mocks.workspace = { datasets, state: { registry: {}, spec, readOnly: false }, updateSpec: mocks.update };
});
it('unknown conditions retain selected UUID sources and never apply or calculate', async () => {
  mocks.inspect.mockImplementation(async ({ comparison }) => ({ schema_version: 1, units: { qs: '1' }, endpoints:
    Object.fromEntries(['a', 'b'].map((side) => [side, { source: comparison[side].source, query_spec: comparison[side].query_spec,
      conditions: null, structures: [], matched_total: null, issues: [{ code: 'CONDITIONS_UNKNOWN', message: '缺少发布证据' }] }])) }));
  render(<ComparisonSetup fields={fields} />);
  fireEvent.change(screen.getByLabelText('比较范围'), { target: { value: 'source' } });
  const a = screen.getByLabelText('比较来源A') as HTMLSelectElement, b = screen.getByLabelText('比较来源B') as HTMLSelectElement;
  const aValue = a.options[1].value, bValue = b.options[2].value;
  fireEvent.change(a, { target: { value: aValue } }); fireEvent.change(b, { target: { value: bValue } });
  fireEvent.click(screen.getByText('审查条件（不计算）'));
  await waitFor(() => expect(mocks.inspect).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getByText('确认比较规格').hasAttribute('disabled')).toBe(true));
  expect(a.value).toBe(aValue); expect(b.value).toBe(bValue); expect(mocks.update).not.toHaveBeenCalled();
  expect(mocks.inspect.mock.calls[0][0].comparison.a.source.origin_id).toBe(sourceA.origin_id);
});
it('snapshot selection validates fields/units and records explicit workspace scope without HTTP', async () => {
  render(<ComparisonSetup fields={fields} />);
  fireEvent.change(screen.getByLabelText('比较来源A'), { target: { value: 'd0' } });
  fireEvent.change(screen.getByLabelText('比较来源B'), { target: { value: 'd1' } });
  fireEvent.click(screen.getByText('使用工作区快照规格'));
  await waitFor(() => expect(mocks.update).toHaveBeenCalledTimes(1));
  expect(mocks.inspect).not.toHaveBeenCalled();
  const result = mocks.update.mock.calls[0][0](emptyProject().version.spec);
  expect(result.comparison.scope).toBe('workspace_snapshot');
  expect(result.comparison.a).toMatchObject({ dataset_id: 'd0', source: null });
});
