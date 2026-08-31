import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, HashRouter } from 'react-router-dom';
import App from './App';
import BootGate from './components/BootGate';
import { IS_STATIC } from './config';
import './styles.css';

// 桌面版加载本地 file:// 文件、静态网页版托管在 GitHub Pages 子路径（无服务端回退），
// 两者都无法用 BrowserRouter，统一 HashRouter
const Router = window.location.protocol === 'file:' || IS_STATIC ? HashRouter : BrowserRouter;

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element not found');
}

createRoot(rootElement).render(
  <React.StrictMode>
    <Router>
      <BootGate>
        <App />
      </BootGate>
    </Router>
  </React.StrictMode>,
);
