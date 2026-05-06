import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Alert, Pagination, Tag } from 'antd';
import NewsList from '../components/NewsList';
import NewsSkeleton from '../components/NewsSkeleton';
import SearchBar from '../components/SearchBar';
import api from '../services/api';
import './Home.css';  // 共用新闻列表样式
import './Media.css';

const CACHE_VERSION = 1;
const CACHE_KEY = `cache:v${CACHE_VERSION}:media`;
const CACHE_TTL = 300 * 1000;

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

const SOURCE_STYLES = {
  '微信公众号': { color: '#07C160', bg: 'rgba(7,193,96,0.1)' },
  '雪球': { color: '#E53935', bg: 'rgba(229,57,53,0.1)' },
  '虎嗅': { color: '#FF6D00', bg: 'rgba(255,109,0,0.1)' },
  '少数派': { color: '#D50000', bg: 'rgba(213,0,0,0.1)' },
  '36氪': { color: '#2979FF', bg: 'rgba(41,121,255,0.1)' },
  '钛媒体': { color: '#00BCD4', bg: 'rgba(0,188,212,0.1)' },
  '观察者网': { color: '#8D6E63', bg: 'rgba(141,110,99,0.1)' },
};

const Media = () => {
  const [news, setNews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [searchKeyword, setSearchKeyword] = useState('');
  const pageSize = 12;
  const abortRef = useRef(null);
  const debounceRef = useRef(null);

  const fetchMedia = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      setLoading(true);
      const endpoint = searchKeyword ? '/news/media/search' : '/news/media';
      const params = { page, limit: pageSize };
      if (searchKeyword) params.q = searchKeyword;

      if (!searchKeyword) {
        const cached = getCached(`${CACHE_KEY}:p${page}`);
        if (cached) { setNews(cached.data); setTotal(cached.total); setLoading(false); }
      }

      const data = await api.get(endpoint, { params, signal: controller.signal });
      setNews(data);
      setTotal(data.total || 0);
      try {
        (Array.isArray(data) ? data : []).forEach(a => {
          if (a?.article_id) sessionStorage.setItem(`article:${a.article_id}`, JSON.stringify(a));
        });
      } catch {}
      if (!searchKeyword) setCache(`${CACHE_KEY}:p${page}`, { data, total: data.total || 0 });
      setError(null);
    } catch (err) {
      if (err.name !== 'AbortError' && err.name !== 'CanceledError') setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [page, searchKeyword]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => fetchMedia(), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (abortRef.current) abortRef.current.abort();
    };
  }, [fetchMedia]);

  const handleSearch = (keyword) => {
    setSearchKeyword(keyword);
    setPage(1);
  };

  return (
    <div className="home">
      <header className="hero-section" style={{ minHeight: '30vh' }}>
        <div className="hero-inner">
          <div className="hero-text">
            <h1 className="hero-title">
              <span className="hero-title-text">自媒体观察</span>
            </h1>
            <p className="hero-subtitle">
              来自微信公众号、雪球、虎嗅、少数派、36氪、钛媒体、观察者网的深度内容。
            </p>
            <div className="hero-cta">
              <SearchBar onSearch={handleSearch} placeholder="搜索自媒体观点..." storageKey="mediaSearchHistory" />
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 16 }}>
              {Object.entries(SOURCE_STYLES).map(([name, style]) => (
                <Tag key={name} style={{ color: style.color, background: style.bg, border: 'none', fontSize: 13, padding: '2px 10px' }}>
                  {name}
                </Tag>
              ))}
            </div>
          </div>
        </div>
      </header>

      {/* 搜索激活指示条 */}
      {searchKeyword && !loading && (
        <div style={{
          maxWidth: 840, margin: '0 auto 16px', padding: '8px 16px',
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
          <h2 className="section-title">最新观点</h2>
          <p className="section-subtitle">多元视角，独立观点。来自各大平台的精选内容。</p>
          <NewsList news={news} />
          {total > pageSize && (
            <div style={{ textAlign: 'center', marginTop: 40 }}>
              <Pagination
                current={page} total={total} pageSize={pageSize}
                onChange={p => { setPage(p); window.scrollTo({ top: 0, behavior: 'smooth' }); }}
                showTotal={t => `共 ${t} 条`} showQuickJumper
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default Media;
