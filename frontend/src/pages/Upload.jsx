import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import I from '../components/Icons.jsx';
import { listMappings, uploadBatch } from '../api/endpoints.js';
import { useUploadProgress } from '../contexts/UploadProgressContext.jsx';
import { usePageState } from '../contexts/PageStateContext.jsx';
import useSSE from '../hooks/useSSE.js';

function ProgressBar({ label, pct, status, compact = false }) {
  const color =
    status === 'error' ? 'var(--fail)' : status === 'success' ? 'var(--pass)' : 'var(--primary)';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: compact ? 6 : 10 }}>
      <span style={{ fontSize: 11, color: 'var(--fg-3)', width: 56, flexShrink: 0 }}>{label}</span>
      <div
        style={{
          flex: 1,
          height: compact ? 5 : 8,
          background: 'var(--bg-panel-2)',
          border: '1px solid var(--border)',
          borderRadius: 3,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: '100%',
            background: color,
            transition: 'width 0.3s',
          }}
        />
      </div>
      <span className="mono" style={{ width: 44, textAlign: 'right', fontSize: 11 }}>
        {pct}%
      </span>
    </div>
  );
}

const UPLOAD_INITIAL_STATE = {
  mappingId: '',
  fStart: '',
  fEnd: '',
  deembed: false,
  deembedMethod: 'default',
};

