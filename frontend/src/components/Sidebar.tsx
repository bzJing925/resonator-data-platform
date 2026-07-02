import React from 'react';
import { NavLink } from 'react-router-dom';
import I from './Icons';

const NAV = [
  { to: '/', icon: I.dashboard, label: '仪表盘', end: true },
  { to: '/explore', icon: I.scatter, label: '数据分析' },
  { to: '/impedance', icon: I.curve, label: '阻抗曲线' },
  { to: '/batches', icon: I.batches, label: '批次管理' },
  { to: '/mappings', icon: I.table, label: '对照表' },
  { to: '/upload', icon: I.upload, label: '上传' },
  { to: '/tasks', icon: I.cpu, label: '任务' },
];

export default function Sidebar() {
  return (
    <div className="rail">
      {NAV.map((n) => {
        const Ico = n.icon;
        return (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) => `rail-btn${isActive ? ' active' : ''}`}
          >
            <Ico size={18} />
            <span className="label">{n.label}</span>
          </NavLink>
        );
      })}
      <div className="grow" />
      <button className="rail-btn" title="滤波器（二期）" disabled style={{ opacity: 0.35 }}>
        <I.layers size={18} />
        <span className="label">滤波器（二期）</span>
      </button>
      <div className="sep" />
      <button className="rail-btn" title="设置">
        <I.settings size={18} />
        <span className="label">设置</span>
      </button>
    </div>
  );
}
