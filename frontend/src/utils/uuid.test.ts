import { afterEach, describe, expect, it, vi } from 'vitest';
import { createUuid } from './uuid';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('createUuid', () => {
  it('uses the native randomUUID implementation when available', () => {
    const randomUUID = vi.fn(() => '00000000-0000-4000-8000-000000000000');
    vi.stubGlobal('crypto', { randomUUID });

    expect(createUuid()).toBe('00000000-0000-4000-8000-000000000000');
    expect(randomUUID).toHaveBeenCalledOnce();
  });

  it('creates a valid v4 UUID when HTTP only exposes getRandomValues', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(1),
    });

    expect(createUuid()).toBe('01010101-0101-4101-8101-010101010101');
  });

  it('creates unique valid UUIDs when the Web Crypto API is unavailable', () => {
    vi.stubGlobal('crypto', undefined);

    const first = createUuid();
    const second = createUuid();

    expect(first).toMatch(UUID_V4);
    expect(second).toMatch(UUID_V4);
    expect(second).not.toBe(first);
  });
});
