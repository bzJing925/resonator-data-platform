import React from 'react';

interface IconProps {
  d?: string | React.ReactNode;
  size?: number;
  fill?: string;
  stroke?: string;
  sw?: number;
  vb?: number;
  style?: React.CSSProperties;
}

interface IconDict {
  [key: string]: (p: IconProps) => React.ReactElement;
}

const Icon: React.FC<IconProps> = ({ d, size = 16, fill, stroke = 'currentColor', sw = 1.6, vb = 24, style }) => (
  <svg
    width={size}
    height={size}
    viewBox={`0 0 ${vb} ${vb}`}
    fill={fill || 'none'}
    stroke={stroke}
    strokeWidth={sw}
    strokeLinecap="round"
    strokeLinejoin="round"
    style={style}
    aria-hidden
  >
    {typeof d === 'string' ? <path d={d} /> : d}
  </svg>
);

const I: IconDict = {
  scatter: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <circle cx="6" cy="18" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="9" cy="13" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="13" cy="15" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="16" cy="9" r="1.4" fill="currentColor" stroke="none" />
          <circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none" />
          <path d="M3 21h18M3 21V3" />
        </g>
      }
    />
  ),
  box: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <path d="M3 21h18M3 21V3" />
          <rect x="6" y="9" width="3" height="9" />
          <rect x="11" y="6" width="3" height="12" />
          <rect x="16" y="11" width="3" height="7" />
          <line x1="7.5" y1="6" x2="7.5" y2="9" />
          <line x1="12.5" y1="3" x2="12.5" y2="6" />
          <line x1="17.5" y1="8" x2="17.5" y2="11" />
        </g>
      }
    />
  ),
  line: (p) => <Icon {...p} d="M3 21h18M3 21V3M5 17l4-5 4 3 5-9 3 4" />,
  wafer: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6L5.6 18.4" strokeOpacity="0.4" />
        </g>
      }
    />
  ),
  curve: (p) => <Icon {...p} d="M3 17c2 0 3-9 6-9s4 11 7 11 3-9 5-9" />,
  upload: (p) => (
    <Icon {...p} d="M12 16V4m0 0l-4 4m4-4l4 4M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" />
  ),
  batches: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <rect x="3" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="14" width="7" height="7" rx="1" />
        </g>
      }
    />
  ),
  table: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <rect x="3" y="4" width="18" height="16" rx="1" />
          <path d="M3 9h18M3 14h18M9 4v16M15 4v16" />
        </g>
      }
    />
  ),
  dashboard: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <rect x="3" y="3" width="7" height="9" rx="1" />
          <rect x="3" y="14" width="7" height="7" rx="1" />
          <rect x="14" y="3" width="7" height="7" rx="1" />
          <rect x="14" y="12" width="7" height="9" rx="1" />
        </g>
      }
    />
  ),
  filter: (p) => <Icon {...p} d="M4 5h16l-6 8v6l-4-2v-4z" />,
  download: (p) => <Icon {...p} d="M12 4v12m0 0l-4-4m4 4l4-4M5 20h14" />,
  settings: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" />
        </g>
      }
    />
  ),
  search: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <circle cx="11" cy="11" r="7" />
          <path d="M21 21l-4.3-4.3" />
        </g>
      }
    />
  ),
  plus: (p) => <Icon {...p} d="M12 5v14M5 12h14" />,
  x: (p) => <Icon {...p} d="M6 6l12 12M6 18L18 6" />,
  chevron: (p) => <Icon {...p} d="M9 6l6 6-6 6" />,
  expand: (p) => <Icon {...p} d="M4 14v6h6M20 10V4h-6M4 20l7-7M20 4l-7 7" />,
  refresh: (p) => <Icon {...p} d="M21 12a9 9 0 11-3-6.7L21 8M21 3v5h-5" />,
  play: (p) => <Icon {...p} d="M7 4l13 8-13 8z" fill="currentColor" stroke="none" />,
  pause: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <rect x="6" y="4" width="4" height="16" fill="currentColor" stroke="none" />
          <rect x="14" y="4" width="4" height="16" fill="currentColor" stroke="none" />
        </g>
      }
    />
  ),
  trash: (p) => <Icon {...p} d="M4 7h16M9 7V4h6v3M6 7l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13M10 11v6M14 11v6" />,
  doc: (p) => <Icon {...p} d="M14 3H6a1 1 0 00-1 1v16a1 1 0 001 1h12a1 1 0 001-1V8z M14 3v5h5" />,
  more: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none" />
          <circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none" />
        </g>
      }
    />
  ),
  zip: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <path d="M14 3H6a1 1 0 00-1 1v16a1 1 0 001 1h12a1 1 0 001-1V8z M14 3v5h5" />
          <path d="M11 12v2M11 16v2" />
        </g>
      }
    />
  ),
  check: (p) => <Icon {...p} d="M5 13l4 4L19 7" />,
  alert: (p) => (
    <Icon
      {...p}
      d="M12 9v4m0 4h.01M10.3 3.86l-8.5 14.4A1.5 1.5 0 003.1 20.5h17.8a1.5 1.5 0 001.3-2.24l-8.5-14.4a1.5 1.5 0 00-2.6 0z"
    />
  ),
  cpu: (p) => (
    <Icon
      {...p}
      d={
        <g>
          <rect x="6" y="6" width="12" height="12" rx="1" />
          <rect x="9" y="9" width="6" height="6" />
          <path d="M3 10h2M3 14h2M19 10h2M19 14h2M10 3v2M14 3v2M10 19v2M14 19v2" />
        </g>
      }
    />
  ),
  layers: (p) => <Icon {...p} d="M12 2l9 5-9 5-9-5zM3 12l9 5 9-5M3 17l9 5 9-5" />,
  spinner: (p) => (
    <Icon
      {...p}
      d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"
      sw={2}
    />
  ),
};

export default I;
export type { IconProps };
