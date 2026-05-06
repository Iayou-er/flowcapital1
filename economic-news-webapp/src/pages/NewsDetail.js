import React, { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { Row, Col, Spin, Alert, Card, Descriptions, Tag, Typography, Divider, Button } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useParams, useNavigate } from 'react-router-dom';
import api from '../services/api';
import { useTheme } from '../context/ThemeContext';
import './NewsDetail.css';

const { Title, Paragraph } = Typography;

// 分类颜色映射（模块级常量，避免每次渲染重建）
const CATEGORY_COLORS_LIGHT = {
  '宏观经济': '#E8753A', '股市动态': '#E8753A', '债券市场': '#2B5B84',
  '外汇交易': '#7A6B5D', '期货市场': '#B83A3A', '公司财报': '#8E6B9A',
  '政策法规': '#2B5B84', '行业分析': '#7A6B5D', '国际市场': '#8E6B9A',
  '投资策略': '#B83A3A'
};
const CATEGORY_COLORS_DARK = {
  '宏观经济': '#C4A35A', '股市动态': '#C4A35A', '债券市场': '#5B8AB8',
  '外汇交易': '#C4956A', '期货市场': '#A8433E', '公司财报': '#9A7EC8',
  '政策法规': '#5B8AB8', '行业分析': '#C4956A', '国际市场': '#9A7EC8',
  '投资策略': '#A8433E'
};

const NewsDetail = () => {
  const { isLight } = useTheme();
  const { id } = useParams();
  const navigate = useNavigate();
  const [news, setNews] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const abortRef = useRef(null);

  const fetchNewsDetail = useCallback(async () => {
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      setLoading(true);
      // 优先从 sessionStorage 缓存读取（列表页已预埋）
      const cached = sessionStorage.getItem(`article:${id}`);
      if (cached) {
        setNews(JSON.parse(cached));
        setLoading(false);
        return;
      }
      const data = await api.get(`/news/${id}`, { signal: controller.signal });
      setNews(data);
      setError(null);

      // 获取分析结果（后台运行，不阻塞页面，导航离开时自动取消）
      api.get(`/analysis/${id}`, { signal: controller.signal })
        .then(data => setAnalysis(data))
        .catch(() => {});
    } catch (err) {
      if (err.name === 'AbortError' || err.name === 'CanceledError') return;
      console.error('NewsDetail fetch error:', { id, message: err.message, code: err.code, name: err.name });
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    fetchNewsDetail();
    return () => { if (abortRef.current) abortRef.current.abort(); };
  }, [fetchNewsDetail]);

  // ESC 键返回
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') navigate(-1); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate]);

  const formatTime = (timeString) => {
    return new Date(timeString).toLocaleString('zh-CN');
  };

  const getCategoryColor = useMemo(() => {
    const colors = isLight ? CATEGORY_COLORS_LIGHT : CATEGORY_COLORS_DARK;
    const fallback = isLight ? '#6B6B6B' : '#8a8578';
    return (category) => colors[category] || fallback;
  }, [isLight]);

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '40px' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    return <Alert message="错误" description={error} type="error" showIcon />;
  }

  if (!news) {
    return (
      <div style={{ textAlign: 'center', padding: '40px' }}>
        <p>未找到相关新闻</p>
      </div>
    );
  }

  return (
    <div className="news-detail">
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate(-1)}
          style={{
            color: 'var(--gold)', borderColor: 'var(--gold)', background: 'transparent',
            borderRadius: 6,
          }}
        >
          返回
        </Button>
      </div>
      <Title level={2}>{news.title}</Title>

      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Card>
            <Descriptions bordered column={2}>
              <Descriptions.Item label="分类">
                <Tag color={getCategoryColor(news.category)}>{news.category}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="发布时间">
                {formatTime(news.published_at)}
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                {news.source || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="作者">
                {news.author || '-'}
              </Descriptions.Item>
              {analysis && analysis.sentiment_score !== undefined && (
                <>
                  <Descriptions.Item label="情感分数">
                    <Tag color={
                      analysis.sentiment_score > 0.15 ? 'green' :
                      analysis.sentiment_score < -0.15 ? 'red' : 'default'
                    }>{analysis.sentiment_label === 'positive' ? '正面' :
                       analysis.sentiment_label === 'negative' ? '负面' : '中性'} ({analysis.sentiment_score})</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="关键词">
                    {analysis.keywords && analysis.keywords.length > 0
                      ? analysis.keywords.slice(0, 5).map((kw, i) => (
                          <Tag key={i} style={{ marginRight: 4 }}>{kw}</Tag>
                        ))
                      : '-'}
                  </Descriptions.Item>
                </>
              )}
            </Descriptions>

            {analysis && analysis.auto_summary && (
              <>
                <Divider orientation="left">AI 摘要</Divider>
                <Paragraph style={{ color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                  {analysis.auto_summary}
                </Paragraph>
              </>
            )}

            {news.tags && news.tags.length > 0 && (
              <>
                <Divider orientation="left">标签</Divider>
                <div style={{ marginBottom: 20 }}>
                  {news.tags.map((tag, index) => (
                    <Tag key={index} style={{ marginBottom: 8, marginRight: 8, backgroundColor: 'var(--copper, #C4956A)', borderColor: 'transparent', color: 'white' }}>
                      {tag}
                    </Tag>
                  ))}
                </div>
              </>
            )}

            {news.summary && (
              <>
                <Divider orientation="left">摘要</Divider>
                <Paragraph style={{ color: 'var(--text-secondary)' }}>
                  {news.summary}
                </Paragraph>
              </>
            )}

            {news.content && (
              <>
                <Divider orientation="left">新闻正文</Divider>
                <Paragraph className="news-content">
                  {news.content}
                </Paragraph>
              </>
            )}

            {news.url && (
              <>
                <Divider orientation="left">原文链接</Divider>
                <a href={news.url} target="_blank" rel="noopener noreferrer">
                  {news.url}
                </a>
              </>
            )}
          </Card>
        </Col>
      </Row>

    </div>
  );
};

export default NewsDetail;
