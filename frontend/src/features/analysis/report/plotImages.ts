import Plotly from 'plotly.js/dist/plotly';
import type { ReportFigure } from './reportModel';
export interface ReportImage { title: string; dataUrl: string; bytes: number }
export interface PlotImageRenderer {
  newPlot: (element: HTMLElement, data: any[], layout: any, config: any) => Promise<unknown>;
  toImage: (element: HTMLElement, options: { format: 'png'; width: number; height: number; scale: number }) => Promise<string>;
  purge: (element: HTMLElement) => void;
}
export async function renderReportImages(figures: ReportFigure[], renderer: PlotImageRenderer = Plotly): Promise<ReportImage[]> {
  if (figures.length > 20) throw new Error('报告超过20图');
  let bytes = 0;
  const images: ReportImage[] = [];
  for (const figure of figures) {
    if (figure.scene.placeholder) throw new Error('报告图规格无效');
    const element = document.createElement('div');
    element.dataset.reportPlot = 'true';
    Object.assign(element.style, { position: 'fixed', left: '-20000px', top: '0', width: '1600px', height: '1000px' });
    document.body.appendChild(element);
    try {
      await renderer.newPlot(element, structuredClone(figure.scene.allTraces), { ...structuredClone(figure.scene.layout), width: 1600, height: 1000, autosize: false }, { staticPlot: true, responsive: false });
      const dataUrl = await renderer.toImage(element, { format: 'png', width: 1600, height: 1000, scale: 1 });
      if (!/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(dataUrl)) throw new Error('PNG导出返回无效图片');
      const encoded = dataUrl.split(',')[1], size = atob(encoded).length;
      bytes += size;
      if (bytes > 100 * 1024 * 1024) throw new Error('报告PNG累计超过100MiB；导出已取消');
      images.push({ title: figure.title, dataUrl, bytes: size });
    } finally {
      try { renderer.purge(element); } finally { element.remove(); }
    }
  }
  return images;
}
