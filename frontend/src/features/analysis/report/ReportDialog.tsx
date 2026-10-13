import React, { useState } from 'react';
import { useAnalysisWorkspace } from '../workspace';
import { getSurrogateStatus } from '../../../api/endpoints';
import { freezeReportInput } from './reportModel';
import { renderReportImages } from './plotImages';
import { reportHtml } from './reportHtml';
export default function ReportDialog() {
  const { project, state } = useAnalysisWorkspace();
  const [opened, setOpened] = useState(false), [title, setTitle] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const generate = async () => {
    setBusy(true); setError('');
    try {
      const head = state.readOnly ? project.head : await project.saveNow();
      if (!head) throw new Error('报告需要完整已保存版本');
      const complete = await project.store.load(head.projectId);
      if (complete.head.currentVersionId !== head.currentVersionId) throw new Error('保存版本已变化，请重新生成报告');
      const frozen = freezeReportInput(complete);
      const metadata = await getSurrogateStatus().then((value) => value.metadata).catch(() => null);
      const images = await renderReportImages(frozen.figures);
      const html = reportHtml(frozen, images, title || head.name, metadata);
      const url = URL.createObjectURL(new Blob([html], { type: 'text/html;charset=utf-8' }));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${(title || head.name).replace(/[\\/:*?"<>|]/g, '_')}.html`;
      document.body.appendChild(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) { setError(failure instanceof Error ? failure.message : '报告生成失败，没有下载'); }
    finally { setBusy(false); }
  };
  return <><button onClick={() => setOpened(true)}>生成HTML报告</button>{opened && <div role="dialog" aria-label="HTML报告">
    <label>报告标题<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
    <p>先保存并冻结完整版本；生成后可离线打开，使用浏览器打印为A4横向PDF。原始曲线及会话撤销不包含。</p>
    {error && <p role="alert">{error}</p>}<button disabled={busy || project.loading || project.sessionMode} onClick={generate}>{busy ? '生成中…' : '下载离线HTML'}</button><button disabled={busy} onClick={() => setOpened(false)}>关闭报告</button>
  </div>}</>;
}
