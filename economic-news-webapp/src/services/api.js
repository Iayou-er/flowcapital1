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
  timeout: 25000,
  headers: {
    'Content-Type': 'application/json',
    'Accept-Encoding': 'gzip, deflate',
  }
});

// ── 响应拦截器 ──
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
  (error) => {
    // AbortController 取消 → 直接透传
    if (error.code === 'ERR_CANCELED' || error.name === 'CanceledError') {
      return Promise.reject(error);
    }

    let message = error.message;
    if (error.response) {
      const { status, data } = error.response;
      message = data?.message || data?.detail || `请求失败 (${status})`;
    } else if (error.code === 'ECONNABORTED') {
      message = '请求超时，请检查网络连接';
    } else {
      message = '无法连接服务器，请确认后端已启动';
    }
    notifyConnection(false);
    return Promise.reject(new Error(message));
  }
);

export default api;
