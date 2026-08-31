import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

// vitest 未开 globals，RTL 自动 cleanup 不生效，这里显式注册
afterEach(cleanup);
