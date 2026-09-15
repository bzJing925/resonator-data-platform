import React from 'react';
import { render, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { UnifiedChartGrid, WaferMap } from './Charts';

// jsdom has no complete SVG/canvas layout engine. Keep the narrow effect tests
// mocked; real rendering and selection are covered by e2e/analysis/plotly.spec.ts.
const plotState = vi.hoisted(() => ({ props: null as any }));
vi.mock('react-plotly.js/factory', () => ({
  default: () => (props: any) => {
    plotState.props = props;
    return null;
  },
}));
vi.mock('plotly.js/dist/plotly', () => ({ default: {} }));

const X_NUM = { name: 'area_um2', label: '面积', isCategorical: false };
const Y_QS = { name: 'qs', label: 'Qs', isCategorical: false };
const Z_CAT = { name: 'fl', label: 'FL', isCategorical: true };
const XS = [X_NUM];
const YS = [Y_QS];

const makeRows = () => Array.from({ length: 26 }, (_, index) => ({
  area_um2: index,
  qs: index,
  fl: index,
}));

function grid(rows: ReturnType<typeof makeRows>, onWarningsChange: (warnings: any[]) => void) {
  return (
    <UnifiedChartGrid
      chartType="scatter"
      rows={rows}
      xFields={XS}
      yFields={YS}
      zField={Z_CAT}
      facetZ
      onWarningsChange={onWarningsChange}
    />
  );
}

describe('UnifiedChartGrid 警告 emit（mock 仅限 effect 边界）', () => {
  it('uses the header-selected interaction mode and hides the scrolling Plotly modebar', () => {
    render(
      <UnifiedChartGrid
        chartType="scatter"
        rows={[{ rowId: 'r1', area_um2: 1, qs: 2 }]}
        xFields={XS}
        yFields={YS}
        analysisLayout
        interactionMode="pan"
      />,
    );

    expect(plotState.props.layout.dragmode).toBe('pan');
    expect(plotState.props.config.displayModeBar).toBe(false);
  });

  it('applies the same fixed-header controls to the wafer chart', () => {
    render(
      <WaferMap
        rows={[{ rowId: 'r1', x: 1, y: 2, qs: 3 }]}
        valueField="qs"
        valueLabel="Qs"
        facetField={null}
        facets={[]}
        onPointClick={undefined}
        onSelection={undefined}
        selectedRowIds={undefined}
        revision="wafer-test"
        interactionMode="lasso"
      />,
    );

    expect(plotState.props.layout.dragmode).toBe('lasso');
    expect(plotState.props.config.displayModeBar).toBe(false);
  });

  it('分面超限时 emit 完整警告数组（含 z_facets_clamped）', async () => {
    const onWarningsChange = vi.fn();
    render(grid(makeRows(), onWarningsChange));
    await waitFor(() => expect(onWarningsChange).toHaveBeenCalled());
    const warnings = onWarningsChange.mock.calls.at(-1)?.[0];
    expect(warnings.some((warning) => warning.kind === 'z_facets_clamped')).toBe(true);
  });

  it('rows 同内容新引用时重新 emit', async () => {
    const onWarningsChange = vi.fn();
    const { rerender } = render(grid(makeRows(), onWarningsChange));
    await waitFor(() => expect(onWarningsChange).toHaveBeenCalled());
    onWarningsChange.mockClear();
    rerender(grid(makeRows(), onWarningsChange));
    await waitFor(() => expect(onWarningsChange).toHaveBeenCalled());
  });

  it('无警告时 emit 空数组', async () => {
    const onWarningsChange = vi.fn();
    render(grid([{ area_um2: 1, qs: 2, fl: 0 }], onWarningsChange));
    await waitFor(() => expect(onWarningsChange).toHaveBeenCalledWith([]));
  });
});
