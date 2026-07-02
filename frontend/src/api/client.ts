import axios, { AxiosError, AxiosInstance, InternalAxiosRequestConfig } from 'axios';

async function resolveBaseURL(): Promise<string> {
  if (window.electronAPI?.getBackendUrl) {
    const backendUrl = await window.electronAPI.getBackendUrl();
    return `${backendUrl}/api`;
  }
  const isElectronFile = typeof window !== 'undefined' && window.location.protocol === 'file:';
  return isElectronFile ? 'http://127.0.0.1:8000/api' : '/api';
}

const client: AxiosInstance = axios.create({
  timeout: 120000,
});

client.interceptors.request.use(async (config: InternalAxiosRequestConfig) => {
  config.baseURL = await resolveBaseURL();
  return config;
});

client.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const { config } = error;
    if (!config) return Promise.reject(error);

    const retryCount = (config as unknown as Record<string, number>).__retryCount || 0;
    const shouldRetry =
      retryCount < 3 &&
      (error.response?.status === 502 || error.response?.status === 503 || !error.response);

    if (shouldRetry) {
      (config as unknown as Record<string, number>).__retryCount = retryCount + 1;
      const delay = 2 ** retryCount * 1000;
      await new Promise((resolve) => {
        setTimeout(resolve, delay);
      });
      return client(config);
    }

    return Promise.reject(error);
  }
);

export default client;
export { resolveBaseURL };
