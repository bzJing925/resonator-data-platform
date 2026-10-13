import { beforeEach, expect, it, vi } from 'vitest';
const query = vi.hoisted(() => vi.fn());
vi.mock('../duckdb', () => ({ getConn: async () => ({ query }), ensureTable: async () => {},
  assertDeviceSnapshot: async () => {}, deviceSnapshotMetadata: () => null, q: (v: unknown) => JSON.stringify(v), normalizeRow: (r: unknown) => r }));
import { queryDevices, queryAggregate } from '../index';
beforeEach(() => { query.mockReset(); });
function table(rows: Record<string, unknown>[]) { return { toArray: () => rows.map((row) => ({ toJSON: () => row })) }; }
it('uses limit+1; cap does not pretend to be the exact 300001 count', async () => {
  query.mockResolvedValueOnce(table([{ qs: 1 }, { qs: 2 }, { qs: 3 }]))
    .mockResolvedValueOnce(table([{ c: 300001 }]));
  const result = await queryDevices({ fields: ['qs'], limit: 2 });
  expect(query.mock.calls[0][0]).toContain('LIMIT 3');
  expect(result.manifest.matched_total).toBe(300001);
  expect(result.returned).toBe(2); expect(result.truncated).toBe(true);
  expect(result.manifest.consistency).toBe('unverified');
});
it('skip-count truncated result stays unknown; exact boundary is not truncated', async () => {
  query.mockResolvedValueOnce(table([{ qs: 1 }, { qs: 2 }, { qs: 3 }]));
  expect((await queryDevices({ fields: ['qs'], limit: 2, skip_count: true })).manifest.matched_total).toBeNull();
  query.mockResolvedValueOnce(table([{ qs: 1 }, { qs: 2 }]));
  const boundary = await queryDevices({ fields: ['qs'], limit: 2, skip_count: true });
  expect(boundary.truncated).toBe(false); expect(boundary.manifest.matched_total).toBe(2);
});
it('aggregate includes matched-device count separately from returned groups', async () => {
  query.mockResolvedValueOnce(table([{ eg: 'X', qs__avg: 20 }]))
    .mockResolvedValueOnce(table([{ c: 12 }]));
  const result = await queryAggregate({ group_by: ['eg'], metrics: [{ field: 'qs', agg: ['avg'] }] });
  expect(result.manifest.returned).toBe(1); expect(result.manifest.matched_total).toBe(12);
});
