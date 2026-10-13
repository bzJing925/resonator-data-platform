import React from 'react';
import { NavLink } from 'react-router-dom';
import I from './Icons';
import { IS_STATIC } from '../config';

const NAV = [
  { to: '/', icon: I.dashboard, label: '仪表盘', end: true },
  { to: '/explore', icon: I.scatter, label: '数据分析' },
  { to: '/impedance', icon: I.curve, label: '阻抗曲线' },
  { to: '/surrogate', icon: I.scatter, label: '代理模型' },
  { to: '/batches', icon: I.batches, label: '批次管理' },
  { to: '/mappings', icon: I.table, label: '对照表' },
  { to: '/process', icon: I.wafer, label: '工艺数据' },
  { to: '/upload', icon: I.upload, label: '上传' },
  { to: '/tasks', icon: I.cpu, label: '任务' },
];

// 静态网页版：无上传/任务/阻抗曲线（原文件查看依赖后端）
const STATIC_HIDDEN = new Set(['/upload', '/tasks', '/impedance']);
const VISIBLE_NAV = IS_STATIC ? NAV.filter((n) => !STATIC_HIDDEN.has(n.to)) : NAV;

export default function Sidebar() {
  return (
    <div className="rail">
      {VISIBLE_NAV.map((n) => {
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
      <NavLink to="/settings" className="rail-btn" title="设置">
        <I.settings size={18} />
        <span className="label">设置</span>
      </NavLink>
    </div>
  );
}
