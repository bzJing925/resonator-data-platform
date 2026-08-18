/** 静态网页版开关：VITE_STATIC=1 构建时启用（无后端，数据走 DuckDB-WASM/JSON/ONNX）。 */
export const IS_STATIC = import.meta.env.VITE_STATIC === '1';
