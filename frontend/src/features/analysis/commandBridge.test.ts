import { describe, expect, it, vi } from 'vitest';
import {
  analysisCommandForKey,
  isEditableTarget,
  showAnalysisContextMenu,
} from './commandBridge';

function key(key: string, options: Partial<KeyboardEvent> = {}) {
  return analysisCommandForKey({
    key,
    metaKey: false,
    ctrlKey: false,
    shiftKey: false,
    ...options,
  });
}

describe('analysis desktop keyboard mapping', () => {
  it('maps editing shortcuts to shared analysis commands', () => {
    expect(key('Delete')).toBe('deleteSelection');
    expect(key('Backspace')).toBe('deleteSelection');
    expect(key('a', { ctrlKey: true })).toBe('selectAll');
    expect(key('c', { metaKey: true })).toBe('copySelection');
    expect(key('z', { ctrlKey: true })).toBe('undo');
    expect(key('z', { metaKey: true, shiftKey: true })).toBe('redo');
  });

  it('ignores unrelated keys', () => {
    expect(key('a')).toBeNull();
    expect(key('x', { metaKey: true })).toBeNull();
  });

  it('recognizes native and contenteditable text editing targets', () => {
    const input = document.createElement('input');
    const editable = document.createElement('div');
    editable.setAttribute('contenteditable', 'true');
    const child = document.createElement('span');
    editable.appendChild(child);

    expect(isEditableTarget(input)).toBe(true);
    expect(isEditableTarget(child)).toBe(true);
    expect(isEditableTarget(document.body)).toBe(false);
  });

  it('requests the restricted Electron context menu for the active surface', () => {
    const previous = window.electronAPI;
    const showAnalysisContextMenuApi = vi.fn();
    window.electronAPI = { showAnalysisContextMenu: showAnalysisContextMenuApi } as any;
    const preventDefault = vi.fn();
    expect(showAnalysisContextMenu({ preventDefault }, 'worksheet')).toBe(true);
    expect(preventDefault).toHaveBeenCalledOnce();
    expect(showAnalysisContextMenuApi).toHaveBeenCalledWith('worksheet');
    window.electronAPI = previous;
  });

  it('does not replace the context menu of an editable field', () => {
    const previous = window.electronAPI;
    const showAnalysisContextMenuApi = vi.fn();
    window.electronAPI = { showAnalysisContextMenu: showAnalysisContextMenuApi } as any;
    const input = document.createElement('input');
    const preventDefault = vi.fn();

    expect(showAnalysisContextMenu({ preventDefault, target: input }, 'chart')).toBe(false);
    expect(preventDefault).not.toHaveBeenCalled();
    expect(showAnalysisContextMenuApi).not.toHaveBeenCalled();
    window.electronAPI = previous;
  });
});
