import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, HashRouter } from 'react-router-dom';
import App from './App.jsx';
import BootGate from './components/BootGate.jsx';
import './styles.css';

// 桌面版加载本地 file:// 文件，BrowserRouter 无法工作，使用 HashRouter
const Router = window.location.protocol === 'file:' ? HashRouter : BrowserRouter;

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Router>
      <BootGate>
        <App />
      </BootGate>
    </Router>
  </React.StrictMode>,
);
