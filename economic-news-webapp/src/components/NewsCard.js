import React from 'react';
import { Link } from 'react-router-dom';
import './NewsCard.css';

const CATEGORY_STYLES = {
  '宏观经济': { accent: 'var(--gold)', bg: 'var(--gold-subtle)' },
  '股市动态': { accent: 'var(--gold)', bg: 'var(--gold-subtle)' },
  '债券市场': { accent: 'var(--steel-blue, #5B8AB8)', bg: 'var(--steel-blue-dim, rgba(91,138,184,0.12))' },
  '外汇交易': { accent: 'var(--copper, #C4956A)', bg: 'var(--copper-dim, rgba(196,149,106,0.1))' },
  '期货市场': { accent: 'var(--signal)', bg: 'var(--signal-dim)' },
  '公司财报': { accent: 'var(--amethyst, #9A7EC8)', bg: 'var(--amethyst-dim, rgba(154,126,200,0.1))' },
  '政策法规': { accent: 'var(--steel-blue, #5B8AB8)', bg: 'var(--steel-blue-dim, rgba(91,138,184,0.12))' },
  '行业分析': { accent: 'var(--copper, #C4956A)', bg: 'var(--copper-dim, rgba(196,149,106,0.1))' },
  '国际市场': { accent: 'var(--amethyst, #9A7EC8)', bg: 'var(--amethyst-dim, rgba(154,126,200,0.1))' },
  '投资策略': { accent: 'var(--signal)', bg: 'var(--signal-dim)' },
};

const NewsCard = ({ news }) => {
  const catStyle = CATEGORY_STYLES[news.category] || { accent: 'var(--gold)', bg: 'var(--gold-subtle)' };

  return (
    <Link
      to={`/news/${news.article_id}`}
      className="news-card"
    >
      <span className="accent-strip" style={{ background: catStyle.accent }} />

      <div className="news-card-inner">
        <div className="news-meta">
          <span
            className="category-tag"
            style={{ background: catStyle.bg, color: catStyle.accent }}
          >
            {news.category || '综合财经'}
          </span>
          <span className="news-time">
            {news.published_at || ''}
          </span>
          {news.source && (
            <span className="news-source">· {news.source}</span>
          )}
        </div>

        <h5 className="news-title">{news.title}</h5>

        {news.summary && (
          <p className="news-summary">
            {news.summary.length > 100 ? news.summary.slice(0, 100) + '...' : news.summary}
          </p>
        )}
      </div>
    </Link>
  );
};

export default React.memo(NewsCard, (prev, next) =>
  prev.news?.article_id === next.news?.article_id &&
  prev.news?.title === next.news?.title
);
