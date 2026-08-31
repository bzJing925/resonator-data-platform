import React, { useState } from 'react';

const METRICS = [
  { key: 'qbode', label: 'Qbode' },
  { key: 'qs', label: 'Qs' },
  { key: 'qp', label: 'Qp' },
  { key: 'kt2', label: 'kt2' },
];

interface Props {
  batchNo: string;
  onClose: () => void;
  onSubmit: (metrics: string[]) => void;
}

export default function ReprocessMetricsModal({ batchNo, onClose, onSubmit }: Props) {
  const [selected, setSelected] = useState<string[]>(['qs', 'qp', 'kt2', 'qbode']);

  const toggle = (key: string) => {
    setSelected((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
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
          minWidth: 320,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ fontWeight: 600, marginBottom: 12 }}>重新计算指标 - {batchNo}</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 16 }}>
          {METRICS.map((m) => (
            <label key={m.key} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={selected.includes(m.key)}
                onChange={() => toggle(m.key)}
              />
              {m.label}
            </label>
          ))}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
          <button className="btn ghost sm" onClick={onClose}>取消</button>
          <button
            className="btn sm"
            disabled={selected.length === 0}
            onClick={() => onSubmit(selected)}
          >
            确认
          </button>
        </div>
      </div>
    </div>
  );
}
