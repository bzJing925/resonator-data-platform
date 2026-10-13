import { loadStaticComparison, inspectStaticConditions } from '../../src/api/static/comparison';
import { queryDevices } from '../../src/api/static';
import { getConn } from '../../src/api/static/duckdb';
import { IS_STATIC } from '../../src/config';
Object.assign(window, { comparisonDataHarness: { loadStaticComparison, inspectStaticConditions, queryDevices, getConn, isStatic: IS_STATIC } });
