import { useEffect, useRef } from 'react';
import type {
  AnalysisCommandMessage,
  AnalysisDesktopCommand,
  AnalysisSurface,
} from '../../../electron/analysisCommands';

export type { AnalysisDesktopCommand, AnalysisSurface } from '../../../electron/analysisCommands';

export function isEditableTarget(target: EventTarget | null) {
  if (!(target instanceof Element)) return false;
  return !!target.closest(
    'input, textarea, select, [role="textbox"], [contenteditable]:not([contenteditable="false"])',
  );
}

export function analysisCommandForKey({
  key,
  metaKey,
  ctrlKey,
  shiftKey,
}: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'shiftKey'>) {
  const modifier = metaKey || ctrlKey;
  const normalized = key.toLowerCase();
  if (modifier && normalized === 'z') return shiftKey ? 'redo' : 'undo';
  if (modifier && normalized === 'a') return 'selectAll';
  if (modifier && normalized === 'c') return 'copySelection';
  if (key === 'Delete' || key === 'Backspace') return 'deleteSelection';
  return null;
}

export function showAnalysisContextMenu(
  event: Pick<MouseEvent, 'preventDefault'> & Partial<Pick<MouseEvent, 'target'>>,
  surface: AnalysisSurface,
) {
  if (isEditableTarget(event.target || null)) return false;
  if (!window.electronAPI?.showAnalysisContextMenu) return false;
  event.preventDefault();
  window.electronAPI.showAnalysisContextMenu(surface);
  return true;
}

export function useAnalysisCommandBridge({
  surface,
  onCommand,
}: {
  surface: AnalysisSurface;
  onCommand: (command: AnalysisDesktopCommand, surface: AnalysisSurface) => void;
}) {
  const commandRef = useRef(onCommand);
  const lastDispatchRef = useRef<{
    command: AnalysisDesktopCommand;
    surface: AnalysisSurface;
    at: number;
  } | null>(null);
  commandRef.current = onCommand;
  const dispatch = (command: AnalysisDesktopCommand, targetSurface: AnalysisSurface) => {
    const now = performance.now();
    const last = lastDispatchRef.current;
    if (last?.command === command && last.surface === targetSurface && now - last.at < 50) return;
    lastDispatchRef.current = { command, surface: targetSurface, at: now };
    commandRef.current(command, targetSurface);
  };

  useEffect(() => {
    const setContext = window.electronAPI?.setAnalysisContext;
    if (!setContext) return;
    let active = true;
    const syncContext = () => {
      if (!active) return;
      setContext(!isEditableTarget(document.activeElement), surface);
    };
    const handleFocusOut = () => queueMicrotask(syncContext);
    syncContext();
    document.addEventListener('focusin', syncContext);
    document.addEventListener('focusout', handleFocusOut);
    return () => {
      active = false;
      document.removeEventListener('focusin', syncContext);
      document.removeEventListener('focusout', handleFocusOut);
      setContext(false, surface);
    };
  }, [surface]);

  useEffect(() => window.electronAPI?.onAnalysisCommand?.((message: AnalysisCommandMessage) => {
    dispatch(message.command, message.surface);
  }), []);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target)) return;
      const command = analysisCommandForKey(event);
      if (!command) return;
      event.preventDefault();
      dispatch(command, surface);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [surface]);
}
