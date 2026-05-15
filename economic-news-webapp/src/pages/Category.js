import React, { useEffect, useState, useCallback, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Spin, Alert, Row, Col, Pagination, Card, Empty } from 'antd';
import NewsList from '../components/NewsList';
import Reveal from '../components/Reveal';
import api from '../services/api';
import './Category.css';

const CATEGORY_ICONS = {
  '宏观经济': '📊',
  '股市动态': '📈',
  '债券市场': '📋',
  '外汇交易': '💱',
  '期货市场': '🏭',
  '公司财报': '📑',
  '政策法规': '⚖️',
  '行业分析': '🔍',
  '国际市场': '🌍',
  '投资策略': '💡',
  '综合财经': '📰',
};

const Category = () => {
  const { category } = useParams();
  const navigate = useNavigate();
  const [news, setNews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [categories, setCategories] = useState([]);
  const [catLoading, setCatLoading] = useState(true);
  const pageSize = 20;

  // 获取分类列表
  useEffect(() => {
    let cancelled = false;
    const fetchCategories = async () => {
      try {
        setCatLoading(true);
        const res = await api.get('/news/categories');
        if (!cancelled) {
          const cats = Array.isArray(res) ? res : [];
          if (process.env.NODE_ENV === 'development') console.log('[Category] 分类数据:', cats);
          setCategories(cats);
        }
      } catch (err) {
        console.error('获取分类失败:', err);
        if (!cancelled) {
          setError(err.message);
        }
      } finally {
        if (!cancelled) {
          setCatLoading(false);
        }
      }
    };
    fetchCategories();
    return () => { cancelled = true; };
  }, []);

  const abortRef = useRef(null);

  // 获取分类下的新闻
  const fetchCategoryNews = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setLoading(true);
      setError(null);
      const data = await api.get(`/news/category/${category}`, { params: { page, limit: pageSize }, signal: controller.signal });
      if (process.env.NODE_ENV === 'development') console.log('[Category] 新闻数据:', data);
      const newsArr = Array.isArray(data) ? data : [];
      setNews(newsArr);
      setTotal(data.total || newsArr.length || 0);
    } catch (err) {
      if (err.name === 'AbortError') return;
      console.error('获取分类新闻失败:', err);
      setError(err.message);
      setNews([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [category, page]);

  useEffect(() => {
    if (category) {
      fetchCategoryNews();
    } else {
      setLoading(false);
    }
  }, [category, fetchCategoryNews]);

  const handlePageChange = (newPage) => {
    setPage(newPage);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleCategoryClick = (cat) => {
    navigate(`/category/${encodeURIComponent(cat)}`);
  };

  // 分类选择页
  if (!category) {
    return (
      <div className="category-page">
        <div className="category-header">
          <h2 className="category-title">新闻分类</h2>
          <p className="category-header-subtitle">选择你感兴趣的经济领域</p>
        </div>

        <div className="category-grid">
        {catLoading ? (
          <div style={{ textAlign: 'center', padding: '60px' }}>
            <Spin size="large" />
          </div>
        ) : error ? (
          <div style={{ maxWidth: 600, margin: '0 auto', padding: '20px 0' }}>
            <Alert
              message="获取分类失败"
              description={error}
              type="error"
              showIcon
              action={
                <a href="#reload" onClick={() => window.location.reload()}>重试</a>
              }
            />
          </div>
        ) : categories.length === 0 ? (
          <Empty description="暂无分类数据，请先运行爬虫获取新闻" />
        ) : (
          <Row gutter={[16, 16]}>
            {categories.map((item, idx) => (
              <Col xs={12} sm={8} md={6} lg={4} key={item.category}>
                <Reveal delay={idx * 80}>
                  <Card
                    className="category-card glass-card"
                    hoverable
                    onClick={() => handleCategoryClick(item.category)}
                  >
                    <div className="category-icon">
                      {CATEGORY_ICONS[item.category] || '📰'}
                    </div>
                    <div className="category-name">{item.category}</div>
                    <div className="category-count">{item.count} 条</div>
                  </Card>
                </Reveal>
              </Col>
            ))}
          </Row>
        )}
        </div>
      </div>
    );
  }

  // 分类新闻列表页
  return (
    <div className="category-page">
      <div className="category-header">
        <h2 className="category-title">
          {CATEGORY_ICONS[category] || '📰'} {category}
        </h2>
        <p className="category-header-subtitle">最新经济新闻资讯</p>
      </div>

      <div className="category-news">
      {loading ? (
        <div style={{ textAlign: 'center', padding: '60px' }}>
          <Spin size="large" />
        </div>
      ) : error ? (
        <div style={{ maxWidth: 600, margin: '0 auto', padding: '20px 0' }}>
          <Alert
            message="获取新闻失败"
            description={error}
            type="error"
            showIcon
            action={
              <a href="#retry" onClick={() => { setPage(1); fetchCategoryNews(); }}>重试</a>
            }
          />
        </div>
      ) : news.length === 0 ? (
        <Empty description={`"${category}" 分类下暂无新闻`} />
      ) : (
        <>
          <Row gutter={[16, 16]}>
            <Col span={24}>
              <NewsList news={news} />
            </Col>
          </Row>
          {total > pageSize && (
            <div style={{ textAlign: 'center', marginTop: 24 }}>
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
        </>
      )}
      </div>
    </div>
  );
};

export default Category;
