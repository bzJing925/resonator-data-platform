import React, { useLayoutEffect, useRef, useState } from 'react';

interface Props {
  batchNo: string;
  onClose: () => void;
  onSubmit: (newBatchNo: string) => void | Promise<void>;
}

export default function RenameBatchModal({ batchNo, onClose, onSubmit }: Props) {
  const [value, setValue] = useState(batchNo);
  const [submitting, setSubmitting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useLayoutEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const handleSubmit = async () => {
    const newBatchNo = value.trim();
    if (!newBatchNo || newBatchNo === batchNo) {
      onClose();
      return;
    }
    setSubmitting(true);
    try {
      await onSubmit(newBatchNo);
    } finally {
      setSubmitting(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSubmit();
    } else if (e.key === 'Escape') {
      onClose();
    }
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 100,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: 6,
          padding: 20,
          minWidth: 360,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ fontWeight: 600, marginBottom: 12 }}>重命名批次</div>
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', fontSize: 12, marginBottom: 6, color: 'var(--fg-3)' }}>
            新批次号
          </label>
          <input
            ref={inputRef}
            className="input"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={submitting}
            style={{ width: '100%' }}
          />
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn ghost sm" onClick={onClose} disabled={submitting}>取消</button>
          <button className="btn sm" disabled={submitting || !value.trim() || value.trim() === batchNo} onClick={handleSubmit}>
            {submitting ? '保存中…' : '确认'}
          </button>
        </div>
      </div>
    </div>
  );
}
