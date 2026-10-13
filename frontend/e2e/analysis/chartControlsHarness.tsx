import React, { useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Plotly from 'plotly.js/dist/plotly';
import { UnifiedChartGrid } from '../../src/components/Charts';
import YAxisRangeControls from '../../src/features/analysis/chart/YAxisRangeControls';
import { buildYDataRanges, type YAxisRanges, datumRowIds } from '../../src/features/analysis/chart/chartInteraction';
import '../../src/styles.css';
const params = new URLSearchParams(location.search);
const metrics = Number(params.get('metrics') || 2), count = Number(params.get('rows') || 224);
const colorMode = params.get('mode') === 'color';
const xFields = [{ name: 'tier', label: '档位', isCategorical: true }];
const yFields = Array.from({ length: metrics }, (_, i) => ({ name: `v${i}`, label: `指标${i+1}` }));
(window as any).plotly = Plotly;
(window as any).timings = [];
for (const method of ['restyle','relayout','react']) {
 const original = (Plotly as any)[method];
 (Plotly as any)[method] = (...args: any[]) => { const start = performance.now(); return Promise.resolve(original(...args)).then(result => { (window as any).timings.push({ method, ms: performance.now()-start }); return result; }); };
}
function Harness() {
 const [selected,setSelected] = useState(new Set<string>()), [mode,setMode] = useState('select');
 const [reset,setReset] = useState(0);
 const [ranges,setRanges] = useState<YAxisRanges>({});
 const resetRanges = () => { setRanges({}); setReset(x=>x+1); };
 const rows = useMemo(() => Array.from({ length: count }, (_, i) => ({ rowId: `r${i}`, tier: `T${i%14}`, group: i%2 ? 'A' : 'B',
 ...Object.fromEntries(yFields.map((f,j)=>[f.name, 100+j*100+(i%17)])) })), []);
 return <main style={{background:'#181B21',color:'white'}}>
 <button data-testid="reset" onClick={resetRanges}>复位</button>
 <select data-testid="interaction" value={mode} onChange={e=>setMode(e.target.value)}><option>select</option><option>pan</option></select>
 <output data-testid="selection">{selected.size}</output>
 <YAxisRangeControls fields={yFields} ranges={{...buildYDataRanges(rows,yFields),...ranges}} onApply={(name,range)=>setRanges({...ranges,[name]:range})} onReset={resetRanges}/>
 <div style={{width:960,overflow:'auto'}}><UnifiedChartGrid chartType="swarm" rows={rows} xFields={xFields} yFields={yFields} height={160} revision="controls" datasetRevision={1} analysisLayout={!colorMode} zField={colorMode ? {name:'group',isCategorical:true} : null} yAxisRanges={ranges} onYAxisReset={()=>setRanges({})} interactionMode={mode as any} autorangeRequest={reset} selectedRowIds={selected}
 onPointClick={d=>setSelected(new Set(datumRowIds(d)))} onSelection={(e:any)=>{ if (e?.points) setSelected(new Set(e.points.flatMap((p:any)=>datumRowIds(p.customdata)))); }} onDeselect={()=>setSelected(new Set())}/></div>
 </main>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
