import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || '';

// ── 连接状态（供 Header 指示器）──
let _online = true;
const listeners = new Set();
export const onConnectionChange = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };
const notifyConnection = (online) => {
  if (_online !== online) { _online = online; listeners.forEach(fn => fn(online)); }
};
export const isApiOnline = () => _online;

// API Key 由 Nginx 反向代理在服务端注入 X-API-Key 请求头
// 不要在客户端 .env 中嵌入 API Key，会被打入 JS bundle 导致泄露
const API_KEY = process.env.REACT_APP_API_KEY || '';

const api = axios.create({
  baseURL: `${API_BASE_URL}/api`,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
    'Accept-Encoding': 'gzip, deflate',
  }
});

// ── 429 自动重试（指数退避，最多 3 次）──
const MAX_RETRIES = 3;
const RETRY_DELAY_BASE = 1000; // 1s

api.interceptors.response.use(
  (response) => {
    notifyConnection(true);
    const res = response.data;
    if (res && typeof res === 'object' && 'code' in res) {
      if (res.code !== 0 && res.code !== 200) {
        return Promise.reject(new Error(res.message || '请求失败'));
      }
      if (Array.isArray(res.data)) {
        const arr = res.data;
        if (res.total !== undefined) arr.total = res.total;
        if (res.page !== undefined) arr.page = res.page;
        if (res.count !== undefined) arr.count = res.count;
      }
      return res.data;
    }
    return res;
  },
  async (error) => {
    // AbortController 取消 → 直接透传
    if (error.code === 'ERR_CANCELED' || error.name === 'CanceledError') {
      return Promise.reject(error);
    }

    // 429 自动重试（指数退避，仅 GET 请求，避免写操作重复提交）
    const config = error.config;
    if (error.response?.status === 429 && config?.method === 'get') {
      config.__retryCount = (config.__retryCount || 0) + 1;
      if (config.__retryCount <= MAX_RETRIES) {
        const delay = RETRY_DELAY_BASE * Math.pow(2, config.__retryCount - 1);
        await new Promise(r => setTimeout(r, delay));
        return api(config);
      }
    }

    // 网络错误自动重试（GET 请求，非超时）
    if (!error.response && error.code !== 'ECONNABORTED' && config?.method === 'get') {
      config.__netRetryCount = (config.__netRetryCount || 0) + 1;
      if (config.__netRetryCount <= 2) {
        await new Promise(r => setTimeout(r, 2000));
        return api(config);
      }
    }

    let message = error.message;
    if (error.response) {
      const { status, data } = error.response;
      if (status === 429) {
        message = '请求过于频繁，请稍后刷新页面重试';
      } else if (status === 503) {
        message = '服务暂时不可用，请稍后重试';
      } else if (status >= 500) {
        message = '服务器错误，请稍后重试';
      } else {
        message = data?.message || data?.detail || `请求失败 (${status})`;
      }
    } else if (error.code === 'ECONNABORTED') {
      message = '请求超时，请稍后重试';
      // 超时不标记离线，服务器可能只是慢
    } else {
      message = config?.method !== 'get'
        ? '操作失败，请检查网络后重试'
        : '网络连接异常，请检查网络后刷新页面';
      notifyConnection(false);  // 仅真正网络断开时标记离线
    }
    return Promise.reject(new Error(message));
  }
);

export default api;
