import type { FrozenReportInput } from './reportModel';
import type { ReportImage } from './plotImages';
import { describeModel, modelDescriptionText } from './modelDescription';
export function escapeHtml(value: unknown) {
  return String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]!));
}
export function reportHtml(input: FrozenReportInput, images: ReportImage[], title = input.project.head.name, metadata: unknown = null) {
  if (images.length !== input.figures.length || images.length > 20) throw new Error('报告图片不完整');
  let bytes = 0;
  const figures = images.map((image) => {
    if (!/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(image.dataUrl)) throw new Error('报告只接受生成的PNG');
    bytes += atob(image.dataUrl.split(',')[1]).length;
    if (bytes > 100 * 1024 * 1024) throw new Error('报告PNG累计超过100MiB');
    return `<figure><figcaption>${escapeHtml(image.title)}</figcaption><img alt="${escapeHtml(image.title)}" src="${image.dataUrl}" width="1600" height="1000"></figure>`;
  }).join('');
  const json = (value: unknown) => escapeHtml(JSON.stringify(value, null, 2));
  return `<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; connect-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(title)}</title><style>
  @page{size:A4 landscape;margin:10mm}*{box-sizing:border-box}body{font:14px system-ui,sans-serif;color:#172033;margin:24px}h1{font-size:24px}pre,p{white-space:pre-wrap;overflow-wrap:anywhere}pre{font-size:11px;background:#f3f5f7;padding:12px}figure{margin:18px 0;break-inside:avoid}figcaption{font-weight:600;margin-bottom:6px}img{display:block;width:100%;height:auto;max-width:1600px}.scope{border:2px solid #a85400;padding:12px}@media print{body{margin:0;font-size:10px}figure{break-before:page;margin:0}img{max-height:170mm;max-width:277mm;object-fit:contain}pre{background:none;font-size:8px}h1{font-size:18px}}
  </style></head><body><h1>${escapeHtml(title)}</h1><p>生成时间：${escapeHtml(input.generatedAt)}\n项目 ${escapeHtml(input.project.head.projectId)} · complete版本 ${escapeHtml(input.project.version.versionId)} · generation ${input.project.head.generation}</p>
  ${input.partial ? '<p class="scope">当前工作区部分数据；请核对下列来源验证、截断、采样及本地修改/排除范围。</p>' : '<p>已加载完整且已验证的数据范围。</p>'}<h2>备注</h2><p>${escapeHtml(input.notes)}</p><h2>数据来源、版本、计数和统计</h2><pre>${json(input.summaries)}</pre><h2>QuerySpec / 单位 / 质量规则 / 比较口径</h2><pre>${json(input.project.version.spec)}</pre>${input.comparison ? `<h2>比较统计（原始pf与自定义规则分开）</h2><pre>${json(input.comparison)}</pre>` : ''}<h2>模型说明</h2><p>${escapeHtml(modelDescriptionText(describeModel(metadata)))}</p><pre>${json(describeModel(metadata))}</pre><p>原始曲线未包含；会话撤销历史未包含。使用浏览器打印，纸张A4横向。</p>${figures}</body></html>`;
}
