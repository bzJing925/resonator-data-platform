import React from 'react';

const STAGES: { key: string; label: string }[] = [
  { key: 'extract', label: '解压' },
  { key: 'deembed', label: '去嵌' },
  { key: 'metrics', label: '指标计算' },
];

interface Props {
  stage?: string;
  stageProgress?: number;
}

export default function StageProgressBars({ stage = 'extract', stageProgress = 0 }: Props) {
  const currentIndex = STAGES.findIndex((s) => s.key === stage);
  return (
    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
      {STAGES.map((s, idx) => {
        let pct = 0;
        if (idx < currentIndex) pct = 100;
        else if (idx === currentIndex) pct = Math.max(0, Math.min(100, stageProgress));
        const active = idx === currentIndex;
        return (
          <div key={s.key} style={{ flex: 1 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
              <span className={active ? '' : 'dim'}>{s.label}</span>
              <span className="mono dim">{pct}%</span>
            </div>
            <div
              style={{
                height: 4,
                background: 'var(--bg-panel-2)',
                border: '1px solid var(--border)',
                borderRadius: 2,
                overflow: 'hidden',
              }}
            >
              <div
                style={{
                  width: `${pct}%`,
                  height: '100%',
                  background: active ? 'var(--primary)' : 'var(--pass)',
                  transition: 'width 0.3s',
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
