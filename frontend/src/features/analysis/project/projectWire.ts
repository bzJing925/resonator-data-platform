// Bounded transport batches keep large provenance maps off postMessage's object copier.
// This is an internal transport, not a new persisted project format.
type Atom = [number, string | number, 'object' | 'array' | 'json' | 'value' | 'ref' | 'undefined' | 'number', unknown];
export async function sendProjectValue(value: unknown, send: (buffer: ArrayBuffer) => void) {
  await new Promise<void>((resolve) => setTimeout(resolve, 0));
  const seen = new WeakMap<object, number>();
  let nextId = 0, batch: Atom[] = [], batchWeight = 0;
  const flush = async () => {
    const buffer = new TextEncoder().encode(JSON.stringify(batch)).buffer;
    send(buffer); batch = []; batchWeight = 0;
    await new Promise<void>((resolve) => setTimeout(resolve, 0));
  };
  async function visit(item: unknown, parent: number, key: string | number): Promise<void> {
    if (item && typeof item === 'object') {
      const old = seen.get(item);
      if (old !== undefined) batch.push([parent, key, 'ref', old]);
      else {
        const id = nextId++; seen.set(item, id);
        if (!Array.isArray(item)) {
          const fields = Object.keys(item);
          let weight = 0;
          const flat = fields.length <= 64 && fields.every((field) => {
            const cell = (item as Record<string, unknown>)[field];
            weight += field.length + (typeof cell === 'string' ? cell.length : 32);
            return cell === null || typeof cell === 'boolean' || typeof cell === 'string'
              || typeof cell === 'number' && Number.isFinite(cell) && !Object.is(cell, -0);
          });
          if (flat && weight <= 64 * 1024) {
            batch.push([parent, key, 'json', [id, item]]); batchWeight += weight;
            if (batch.length >= 2000 || batchWeight >= 128 * 1024) await flush();
            return;
          }
        }
        batch.push([parent, key, Array.isArray(item) ? 'array' : 'object', id]);
        if (batch.length >= 2000) await flush();
        for (const field of Object.keys(item)) await visit((item as Record<string, unknown>)[field], id, field);
        return;
      }
    } else if (item === undefined) batch.push([parent, key, 'undefined', null]);
    else if (typeof item === 'number' && (!Number.isFinite(item) || Object.is(item, -0))) batch.push([parent, key, 'number', String(item === 0 ? '-0' : item)]);
    else batch.push([parent, key, 'value', item]);
    if (batch.length >= 2000) await flush();
  }
  await visit(value, -1, 'root');
  if (batch.length) await flush();
}
export class ProjectValueReader {
  private nodes: Record<string, unknown>[] = [];
  value: unknown;
  append(buffer: ArrayBuffer) {
    const atoms: Atom[] = JSON.parse(new TextDecoder().decode(buffer));
    for (const [parent, key, kind, raw] of atoms) {
      let value: unknown = raw;
      if (kind === 'object' || kind === 'array') {
        value = kind === 'array' ? [] : {};
        this.nodes[raw as number] = value as Record<string, unknown>;
      } else if (kind === 'json') {
        const [id, object] = raw as [number, Record<string, unknown>]; value = object; this.nodes[id] = object;
      } else if (kind === 'ref') value = this.nodes[raw as number];
      else if (kind === 'undefined') value = undefined;
      else if (kind === 'number') value = Number(raw);
      if (parent === -1) this.value = value;
      else Object.defineProperty(this.nodes[parent], key, { value, writable: true, enumerable: true, configurable: true });
    }
  }
}
