import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import Settings from '../../src/pages/Settings';
import ServerAutoSync from '../../src/components/ServerAutoSync';
import { AnalysisWorkspaceProvider } from '../../src/features/analysis/workspace';
function Harness() { const [opened, setOpened] = useState(true); return <><ServerAutoSync /><button onClick={() => setOpened((value) => !value)}>切换设置页</button>{opened && <Settings />}</>; }
createRoot(document.getElementById('root')!).render(<AnalysisWorkspaceProvider><Harness /></AnalysisWorkspaceProvider>);
