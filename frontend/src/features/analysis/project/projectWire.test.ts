import { expect, it } from 'vitest';
import { ProjectValueReader, sendProjectValue } from './projectWire';
it('bounded transferable transport preserves aliases, dangerous keys and numeric cells', async () => {
  const shared = { value: NaN }, value = { first: shared, second: shared, rows: Array.from({length: 2001}, (_, i) => ({ i, empty: undefined, number: i === 0 ? -0 : Infinity })), data: JSON.parse('{"__proto__":{"polluted":true}}') };
  const reader = new ProjectValueReader(), buffers: ArrayBuffer[] = [];
  await sendProjectValue(value, (buffer) => { buffers.push(buffer); reader.append(buffer); });
  const output = reader.value as typeof value;
  expect(buffers.length).toBeGreaterThan(1); expect(output.first).toBe(output.second);
  expect(output.rows).toHaveLength(2001); expect(Object.is(output.rows[0].number, -0)).toBe(true);
  expect(output.rows[1].number).toBe(Infinity); expect(output.first.value).toBeNaN();
  expect(output.rows[0]).toHaveProperty('empty', undefined); expect({}).not.toHaveProperty('polluted');
  expect(Object.prototype.hasOwnProperty.call(output.data, '__proto__')).toBe(true);
});
