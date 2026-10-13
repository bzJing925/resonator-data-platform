import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import UploadGroup from '../upload/UploadGroup';
import I from '../components/Icons';
import { listMappings, pullWebData } from '../api/endpoints';
import { usePageState } from '../contexts/PageStateContext';
import type { Mapping } from '../types';

const UPLOAD_INITIAL_STATE = {
  mappingId: '',
  fStart: '',
  fEnd: '',
  deembed: false,
  deembedMethod: 'default',
};

const RAW_FILE_EXTS = ['.zip', '.s1p', '.s2p', '.snp'];
const TABLE_FILE_EXTS = ['.xlsx', '.xls', '.csv'];

export default function Upload() {
  const [state, setState] = usePageState('upload', UPLOAD_INITIAL_STATE);
  const {
    mappingId,
    fStart,
    fEnd,
    deembed,
    deembedMethod,
  } = state;

  const setMappingId = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, mappingId: typeof v === 'function' ? v(s.mappingId) : v })), [setState]);
  const setFStart = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, fStart: typeof v === 'function' ? v(s.fStart) : v })), [setState]);
  const setFEnd = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, fEnd: typeof v === 'function' ? v(s.fEnd) : v })), [setState]);
  const setDeembed = useCallback((v: boolean | ((prev: boolean) => boolean)) => setState((s) => ({ ...s, deembed: typeof v === 'function' ? v(s.deembed) : v })), [setState]);
  const setDeembedMethod = useCallback((v: string | ((prev: string) => string)) => setState((s) => ({ ...s, deembedMethod: typeof v === 'function' ? v(s.deembedMethod) : v })), [setState]);

  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [targetBatch, setTargetBatch] = useState('');
  const [uploadKind, setUploadKind] = useState<'raw' | 'table'>('raw');
  const [dragOver, setDragOver] = useState<boolean>(false);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [webSyncMessage, setWebSyncMessage] = useState<string | null>(null);
  const [pullingWeb, setPullingWeb] = useState<boolean>(false);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const refreshMappings = useCallback(() => {
    listMappings()
      .then((data) => {
        const list = Array.isArray(data) ? data : (data as { items?: Mapping[] })?.items || [];
        setMappings(list);
        if (list.length && !mappingId) setMappingId(String(list[0].id));
      })
      .catch(() => setMappings([]));
  }, [mappingId, setMappingId]);

  useEffect(() => {
    refreshMappings();
  }, [refreshMappings]);


  const validExts = uploadKind === 'raw' ? RAW_FILE_EXTS : TABLE_FILE_EXTS;

  const validateFiles = (list: File[]) => {
    const bad = list.filter((f) => !validExts.some((ext) => f.name.toLowerCase().endsWith(ext)));
    if (bad.length) {
      setSubmitError(`仅支持 ${validExts.join(' / ')} 文件，以下文件被忽略：${bad.map((f) => f.name).join(', ')}`);
      return list.filter((f) => validExts.some((ext) => f.name.toLowerCase().endsWith(ext)));
    }
    const empty = list.filter((f) => f.size === 0);
    if (empty.length) {
      setSubmitError(`以下文件为空（0 字节），已忽略：${empty.map((f) => f.name).join(', ')}`);
      return list.filter((f) => f.size > 0);
    }
    setSubmitError(null);
    return list;
  };

  const onPickFiles = (fileList: FileList | null) => {
    if (!fileList || fileList.length === 0) return;
    const picked = validateFiles(Array.from(fileList));
    if (picked.length) setFiles((prev) => uploadKind === 'table' ? [picked[0]] : [...prev, ...picked]);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    onPickFiles(e.dataTransfer.files);
  };

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const reset = () => {
    setFiles([]);
    setSubmitError(null);
    setDragOver(false);
    setWebSyncMessage(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  const pullFromWeb = async () => {
    setPullingWeb(true);
    setWebSyncMessage('正在下载网页版已发布的数据和对照表…');
    try {
      const result = await pullWebData();
      setWebSyncMessage(`已同步：新增 ${result.batches_added} 个批次、${result.devices_added} 个数据点、${result.mappings_added} 份对照表。`);
      refreshMappings();
    } catch (error: any) {
      setWebSyncMessage(`从网页版同步失败：${error?.response?.data?.detail || error.message}`);
    } finally {
      setPullingWeb(false);
    }
  };

  const changeUploadKind = (kind: 'raw' | 'table') => {
    if (kind === uploadKind) return;
    setUploadKind(kind);
    setFiles([]);
    setSubmitError(null);
    setDeembed(false);
    if (inputRef.current) inputRef.current.value = '';
  };

  return (
    <>
      <div className="toolbar">
        <span className="crumb">
          <b>上传新批次</b>
        </span>
        <div className="spacer" />
        {mappings.length === 0 && (
          <Link to="/mappings" className="btn">
            <I.table size={13} /> 先去添加对照表
          </Link>
        )}
        <button className="btn" onClick={pullFromWeb} disabled={pullingWeb || submitting}>
          <I.download size={13} /> {pullingWeb ? '同步中…' : '从网页版同步'}
        </button>
      </div>

      <div
        style={{
          flex: 1,
          overflow: 'auto',
          padding: 18,
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gridAutoRows: 'max-content',
          gap: 14,
          alignContent: 'start',
          maxWidth: 1280,
          margin: '0 auto',
        }}
      >
        <div className="chart-card" style={{ margin: 0 }}>
          <div className="chart-head">
            <span className="title">① 数据来源</span>
            <div className="right" style={{ display: 'flex', gap: 6 }}>
              <button className={`btn sm${uploadKind === 'raw' ? ' active' : ''}`} onClick={() => changeUploadKind('raw')}>原始测量数据</button>
              <button className={`btn sm${uploadKind === 'table' ? ' active' : ''}`} onClick={() => changeUploadKind('table')}>参数表格</button>
            </div>
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
                      disabled={submitting}
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
                    accept={validExts.join(',')}
                    multiple={uploadKind === 'raw'}
                    style={{ display: 'none' }}
                    onChange={(e) => onPickFiles(e.target.files)}
                  />
                  {uploadKind === 'raw' ? '+ 继续添加文件' : '更换表格文件'}
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
                  accept={validExts.join(',')}
                  multiple={uploadKind === 'raw'}
                  style={{ display: 'none' }}
                  onChange={(e) => onPickFiles(e.target.files)}
                />
                <I.upload size={28} stroke={dragOver ? 'var(--primary)' : 'var(--fg-4)'} />
                <div style={{ fontSize: 13, color: dragOver ? 'var(--primary)' : 'var(--fg-2)', margin: '8px 0 4px' }}>
                  {dragOver ? '释放以上传' : '点击或拖拽文件到此处'}
                </div>
                <div className="dim mono" style={{ fontSize: 10.5 }}>
                  {uploadKind === 'raw'
                    ? '支持 .zip / .s1p / .s2p / .snp · 可多选'
                    : '支持 .xlsx / .xls / .csv · 已提取参数表格，单文件导入'}
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
                disabled={submitting}
              >
                {mappings.length === 0 && <option value="">（无对照表，请先上传）</option>}
                {mappings.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}（{m.entry_count} 条）
                  </option>
                ))}
              </select>
            </div>
            {uploadKind === 'table' && <div className="field">
              <label>关联服务器原始批次（可选）</label>
              <input className="input" value={targetBatch}
                onChange={(e) => setTargetBatch(e.target.value)}
                placeholder="填写服务器批次名；留空则作为独立指标批次"
                disabled={submitting} />
              <small>关联后按原始文件名、目录和端口更新指标，保留服务器原始文件与曲线。</small>
            </div>}
            <Link to="/settings?target=web">网页发布请到设置中人工确认</Link>
            <div className="field">
              <div className="field-label">
                <span>频率范围 (GHz)</span>
                <span className="hint">留空自动使用文件频段</span>
              </div>
              <div className="row-flex">
                <input
                  className="input mono"
                  placeholder="自动"
                  value={fStart}
                  onChange={(e) => setFStart(e.target.value)}
                  style={{ flex: 1 }}
                  disabled={submitting}
                />
                <span className="dim">—</span>
                <input
                  className="input mono"
                  placeholder="自动"
                  value={fEnd}
                  onChange={(e) => setFEnd(e.target.value)}
                  style={{ flex: 1 }}
                  disabled={submitting}
                />
              </div>
            </div>
            <div className="field">
              <label
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  cursor: submitting ? 'default' : 'pointer',
                  opacity: submitting ? 0.6 : 1,
                }}
              >
                <input
                  type="checkbox"
                  data-testid="deembed-toggle"
                  checked={deembed}
                  onChange={(e) => setDeembed(e.target.checked)}
                  disabled={submitting}
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
                      <select className="select"
                        value={deembedMethod}
                        onChange={(e) => setDeembedMethod(e.target.value)}
                        disabled={submitting}
                        style={{ fontSize: 12, padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg-panel)', color: 'var(--fg-1)' }}
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
          <UploadGroup files={files} options={{ mapping_id: Number(mappingId), f_start_ghz: fStart.trim() ? Number(fStart) : null, f_end_ghz: fEnd.trim() ? Number(fEnd) : null, process_type: 'AUTO', deembed, deembed_method: deembedMethod, target_batch: uploadKind === 'table' ? targetBatch.trim() : '' }} onBusy={setSubmitting} />
        </div>


      </div>
    </>
  );
}
