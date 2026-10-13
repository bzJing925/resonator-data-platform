import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { UnifiedChartGrid } from '../../src/components/Charts';
import { datumRowIds } from '../../src/features/analysis/chart/chartInteraction';

const xFields = [{ name: 'tier', label: '档位', isCategorical: true }];
const yFields = [{ name: 'v', label: '指标一' }, { name: 'w', label: '指标二' }];
const zField = { name: 'group', label: '来源组', isCategorical: true };

function SwarmHarness() {
  const [mode, setMode] = useState('analysis');
  const [slots, setSlots] = useState(14);
  const [selected, setSelected] = useState(new Set<string>());
  const [fieldsEnabled, setFieldsEnabled] = useState(!window.location.search.includes('empty'));
  const rowsPerSlot = window.location.search.includes('large') ? 360 : 16;
  const rows = React.useMemo(() => Array.from({ length: slots * rowsPerSlot }, (_, index) => ({
    rowId: `r${index}`, tier: `T${Math.floor(index / rowsPerSlot)}`,
    group: index % 2 ? 'B' : 'A', v: 10 + index % 7, w: 40 + index % 11,
  })), [slots, rowsPerSlot]);
  return <main style={{ background: '#181B21', color: '#C4C8D0', padding: 12 }}>
    <select data-testid="mode" value={mode} onChange={(event) => setMode(event.target.value)}>
      <option value="analysis">指标分层</option><option value="color">Z 着色</option>
    </select>
    <input data-testid="slots" type="number" value={slots} onChange={(event) => setSlots(Number(event.target.value))} />
    <output data-testid="selected">{[...selected].join(',')}</output>
    <button data-testid="enable-fields" onClick={() => setFieldsEnabled(true)}>选择字段</button>
    <div data-testid="viewport" style={{ width: 520, overflow: 'auto' }}>
      <UnifiedChartGrid chartType="swarm" rows={rows} xFields={fieldsEnabled ? xFields : []} yFields={yFields}
        zField={mode === 'color' ? zField : null} analysisLayout={mode === 'analysis'}
        height={160} selectedRowIds={selected} revision={mode}
        onPointClick={(datum) => setSelected(new Set(datumRowIds(datum)))} />
    </div>
  </main>;
}

createRoot(document.getElementById('root')!).render(<SwarmHarness />);
