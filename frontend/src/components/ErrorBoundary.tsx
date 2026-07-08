import React from 'react';
import I from './Icons';

interface Props {
  children?: React.ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('ErrorBoundary caught:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--fail)' }}>
          <I.alert size={32} />
          <div style={{ marginTop: 12, fontSize: 14 }}>页面渲染出错</div>
          <div style={{ marginTop: 8, fontSize: 12, color: 'var(--fg-3)', fontFamily: 'monospace' }}>
            {this.state.error?.message || '未知错误'}
          </div>
          <button
            className="btn"
            style={{ marginTop: 16 }}
            onClick={() => this.setState({ hasError: false, error: null })}
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
