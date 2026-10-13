import type { SourceRef } from '../../../types/platformContracts';

export const sourceA: SourceRef = {
  origin_id: '11111111-1111-4111-8111-111111111111',
  source_batch_id: '22222222-2222-4222-8222-222222222222',
  source_device_key: '33333333-3333-4333-8333-333333333333', source_revision: 7,
};
export const sourceB: SourceRef = { ...sourceA,
  origin_id: '44444444-4444-4444-8444-444444444444',
  source_batch_id: '55555555-5555-4555-8555-555555555555',
  source_device_key: '66666666-6666-4666-8666-666666666666' };
export function platformRows(count: number) {
  return Array.from({ length: count }, (_, index) => ({ id: index + 1, qs: index % 97,
    original_filename: `device-${index}.s1p`, s_param_port: 'S11' }));
}
