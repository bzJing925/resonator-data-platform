import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, HashRouter } from 'react-router-dom';
import App from './App';
import BootGate from './components/BootGate';
import './styles.css';

// 桌面版加载本地 file:// 文件，无法用 BrowserRouter，用 HashRouter
const Router = window.location.protocol === 'file:' ? HashRouter : BrowserRouter;

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