export default function Upload() {
  const [state, setState] = usePageState('upload', UPLOAD_INITIAL_STATE);
  const {
    mappingId,
    fStart,
    fEnd,
    deembed,
    deembedMethod,
  } = state;

  const setMappingId = useCallback((v) => setState((s) => ({ ...s, mappingId: typeof v === 'function' ? v(s.mappingId) : v })), [setState]);
  const setFStart = useCallback((v) => setState((s) => ({ ...s, fStart: typeof v === 'function' ? v(s.fStart) : v })), [setState]);
  const setFEnd = useCallback((v) => setState((s) => ({ ...s, fEnd: typeof v === 'function' ? v(s.fEnd) : v })), [setState]);
  const setDeembed = useCallback((v) => setState((s) => ({ ...s, deembed: typeof v === 'function' ? v(s.deembed) : v })), [setState]);
  const setDeembedMethod = useCallback((v) => setState((s) => ({ ...s, deembedMethod: typeof v === 'function' ? v(s.deembedMethod) : v })), [setState]);

  const [mappings, setMappings] = useState([]);
  const [files, setFiles] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [taskInfo, setTaskInfo] = useState(null);
  const [submitError, setSubmitError] = useState(null);
  const [uploadPct, setUploadPct] = useState(0);
  const inputRef = useRef(null);
  const navigate = useNavigate();
  const { addTask } = useUploadProgress();

  useEffect(() => {
    listMappings()
      .then((data) => {
        const list = Array.isArray(data) ? data : data?.items || [];
        setMappings(list);
        if (list.length && !mappingId) setMappingId(String(list[0].id));
      })
      .catch(() => setMappings([]));
  }, []);

  const sse = useSSE(taskInfo?.task_id, { enabled: !!taskInfo });

  useEffect(() => {
    if (sse.done && sse.status === 'success' && taskInfo?.batch_no) {
      const t = setTimeout(() => navigate(`/batches/${encodeURIComponent(taskInfo.batch_no)}`), 1500);
      return () => clearTimeout(t);
    }
  }, [sse.done, sse.status, taskInfo, navigate]);

  const VALID_EXTS = ['.zip', '.s1p', '.s2p', '.snp'];

  const validateFiles = (list) => {
    const bad = list.filter((f) => !VALID_EXTS.some((ext) => f.name.toLowerCase().endsWith(ext)));
    if (bad.length) {
      setSubmitError(`仅支持 ${VALID_EXTS.join(' / ')} 文件，以下文件被忽略：${bad.map((f) => f.name).join(', ')}`);
      return list.filter((f) => VALID_EXTS.some((ext) => f.name.toLowerCase().endsWith(ext)));
    }
    const empty = list.filter((f) => f.size === 0);
    if (empty.length) {
      setSubmitError(`以下文件为空（0 字节），已忽略：${empty.map((f) => f.name).join(', ')}`);
      return list.filter((f) => f.size > 0);
    }
    setSubmitError(null);
    return list;
  };

  const onPickFiles = (fileList) => {
    if (!fileList || fileList.length === 0) return;
    const picked = validateFiles(Array.from(fileList));
    if (picked.length) setFiles((prev) => [...prev, ...picked]);
  };

  const onDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    onPickFiles(e.dataTransfer.files);
  };

  const onDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const onDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const submit = async () => {
    setSubmitError(null);
    if (files.length === 0) {
      setSubmitError('请选择 .zip / .s1p / .s2p / .snp 文件');
      return;
    }
    if (!mappingId) {
      setSubmitError('请选择对照表（如果列表为空，先到 /mappings 上传）');
      return;
    }

    setSubmitting(true);
    setUploadPct(0);
    const results = [];
    const errors = [];

    for (let i = 0; i < files.length; i++) {
      const f = files[i];
      const fd = new FormData();
      fd.append('file', f);
      fd.append('mapping_id', mappingId);
      if (fStart) fd.append('f_start_ghz', fStart);
      if (fEnd) fd.append('f_end_ghz', fEnd);
      fd.append('deembed', deembed ? 'true' : 'false');
      if (deembed) fd.append('deembed_method', deembedMethod);

      try {
        const res = await uploadBatch(fd, (p) => {
          if (p.total) {
            const filePct = (p.loaded / p.total) * 100;
            const overall = ((i + filePct / 100) / files.length) * 100;
            setUploadPct(Math.round(overall));
          }
        });
        results.push(res);
      } catch (e) {
        errors.push(`${f.name}: ${e.message}`);
      }
    }

    if (errors.length) {
      setSubmitError(`部分文件上传失败（${errors.length}/${files.length}）：\n${errors.join('\n')}`);
    }
    if (results.length) {
      setTaskInfo(results[0]); // 导航到第一个成功的批次
      results.forEach((r) => addTask(r));
    }
    setSubmitting(false);
  };

  const reset = () => {
    setTaskInfo(null);
    setFiles([]);
    setUploadPct(0);
    setSubmitError(null);
    setDragOver(false);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          谐振器 <span style={{ color: 'var(--fg-4)' }}>›</span> <b>上传新批次</b>
        </span>
        <div className="spacer" />
        {mappings.length === 0 && (
          <Link to="/mappings" className="btn">
            <I.table size={13} /> 先去添加对照表
          </Link>
        )}
      </div>

      <div
        style={{
          flex: 1,
          overflow: 'auto',
          padding: 18,
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 14,
          alignContent: 'start',
          maxWidth: 1280,
          margin: '0 auto',
        }}
      >
        <div className="chart-card" style={{ margin: 0 }}>
          <div className="chart-head">
            <span className="title">① 数据包</span>
            <span className="axes">.zip / .s1p / .s2p / .snp · 支持拖拽多选</span>
          </div>
          <div style={{ padding: 14 }}>
            {files.length > 0 ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {files.map((f, idx) => (
                  <div
                    key={`${f.name}-${idx}`}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12,
                      padding: 10,
                      border: '1px solid var(--border)',
                      background: 'var(--bg-panel-2)',
                      borderRadius: 4,
                    }}
                  >
                    <div
                      style={{
                        width: 36,
                        height: 36,
                        display: 'grid',
                        placeItems: 'center',
                        background: 'var(--primary-soft)',
                        borderRadius: 4,
                        color: 'var(--primary)',
                      }}
                    >
                      <I.zip size={18} />
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-1)' }}>{f.name}</div>
                      <div className="mono dim" style={{ fontSize: 10.5 }}>
                        {(f.size / 1024 / 1024).toFixed(1)} MB
                      </div>
                    </div>
                    <button
                      className="btn sm danger"
                      onClick={() => setFiles((prev) => prev.filter((_, i) => i !== idx))}
                      disabled={submitting || taskInfo}
                    >
                      <I.trash size={12} />
                    </button>
                  </div>
                ))}
                <label
                  style={{
                    display: 'block',
                    border: '2px dashed var(--border-strong)',
                    borderRadius: 4,
                    padding: '16px',
                    textAlign: 'center',
                    background: 'var(--bg-panel-2)',
                    cursor: 'pointer',
                    fontSize: 12,
                    color: 'var(--fg-3)',
                  }}
                >
                  <input
                    ref={inputRef}
                    type="file"
                    accept=".zip,.s1p,.s2p,.snp"
                    multiple
                    style={{ display: 'none' }}
                    onChange={(e) => onPickFiles(e.target.files)}
                  />
                  + 继续添加文件
                </label>
              </div>
            ) : (
              <label
                onDrop={onDrop}
                onDragOver={onDragOver}
                onDragLeave={onDragLeave}
                style={{
                  display: 'block',
                  border: `2px dashed ${dragOver ? 'var(--primary)' : 'var(--border-strong)'}`,
                  borderRadius: 4,
                  padding: '32px 16px',
                  textAlign: 'center',
                  background: dragOver ? 'var(--primary-soft)' : 'var(--bg-panel-2)',
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                <input
                  ref={inputRef}
                  type="file"
                  accept=".zip,.s1p,.s2p,.snp"
                  multiple
                  style={{ display: 'none' }}
                  onChange={(e) => onPickFiles(e.target.files)}
                />
                <I.upload size={28} stroke={dragOver ? 'var(--primary)' : 'var(--fg-4)'} />
                <div style={{ fontSize: 13, color: dragOver ? 'var(--primary)' : 'var(--fg-2)', margin: '8px 0 4px' }}>
                  {dragOver ? '释放以上传' : '点击或拖拽文件到此处'}
                </div>
                <div className="dim mono" style={{ fontSize: 10.5 }}>
                  支持 .zip / .s1p / .s2p / .snp · 可多选
                </div>
              </label>
            )}
          </div>
        </div>

        <div className="chart-card" style={{ margin: 0 }}>
          <div className="chart-head">
            <span className="title">② 处理选项</span>
          </div>
          <div style={{ padding: 14 }}>
            <div className="field">
              <div className="field-label">
                <span>对照表 mapping</span>
                <span className="hint">必填</span>
              </div>
              <select
                className="select"
                style={{ width: '100%' }}
                value={mappingId}
                onChange={(e) => setMappingId(e.target.value)}
                disabled={submitting || taskInfo}
              >
                {mappings.length === 0 && <option value="">（无对照表，请先上传）</option>}
                {mappings.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}（{m.entry_count} 条）
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <div className="field-label">
                <span>频率范围 (GHz)</span>
                <span className="hint">留空 = 全频段</span>
              </div>
              <div className="row-flex">
                <input
                  className="input mono"
                  placeholder="14.0"
                  value={fStart}
                  onChange={(e) => setFStart(e.target.value)}
                  style={{ flex: 1 }}
                  disabled={submitting || taskInfo}
                />
                <span className="dim">—</span>
                <input
                  className="input mono"
                  placeholder="16.0"
                  value={fEnd}
                  onChange={(e) => setFEnd(e.target.value)}
                  style={{ flex: 1 }}
                  disabled={submitting || taskInfo}
                />
              </div>
            </div>
            <div className="field">
              <label
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  cursor: submitting || taskInfo ? 'default' : 'pointer',
                  opacity: submitting || taskInfo ? 0.6 : 1,
                }}
              >
                <input
                  type="checkbox"
                  data-testid="deembed-toggle"
                  checked={deembed}
                  onChange={(e) => setDeembed(e.target.checked)}
                  disabled={submitting || !!taskInfo}
                  style={{ marginTop: 2 }}
                />
                <span>
                  <div style={{ fontSize: 13, color: 'var(--fg-1)' }}>
                    去嵌（开路/短路校准）
                  </div>
                  <div className="dim" style={{ fontSize: 11, marginTop: 2 }}>
                    需 zip 内含开路/短路校准 .s2p。开启后处理速度变慢；缺校准件会任务失败。
                  </div>
                  {deembed && (
                    <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <div style={{ fontSize: 11, color: 'var(--fg-2)' }}>去嵌方法：</div>
                      <select
                        value={deembedMethod}
                        onChange={(e) => setDeembedMethod(e.target.value)}
                        disabled={submitting || !!taskInfo}
                        style={{ fontSize: 12, padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg-1)', color: 'var(--fg-1)' }}
                      >
                        <option value="default">平台默认（同目录第一组校准件）</option>
                        <option value="original">原始（de.py：按编号+位置匹配）</option>
                        <option value="gsg100">GSG100（de_GSG100_ELB003.py：前缀精确匹配）</option>
                        <option value="vz">VZ（de_ELB003_VZ.py：支持 V-Z 器件）</option>
                        <option value="basic">基础（de_ELB003_Basic.py：WO/WS 识别）</option>
                      </select>
                    </div>
                  )}
                </span>
              </label>
            </div>
          </div>
        </div>

        <div style={{ gridColumn: '1 / -1', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          {submitError && (
            <div style={{ flex: 1, color: 'var(--fail)', fontSize: 12, alignSelf: 'center' }}>
              <I.alert size={12} /> {submitError}
            </div>
          )}
          {taskInfo ? (
            <button className="btn" onClick={reset}>
              重新开始
            </button>
          ) : (
            <button className="btn primary" onClick={submit} disabled={submitting || files.length === 0 || !mappingId}>
              <I.play size={11} /> {submitting ? `上传中 ${uploadPct}%` : '启动入库'}
            </button>
          )}
        </div>

        {taskInfo && (
          <div style={{ gridColumn: '1 / -1' }} className="chart-card">
            <div className="chart-head">
              <span className="title">④ 处理中 · {taskInfo.batch_no}</span>
              <span className="axes">任务 ID：{taskInfo.task_id}</span>
              <div className="right">
                <span
                  className={`badge ${
                    sse.status === 'success' ? 'done' : sse.status === 'error' ? 'err' : 'run'
                  }`}
                >
                  {{
                    pending: '排队中',
                    running: '运行中',
                    success: '成功',
                    error: '失败',
                  }[sse.status || 'pending']}
                </span>
                <Link to={`/tasks/${taskInfo.task_id}`} className="btn ghost sm" style={{ textDecoration: 'none' }}>
                  详情 ›
                </Link>
              </div>
            </div>
            <div style={{ padding: 14 }}>
              <ProgressBar label="总进度" pct={sse.progress} status={sse.status} />
              {sse.stage && sse.stage !== 'done' && sse.stage !== 'failed' && (
                <ProgressBar
                  label={sse.stage === 'metrics' ? '指标计算' : '解压'}
                  pct={sse.stageProgress}
                  status={sse.status}
                  compact
                />
              )}
              <div className="mono dim" style={{ fontSize: 12, marginTop: 8 }}>
                {sse.message || '等待 worker...'}
              </div>
              {sse.error && (
                <div
                  style={{
                    marginTop: 10,
                    padding: 10,
                    background: 'var(--fail-soft)',
                    border: '1px solid var(--fail)',
                    borderRadius: 4,
                    color: 'var(--fail)',
                  }}
                >
                  <I.alert size={12} /> {sse.error}
                </div>
              )}
              {sse.done && sse.status === 'success' && (
                <div style={{ marginTop: 10, color: 'var(--pass)', fontSize: 12 }}>
                  <I.check size={12} /> 完成，即将跳转到批次详情...
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </>
  );
}
