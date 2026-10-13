import { expect, test } from '@playwright/test';
import { launchHarness } from './electron';

async function clickApplicationMenu(electronApp: any, id: string) {
  await electronApp.evaluate(({ BrowserWindow, Menu }, menuId) => {
    const item = Menu.getApplicationMenu()?.getMenuItemById(menuId);
    const browserWindow = BrowserWindow.getAllWindows()[0];
    if (!item || !browserWindow) throw new Error(`Missing application menu item: ${menuId}`);
    item.click(undefined as any, browserWindow, {} as any);
  }, id);
}

test.describe('分析工作区 Electron 端到端验收', () => {
  test('Electron 应用菜单与快捷键调用共享分析命令', async () => {
    const { electronApp, page } = await launchHarness();
    try {
      const graph = page.locator('#chart-unified');
      await expect(page.getByTestId('row-count')).toHaveText('24');
      await graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) => item.meta?.analysisLayer === 'raw-points');
        element.emit('plotly_selected', {
          points: [{ customdata: trace.customdata[0] }],
          event: {},
        });
      });
      await expect(page.getByTestId('selection')).toHaveText('device:1');

      await clickApplicationMenu(electronApp, 'analysis.delete');
      await expect(page.getByTestId('last-command')).toHaveText('deleteSelection:chart');
      await expect(page.getByTestId('row-count')).toHaveText('23');
      await clickApplicationMenu(electronApp, 'analysis.undo');
      await expect(page.getByTestId('row-count')).toHaveText('24');

      await clickApplicationMenu(electronApp, 'analysis.select-all');
      await expect.poll(() => page.getByTestId('selection').evaluate((element) =>
        element.textContent?.split(',').filter(Boolean).length)).toBe(24);
      await clickApplicationMenu(electronApp, 'analysis.copy');
      await expect(page.getByTestId('last-command')).toHaveText('copySelection:chart');
      const clipboard = await electronApp.evaluate(({ clipboard }) => clipboard.readText());
      expect(clipboard).toContain('structureType');
      expect(clipboard.split('\r\n')).toHaveLength(25);
      await clickApplicationMenu(electronApp, 'analysis.export-selection');
      await expect(page.getByTestId('last-command')).toHaveText('exportSelection:chart');

      await clickApplicationMenu(electronApp, 'analysis.autorange');
      await expect(page.getByTestId('last-command')).toHaveText('autorange:chart');
      await expect.poll(() => graph.evaluate((element: any) =>
        element._fullLayout.yaxis.range)).toEqual([795, 1196]);
      await clickApplicationMenu(electronApp, 'analysis.edit-style');
      await expect(page.getByTestId('last-command')).toHaveText('editStyle:chart');

      await page.getByRole('tab', { name: /数据表1/ }).click();
      await clickApplicationMenu(electronApp, 'analysis.select-all');
      await expect(page.getByTestId('last-command')).toHaveText('selectAll:worksheet');
    } finally {
      await electronApp.close();
    }
  });

  test('Electron Delete 不依赖图表或表格局部焦点', async () => {
    const { electronApp, page } = await launchHarness();
    try {
      const graph = page.locator('#chart-unified');
      const before = await graph.evaluate((element: any) => ({
        yMax: element._fullLayout.yaxis.range[1],
        xTicks: element.layout.xaxis3.ticktext,
      }));
      await graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) => item.meta?.analysisLayer === 'raw-points');
        const datum = trace.customdata.find((item: any) => item.rowId === 'device:24');
        element.emit('plotly_selected', { points: [{ customdata: datum }], event: {} });
      });
      await expect(page.getByTestId('selection')).toHaveText('device:24');
      await page.locator('main').click({ position: { x: 8, y: 8 } });
      await page.keyboard.press('Delete');
      await expect(page.getByTestId('row-count')).toHaveText('23');
      await expect(page.getByTestId('selection')).toHaveText('');
      await expect.poll(() => graph.evaluate((element: any) =>
        element._fullLayout.yaxis.range[1])).toBeLessThan(before.yMax);
      expect(await graph.evaluate((element: any) => element.layout.xaxis3.ticktext))
        .toEqual(before.xTicks);

      await page.keyboard.press('Meta+z');
      await expect(page.getByTestId('row-count')).toHaveText('24');
      await expect(page.getByTestId('selection')).toHaveText('device:24');
      await expect.poll(() => graph.evaluate((element: any) =>
        element._fullLayout.yaxis.range[1])).toBeGreaterThanOrEqual(before.yMax);
    } finally {
      await electronApp.close();
    }
  });

  test('输入框保留原生编辑快捷键，不触发分析命令', async () => {
    const { electronApp, page } = await launchHarness();
    try {
      const input = page.getByTestId('editable-probe');
      await input.focus();
      await page.keyboard.press('Meta+a');
      await page.keyboard.press('Backspace');
      await expect(input).toHaveValue('');
      await expect(page.getByTestId('row-count')).toHaveText('24');
      await expect(page.getByTestId('last-command')).toHaveText('');

      await page.keyboard.press('Meta+z');
      await expect(input).toHaveValue('editable text');
      await expect(page.getByTestId('last-command')).toHaveText('');
    } finally {
      await electronApp.close();
    }
  });

  for (const type of ['scatter', 'box', 'violin', 'swarm', 'line']) {
    test(`${type} 提供显式的一对一原始点选择层`, async () => {
      const { electronApp, page } = await launchHarness();
      try {
        await page.getByTestId('chart-type').selectOption(type);
        const rawLayers = await page.locator('#chart-unified').evaluate((element: any) =>
          element.data.filter((trace: any) => trace.meta?.analysisLayer === 'raw-points'));
        expect(rawLayers.length).toBeGreaterThanOrEqual(3);
        const mapping = rawLayers.flatMap((trace) => trace.customdata || []);
        expect(mapping.every((datum) => datum?.rowId && !datum?.__memberRowIds)).toBe(true);
        const renderedLayers = await page.locator('#chart-unified').evaluate((element: any) =>
          (element.calcdata || []).map((points: any[]) => points[0]?.trace?.meta?.analysisLayer));
        expect(renderedLayers.filter((layer) => layer === 'raw-points')).toHaveLength(3);
        if (type === 'box' || type === 'violin') expect(renderedLayers).toContain('distribution');
        if (type === 'line') expect(renderedLayers).toContain('summary');

        await page.locator('#chart-unified').evaluate((element: any) => {
          const raw = element.data.find((trace: any) => trace.meta?.analysisLayer === 'raw-points');
          element.emit('plotly_selected', {
            points: ['device:1', 'device:2'].map((rowId) => ({
              customdata: raw.customdata.find((item: any) => item.rowId === rowId),
            })),
            event: {},
          });
        });
        await expect(page.getByTestId('selection')).toHaveText('device:1,device:2');
        await expect(page.locator('.analysis-grid-row.selected')).toHaveCount(2);
        await expect.poll(() => page.locator('#chart-unified').evaluate((element: any) =>
          element.data.filter((trace: any) => trace.meta?.analysisLayer === 'raw-points')
            .map((trace: any) => trace.selectedpoints?.length ?? null))).toEqual([2, 2, 2]);
        const eventNames = await page.locator('#chart-unified').evaluate((element: any) =>
          Object.keys(element._ev?._events || {}));
        expect(eventNames).toEqual(expect.arrayContaining([
          'plotly_click', 'plotly_deselect', 'plotly_selected',
        ]));
        await page.locator('#chart-unified').evaluate((element: any) =>
          element.emit('plotly_deselect'));
        await expect(page.getByTestId('selection')).toHaveText('');
        await expect.poll(() => page.locator('#chart-unified').evaluate((element: any) =>
          element.data.filter((trace: any) => trace.meta?.analysisLayer === 'raw-points')
            .map((trace: any) => trace.selectedpoints ?? null))).toEqual([null, null, null]);

        const clickPointBox = await page.locator('#chart-unified').evaluate((element: any) => {
          const point = [...element.querySelectorAll('.scatterlayer .point')]
            .find((candidate: any) => candidate.__data__?.data?.rowId === 'device:3') as Element;
          return point?.getBoundingClientRect().toJSON();
        });
        if (!clickPointBox) throw new Error('Raw point device:3 is not rendered');
        await page.mouse.move(0, 0);
        await page.mouse.move(
          clickPointBox.x + clickPointBox.width / 2,
          clickPointBox.y + clickPointBox.height / 2,
          { steps: 10 },
        );
        await expect.poll(() => page.locator('#chart-unified').evaluate((element: any) =>
          element._hoverdata?.[0]?.customdata?.rowId ?? null)).toBe('device:3');
        await page.mouse.click(
          clickPointBox.x + clickPointBox.width / 2,
          clickPointBox.y + clickPointBox.height / 2,
        );
        await expect(page.getByTestId('selection')).toHaveText('device:3');
        await page.locator('#chart-unified').evaluate((element: any) =>
          element.emit('plotly_deselect'));
        await expect(page.getByTestId('selection')).toHaveText('');
        await expect(page.locator('.analysis-grid-row.selected')).toHaveCount(0);
      } finally {
        await electronApp.close();
      }
    });
  }

  test('Worksheet 选择通过共享 Selection State 高亮图表原始点', async () => {
    const { electronApp, page } = await launchHarness();
    try {
      await page.getByTestId('worksheet').scrollIntoViewIfNeeded();
      await page.locator('.analysis-grid-row').first().click();
      await expect(page.getByTestId('selection')).toHaveText('device:1');
      await expect.poll(() => page.locator('#chart-unified').evaluate((element: any) =>
        element.data.filter((trace: any) => trace.meta?.analysisLayer === 'raw-points')
          .every((trace: any) => trace.selectedpoints?.length === 1))).toBe(true);
    } finally {
      await electronApp.close();
    }
  });

  test('数据表 Tab 可新建、复制、重命名、关闭和恢复', async () => {
    const { electronApp, page } = await launchHarness();
    try {
      await expect(page.getByTestId('dataset-count')).toHaveText('1');
      await page.getByRole('tab', { name: /数据表1/ }).click();
      await page.getByRole('button', { name: '重命名 数据表1' }).click();
      const renameInput = page.getByRole('textbox', { name: '重命名 数据表1' });
      await renameInput.fill('测量结果');
      await renameInput.press('Enter');
      await expect(page.getByRole('tab', { name: /测量结果/ })).toBeVisible();

      await page.getByRole('button', { name: '新建空白数据表' }).click();
      await expect(page.getByTestId('dataset-count')).toHaveText('2');
      const activeDatasetTab = page.locator('.analysis-tab-item > [role="tab"].active');
      await activeDatasetTab.click({ button: 'right' });
      await page.getByRole('menuitem', { name: '复制数据表' }).click();
      await expect(page.getByTestId('dataset-count')).toHaveText('3');

      await page.getByRole('button', { name: /关闭 数据表/ }).click();
      await expect(page.getByTestId('dataset-count')).toHaveText('2');
      await page.locator('main').click({ position: { x: 8, y: 8 } });
      await page.keyboard.press('Meta+z');
      await expect(page.getByTestId('dataset-count')).toHaveText('3');

      await page.getByRole('tab', { name: /测量结果/ }).click();
      await page.getByRole('button', { name: '关闭 测量结果' }).click();
      const closeDialog = page.getByRole('dialog', { name: '关闭图表数据源' });
      await expect(closeDialog).toBeVisible();
      const replacement = await closeDialog.locator('option').nth(1).getAttribute('value');
      if (!replacement) throw new Error('A replacement dataset should be available');
      await closeDialog.getByRole('combobox').selectOption(replacement);
      await closeDialog.getByRole('button', { name: '关闭并继续' }).click();
      await expect(page.getByRole('tab', { name: /测量结果/ })).toHaveCount(0);
      await expect(page.getByRole('tab', { name: '图表' })).toHaveCount(1);
      await page.keyboard.press('Meta+z');
      await expect(page.getByRole('tab', { name: /测量结果/ })).toBeVisible();
    } finally {
      await electronApp.close();
    }
  });

  test('Worksheet 表头/内容严格对齐并支持 pointer drag 连续选择', async () => {
    const { electronApp, page } = await launchHarness();
    try {
      await page.getByTestId('worksheet').scrollIntoViewIfNeeded();
      const viewport = page.locator('.analysis-grid-viewport');
      await viewport.evaluate((element) => { element.scrollTop = 0; });
      await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBe(0);
      const geometry = await viewport.evaluate((viewportElement) => {
        const rects = (selector: string) => [...viewportElement.querySelectorAll(selector)].map((cell) => {
          const rect = cell.getBoundingClientRect();
          return { key: (cell as HTMLElement).dataset.columnKey, x: rect.x, width: rect.width };
        });
        return {
          header: rects('.analysis-grid-header .analysis-grid-cell'),
          row: rects('.analysis-grid-row[data-row-index="0"] .analysis-grid-cell'),
        };
      });
      expect(geometry.row).toEqual(geometry.header);
      await viewport.evaluate((element) => { element.scrollLeft = 120; });
      await expect.poll(() => viewport.evaluate((element) => element.scrollLeft)).toBe(120);
      const scrolledGeometry = await viewport.evaluate((viewportElement) => {
        const xs = (selector: string) => [...viewportElement.querySelectorAll(selector)]
          .map((cell) => cell.getBoundingClientRect().x);
        return {
          header: xs('.analysis-grid-header .analysis-grid-cell'),
          row: xs('.analysis-grid-row[data-row-index="0"] .analysis-grid-cell'),
        };
      });
      expect(scrolledGeometry.row).toEqual(scrolledGeometry.header);
      await viewport.evaluate((element) => { element.scrollLeft = 0; });

      const rows = page.locator('.analysis-grid-row');
      const first = await page.locator('.analysis-grid-row[data-row-index="0"]').boundingBox();
      const third = await page.locator('.analysis-grid-row[data-row-index="2"]').boundingBox();
      if (!first || !third) throw new Error('Worksheet rows are not visible');
      await page.mouse.move(first.x + 10, first.y + first.height / 2);
      await page.mouse.down();
      await expect(page.locator('.analysis-grid-row.selected')).toHaveCount(1);
      await page.mouse.move(third.x + 10, third.y + third.height / 2, { steps: 6 });
      await expect(page.locator('.analysis-grid-row.selected')).toHaveCount(3);
      await page.mouse.up();
      await expect(page.getByText('3 行已选中')).toBeVisible();
      await expect(page.getByTestId('selection')).toHaveText('device:1,device:2,device:3');

      await rows.nth(1).click({ modifiers: ['Control'] });
      await expect(page.getByText('2 行已选中')).toBeVisible();
      await viewport.evaluate((element) => { element.scrollTop = 280; });
      await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBe(280);
      // Plotly's asynchronous selection redraw can move the outer scroll surface.
      // Physical pointer coordinates must be read after the row is on screen.
      await page.locator('.analysis-grid-row[data-row-index="11"]').scrollIntoViewIfNeeded();
      const viewportBox = await viewport.boundingBox();
      const visibleRow = await page.locator('.analysis-grid-row[data-row-index="11"]').boundingBox();
      if (!viewportBox || !visibleRow) throw new Error('Worksheet viewport is not visible');
      await page.mouse.move(visibleRow.x + 10, visibleRow.y + visibleRow.height / 2);
      await page.mouse.down();
      await page.mouse.move(visibleRow.x + 10, viewportBox.y + 2, { steps: 8 });
      await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeLessThan(280);
      await page.mouse.up();

      await viewport.focus();
      await page.keyboard.press('Home');
      await expect(page.getByTestId('selection')).toHaveText('device:1');
      await page.keyboard.press('Shift+ArrowDown');
      await expect(page.getByTestId('selection')).toHaveText('device:1,device:2');
      await page.keyboard.press('End');
      await expect(page.getByTestId('selection')).toHaveText('device:24');
      await page.keyboard.press('Meta+a');
      await expect(page.getByText('24 行已选中')).toBeVisible();
      await page.keyboard.press('Delete');
      await expect(page.getByTestId('row-count')).toHaveText('0');
      await page.keyboard.press('Meta+z');
      await expect(page.getByTestId('row-count')).toHaveText('24');
    } finally {
      await electronApp.close();
    }
  });

  test('图表使用 structureType X、areaReality 分面和多 Y 纵排布局', async () => {
    const { electronApp, page } = await launchHarness();
    try {
      const layout = await page.locator('#chart-unified').evaluate((element: any) => element.layout);
      expect(layout.meta?.analysisLayout).toEqual({
        xFields: ['structureType'],
        zField: 'areaReality',
        yMetrics: ['qs', 'qp', 'k2eff_pct'],
      });
      const interactionConfig = await page.locator('#chart-unified').evaluate((element: any) => ({
        dragmode: element.layout.dragmode,
        scrollZoom: element._context.scrollZoom,
        displayModeBar: element._context.displayModeBar,
      }));
      expect(interactionConfig).toEqual({ dragmode: 'select', scrollZoom: true, displayModeBar: false });
      await expect(page.locator('#chart-unified .modebar-btn')).toHaveCount(0);
    } finally {
      await electronApp.close();
    }
  });

  test('选中点可以通过 Origin 式 Inspector 编辑样式', async () => {
    const { electronApp, page } = await launchHarness();
    try {
      const graph = page.locator('#chart-unified');
      await expect(page.getByRole('heading', { name: '数据点样式' })).toBeVisible();
      await expect.poll(() => graph.evaluate((element: any) =>
        Object.keys(element._ev?._events || {}).includes('plotly_selected'))).toBe(true);
      await graph.evaluate((element: any) => {
        (window as any).__phase5Graph = element;
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qs');
        const datum = trace.customdata.find((item: any) => item.rowId === 'device:1');
        element.emit('plotly_selected', { points: [{ customdata: datum, data: trace }], event: {} });
      });
      await expect(page.getByTestId('selection')).toHaveText('device:1');

      await page.getByTestId('point-style-color').evaluate((input: HTMLInputElement) => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
          ?.set?.call(input, '#ff0000');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await page.getByTestId('point-style-size').fill('12');
      await page.getByTestId('point-style-symbol').selectOption('triangle-up');
      await page.getByTestId('point-style-opacity').fill('0.4');
      await page.getByTestId('point-style-outline-color').evaluate((input: HTMLInputElement) => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
          ?.set?.call(input, '#00ffff');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await page.getByTestId('point-style-outline-width').fill('2');

      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qs');
        const index = trace.customdata.findIndex((item: any) => item.rowId === 'device:1');
        return {
          sameGraph: (window as any).__phase5Graph === element,
          color: trace.marker.color[index],
          size: trace.marker.size[index],
          symbol: trace.marker.symbol[index],
          opacity: trace.marker.opacity[index],
          outlineColor: trace.marker.line.color[index],
          outlineWidth: trace.marker.line.width[index],
        };
      })).toEqual({
        sameGraph: true,
        color: '#ff0000',
        size: 12,
        symbol: 'triangle-up',
        opacity: 0.4,
        outlineColor: '#00ffff',
        outlineWidth: 2,
      });

      await page.getByTestId('point-style-scope').selectOption('group');
      await expect(page.getByText('4 个数据点')).toBeVisible();
      await page.getByTestId('point-style-symbol').selectOption('square');
      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qs');
        return trace.customdata.filter((_: any, index: number) =>
          trace.marker.symbol[index] === 'square').map((item: any) => item.rowId);
      })).toEqual(['device:1', 'device:7', 'device:13', 'device:19']);

      await page.getByRole('heading', { name: '数据点样式' }).click();
      await page.keyboard.press('Meta+z');
      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qs');
        const index = trace.customdata.findIndex((item: any) => item.rowId === 'device:7');
        return trace.marker.symbol[index];
      })).toBe('circle');
      await page.keyboard.press('Meta+Shift+z');
      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qs');
        const index = trace.customdata.findIndex((item: any) => item.rowId === 'device:7');
        return trace.marker.symbol[index];
      })).toBe('square');

      await page.getByTestId('point-style-scope').selectOption('metric');
      await page.getByTestId('point-style-metric').selectOption('qp');
      await page.getByTestId('point-style-color').evaluate((input: HTMLInputElement) => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')
          ?.set?.call(input, '#00ff00');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qp');
        const first = trace.customdata.findIndex((item: any) => item.rowId === 'device:1');
        const second = trace.customdata.findIndex((item: any) => item.rowId === 'device:2');
        return [trace.marker.color[first], trace.marker.color[second]];
      })).toEqual(['#ff0000', '#00ff00']);

      await page.getByTestId('chart-type').selectOption('line');
      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qp');
        const index = trace.customdata.findIndex((item: any) => item.rowId === 'device:2');
        return trace.marker.color[index];
      })).toBe('#00ff00');

      await page.getByTestId('toggle-filter').click();
      await page.getByTestId('toggle-fullscreen').click();
      await expect(page.getByTestId('chart')).toHaveAttribute('data-fullscreen', 'true');
      await expect(page.getByTestId('main-nav')).toBeHidden();
      await expect(page.getByRole('tab', { name: '图表' })).toBeVisible();
      await expect(page.getByTestId('filter-state')).toHaveText('filtered');
      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qp');
        const index = trace.customdata.findIndex((item: any) => item.rowId === 'device:2');
        return trace.marker.color[index];
      })).toBe('#00ff00');

      await page.getByTestId('toggle-fullscreen').click();
      await expect(page.getByTestId('fullscreen-state')).toHaveText('windowed');
      await expect(page.getByTestId('main-nav')).toBeVisible();
      await expect(page.getByTestId('filter-state')).toHaveText('filtered');
      await expect(page.getByTestId('chart-type')).toHaveValue('line');
      await expect(page.getByTestId('selection')).toContainText('device:1');
      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qp');
        const index = trace.customdata.findIndex((item: any) => item.rowId === 'device:2');
        return trace.marker.color[index];
      })).toBe('#00ff00');

      await page.getByTestId('toggle-chart').click();
      await expect(graph).toHaveCount(0);
      await page.getByTestId('toggle-chart').click();
      await expect(graph).toBeVisible();
      await expect.poll(() => graph.evaluate((element: any) => {
        const trace = element.data.find((item: any) =>
          item.meta?.analysisLayer === 'raw-points' && item.meta?.metric === 'qp');
        const index = trace.customdata.findIndex((item: any) => item.rowId === 'device:2');
        return trace.marker.color[index];
      })).toBe('#00ff00');
    } finally {
      await electronApp.close();
    }
  });
});
