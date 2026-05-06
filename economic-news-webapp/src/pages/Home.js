import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Alert, Pagination } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined } from '@ant-design/icons';
import NewsList from '../components/NewsList';
import NewsSkeleton from '../components/NewsSkeleton';
import SearchBar from '../components/SearchBar';
import api from '../services/api';
import './Home.css';

const CACHE_VERSION = 1; // 递增此值可强制清除所有缓存
const CACHE_KEY_NEWS = `cache:v${CACHE_VERSION}:news`;
const CACHE_KEY_HERO = `cache:v${CACHE_VERSION}:hero`;
const CACHE_TTL = 300 * 1000; // 5分钟，极致缓存命中

function getCached(key) {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return null;
    const { data, time } = JSON.parse(raw);
    if (Date.now() - time > CACHE_TTL) {
      sessionStorage.removeItem(key);
      return null;
    }
    return data;
  } catch { return null; }
}

function setCache(key, data) {
  try {
    sessionStorage.setItem(key, JSON.stringify({ data, time: Date.now() }));
  } catch {}
}

const Home = () => {
  const [news, setNews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [searchKeyword, setSearchKeyword] = useState('');
  const [sentimentData, setSentimentData] = useState(null);
  const pageSize = 12;
  const abortRef = useRef(null);
  const debounceRef = useRef(null);

  const fetchLatestNews = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      setLoading(true);
      const endpoint = searchKeyword ? '/news/search' : '/news/latest';
      const params = { page, limit: pageSize };
      if (searchKeyword) params.q = searchKeyword;

      // 缓存即时展示
      if (!searchKeyword) {
        const cached = getCached(`${CACHE_KEY_NEWS}:p${page}`);
        if (cached) { setNews(cached.data); setTotal(cached.total); setLoading(false); }
      }

      const data = await api.get(endpoint, { params, signal: controller.signal });
      setNews(data);
      setTotal(data.total || 0);
      // 预埋文章数据到 sessionStorage，详情页零请求
      try {
        (Array.isArray(data) ? data : []).forEach(a => {
          if (a?.article_id) sessionStorage.setItem(`article:${a.article_id}`, JSON.stringify(a));
        });
      } catch {}
      if (!searchKeyword) setCache(`${CACHE_KEY_NEWS}:p${page}`, { data, total: data.total || 0 });
      setError(null);
    } catch (err) {
      if (err.name !== 'AbortError' && err.name !== 'CanceledError') setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [page, searchKeyword]);

  // Hero 数据串行加载
  const [heroLoaded, setHeroLoaded] = useState(false);
  useEffect(() => {
    if (!heroLoaded && !loading && news.length > 0) {
      setHeroLoaded(true);
      (async () => {
        const cached = getCached(CACHE_KEY_HERO);
        if (cached) setSentimentData(cached);
        try {
          const data = await api.get('/analysis/hero');
          setSentimentData(data);
          setCache(CACHE_KEY_HERO, data);
        } catch (e) { /* 静默 */ }
      })();
    }
  }, [loading, news.length, heroLoaded]);

  // 轮询：页面可见时每 60s 一次
  const [newContentAvailable, setNewContentAvailable] = useState(false);
  const latestAtRef = useRef('');
  const pollTimerRef = useRef(null);
  useEffect(() => {
    const poll = async () => {
      if (document.hidden) { pollTimerRef.current = setTimeout(poll, 15000); return; }
      try {
        const data = await api.get('/news/fresh');
        if (data?.latest_at && data.latest_at !== latestAtRef.current) {
          if (latestAtRef.current && data.latest_at > latestAtRef.current) setNewContentAvailable(true);
          latestAtRef.current = data.latest_at;
        }
      } catch (e) { /* 静默 */ }
      pollTimerRef.current = setTimeout(poll, 60000);
    };
    pollTimerRef.current = setTimeout(poll, 10000);
    const onVisible = () => { if (!document.hidden) poll(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearTimeout(pollTimerRef.current);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchLatestNews(), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (abortRef.current) abortRef.current.abort();
    };
  }, [fetchLatestNews]);

  const handleSearch = async (keyword) => {
    setSearchKeyword(keyword);
    setPage(1);
  };

  const handlePageChange = (newPage) => {
    setPage(newPage);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const isPositive = sentimentData?.sentiment?.overallScore >= 0;

  return (
    <div className="home">
      {/* Hero 区域 */}
      <header className="hero-section">
        <div className="hero-inner">
          {/* 左侧文字 */}
          <div className="hero-text">
            <div className="live-indicator">
              <span className="pulse-dot" />
              <span>实时数据驱动</span>
            </div>
            <h1 className="hero-title">
              <span className="hero-title-text">别看新闻</span><br />
              <span className="neon-glow">看数据</span>
            </h1>
            <p className="hero-subtitle">
              新世代财经洞察平台，社区驱动的前沿市场信号。我们读图表，你读趋势——告别传统财经信息焦虑。
            </p>
            <div className="hero-cta">
              <SearchBar onSearch={handleSearch} />
            </div>
            {sentimentData && (
              <div className="hero-stats">
                <span>已有</span>
                <span className="stat-neon">{sentimentData.totalNews || 0}</span>
                <span>条今日分析</span>
                <span className="stat-divider" />
                <span>情感</span>
                <span className={`stat-neon ${isPositive ? '' : 'stat-signal'}`}>
                  {isPositive ? '正面' : '负面'} {(sentimentData.sentiment?.overallScore || 0).toFixed(3)}
                </span>
              </div>
            )}
          </div>

          {/* 右侧浮动数据卡 */}
          <div className="hero-cards">
            <div className="float-card float-anim">
              <ArrowUpOutlined className="fc-icon" style={{ color: 'var(--gold)' }} />
              <span className="fc-label">正面</span>
              <span className="fc-value neon">{sentimentData?.sentiment?.positiveCount || 0}</span>
            </div>
            <div className="float-card float-anim-delay ml8">
              <ArrowDownOutlined className="fc-icon" style={{ color: 'var(--signal)' }} />
              <span className="fc-label">负面</span>
              <span className="fc-value signal">{sentimentData?.sentiment?.negativeCount || 0}</span>
            </div>
            <div className="float-card float-anim-delay2">
              <span className="fc-icon" style={{ color: 'var(--dim)' }}>=</span>
              <span className="fc-label">中性</span>
              <span className="fc-value">{sentimentData?.sentiment?.neutralCount || 0}</span>
            </div>
          </div>
        </div>
      </header>

      {/* 行情Ticker */}
      {sentimentData && (
        <div className="ticker">
          <div className="ticker-track">
            <span>
              市场情绪: <strong className={isPositive ? 'text-positive' : 'text-negative'}>{isPositive ? '正面 ↑' : '负面 ↓'}</strong>
              &nbsp;&nbsp;|&nbsp;&nbsp; 今日分析 {sentimentData.totalNews} 条
              &nbsp;&nbsp;|&nbsp;&nbsp; 情感评分 {(sentimentData.sentiment?.overallScore || 0).toFixed(3)}
              &nbsp;&nbsp;|&nbsp;&nbsp; 正面 {sentimentData.sentiment?.positiveCount} | 中性 {sentimentData.sentiment?.neutralCount} | 负面 {sentimentData.sentiment?.negativeCount}
            </span>
            <span>
              市场情绪: <strong className={isPositive ? 'text-positive' : 'text-negative'}>{isPositive ? '正面 ↑' : '负面 ↓'}</strong>
              &nbsp;&nbsp;|&nbsp;&nbsp; 今日分析 {sentimentData.totalNews} 条
              &nbsp;&nbsp;|&nbsp;&nbsp; 情感评分 {(sentimentData.sentiment?.overallScore || 0).toFixed(3)}
              &nbsp;&nbsp;|&nbsp;&nbsp; 正面 {sentimentData.sentiment?.positiveCount} | 中性 {sentimentData.sentiment?.neutralCount} | 负面 {sentimentData.sentiment?.negativeCount}
            </span>
          </div>
        </div>
      )}

      {/* 数据指标栏 */}
      {sentimentData && (
        <div style={{
          display: 'flex', gap: 12, maxWidth: 840, margin: '16px auto 8px', padding: '0 24px',
          flexWrap: 'wrap', justifyContent: 'center',
        }}>
          {[
            { label: '分析总量', value: sentimentData.totalNews || 0, color: 'var(--frost)' },
            { label: '正面', value: sentimentData.sentiment?.positiveCount || 0, color: 'var(--gold)' },
            { label: '负面', value: sentimentData.sentiment?.negativeCount || 0, color: 'var(--signal)' },
          ].map((m, i) => (
            <div key={i} style={{
              background: 'var(--card-bg)', border: '1px solid var(--card-border)',
              borderRadius: 6, padding: '10px 20px', textAlign: 'center',
              minWidth: 120, flex: '1 1 0',
            }}>
              <div style={{ fontSize: 11, color: 'var(--dim)', marginBottom: 4 }}>{m.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, color: m.color }}>{m.value.toLocaleString()}</div>
            </div>
          ))}
        </div>
      )}

      {/* 新内容通知 */}
      {newContentAvailable && (
        <div onClick={() => { setNewContentAvailable(false); fetchLatestNews(); }}
          style={{
            maxWidth: 1050, margin: '0 auto 16px', padding: '10px 16px',
            background: 'rgba(196,163,90,0.1)', borderRadius: 6,
            display: 'flex', alignItems: 'center', gap: 10,
            border: '1px solid rgba(196,163,90,0.3)', cursor: 'pointer',
          }}>
          <span style={{ fontSize: 14 }}>🆕</span>
          <span style={{ color: 'var(--gold)', fontSize: 13, fontWeight: 500 }}>
            有新的内容，点击刷新
          </span>
          <span
            onClick={(e) => { e.stopPropagation(); setNewContentAvailable(false); }}
            style={{ marginLeft: 'auto', cursor: 'pointer', color: 'var(--dim)', fontSize: 14 }}
          >✕</span>
        </div>
      )}

      {/* 搜索激活指示条 */}
      {searchKeyword && !loading && (
        <div style={{
          maxWidth: 1050, margin: '0 auto 16px', padding: '8px 16px',
          background: 'var(--card-bg)', borderRadius: 6,
          display: 'flex', alignItems: 'center', gap: 12,
          border: '1px solid var(--border)',
        }}>
          <span style={{ color: 'var(--dim)', fontSize: 13 }}>🔍 搜索：</span>
          <strong style={{ fontSize: 14 }}>"{searchKeyword}"</strong>
          <span style={{ color: 'var(--dim)', fontSize: 12, marginLeft: 'auto' }}>
            找到 {total} 条结果
          </span>
          <span
            onClick={() => handleSearch('')}
            style={{ cursor: 'pointer', color: 'var(--gold)', fontSize: 13, marginLeft: 8 }}
          >
            ✕ 清除
          </span>
        </div>
      )}

      {/* 新闻列表 */}
      {loading ? (
        <div style={{ maxWidth: 840, margin: '0 auto', padding: '40px 24px' }}>
          <NewsSkeleton count={6} />
        </div>
      ) : error ? (
        <div style={{ textAlign: 'center', padding: '40px 0' }}>
          <Alert message="出错了" description={error} type="error" showIcon />
        </div>
      ) : (
        <div className="news-listing">
          <h2 className="section-title">最新洞察</h2>
          <p className="section-subtitle">市场脉搏，一秒不漏。来自社区验证的前沿信号。</p>
          <NewsList news={news} />
          {total > pageSize && (
            <div style={{ textAlign: 'center', marginTop: 40 }}>
              <Pagination
                current={page}
                total={total}
                pageSize={pageSize}
                onChange={handlePageChange}
                showTotal={(t) => `共 ${t} 条`}
                showQuickJumper
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default Home;
