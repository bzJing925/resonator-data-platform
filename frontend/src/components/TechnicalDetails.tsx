import React from 'react';

/** Keep audit evidence available without filling routine workflows with raw JSON. */
export default function TechnicalDetails({ value, label = '查看详情' }: { value: unknown; label?: string }) {
  return <details className="technical-details"><summary>{label}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}
