import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import ExportDialog from '../../src/export/ExportDialog';
import { queryDevices } from '../../src/api/endpoints';
import { createAnalysisDataset, type AnalysisDataset } from '../../src/features/analysis/model/dataset';
function Harness() {
  const [dataset, setDataset] = useState<AnalysisDataset | null>(null), [error, setError] = useState('');
  useEffect(() => { queryDevices({ filters: {}, fields: ['qs'], limit: 1 }).then((result: any) => {
    const value = createAnalysisDataset({ id: 'export-fixture', kind: 'query', name: 'workspace', rows: result.rows, manifest: result.manifest });
    value.rows[0].qs = 999; value.edits.push({ rowId: value.rows[0].rowId, field: 'qs', originalValue: 1, currentValue: 999 } as any); setDataset(value);
  }).catch((failure) => setError(String(failure))); }, []);
  return <>{error && <p role="alert">{error}</p>}{dataset && <ExportDialog dataset={dataset} onClose={() => {}} />}</>;
}
createRoot(document.getElementById('root')!).render(<Harness />);
