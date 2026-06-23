import axios from 'axios';

// Electron 生产环境加载本地 file:// 协议页面，相对 /api 无法直接请求到本地后端，
// 需要回退到绝对地址 http://127.0.0.1:8000/api。
const isElectronFile = typeof window !== 'undefined' && window.location.protocol === 'file:';
const fallbackBaseURL = isElectronFile ? 'http://127.0.0.1:8000/api' : '/api';
const baseURL = import.meta.env.VITE_API_BASE || fallbackBaseURL;

const api = axios.create({
  baseURL,
  timeout: 30000,
});

api.interceptors.response.use(
  (r) => r,
  (e) => {
    const message = e.response?.data?.detail || e.message || 'Network error';
    return Promise.reject({ ...e, message });
  }
);

export default api;
export { baseURL };
