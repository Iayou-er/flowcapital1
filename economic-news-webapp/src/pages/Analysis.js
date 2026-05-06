import React, { useEffect, useState, useCallback } from 'react';
import { Row, Col, Spin, Alert, Card, Radio, DatePicker, Typography, Empty, Popover, Tag } from 'antd';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, PieChart, Pie, Cell, LineChart, Line } from 'recharts';
import api from '../services/api';
import { useCountUp } from '../hooks/useReveal';
import { useTheme } from '../context/ThemeContext';
import './Analysis.css';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const CACHE_KEY = 'cache:analysis';
const CACHE_TTL = 60 * 1000; // 1分钟

function getCached() {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const { data, time } = JSON.parse(raw);
    if (Date.now() - time > CACHE_TTL) {
      sessionStorage.removeItem(CACHE_KEY);
      return null;
    }
    return data;
  } catch { return null; }
}

function setCache(data) {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({ data, time: Date.now() }));
  } catch {}
}

// 数字递增组件
const CountUp = ({ end, suffix, style }) => {
  const [ref, count] = useCountUp(end, 1500);
  return (
    <span ref={ref} style={style}>
      {count}{suffix || ''}
    </span>
  );
};

const Analysis = () => {
  const [analysisData, setAnalysisData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [timeRange, setTimeRange] = useState('day');
  const [dateRange, setDateRange] = useState(null);

  const fetchAnalysisData = useCallback(async () => {
    try {
      setLoading(true);
      const params = { time_range: timeRange };
      if (dateRange && dateRange[0] && dateRange[1]) {
        params.start_date = dateRange[0].format('YYYY-MM-DD');
        params.end_date = dateRange[1].format('YYYY-MM-DD');
      }

      // 先读缓存快速展示（stale-while-revalidate）
      const cached = getCached();
      if (cached && !dateRange) {
        setAnalysisData(cached);
        setLoading(false);
        // 后台静默刷新
        api.get('/analysis', { params, timeout: 30000 })
          .then(data => { setAnalysisData(data); setCache(data); })
          .catch(() => {});
        return;
      }

      const data = await api.get('/analysis', { params, timeout: 30000 });
      setAnalysisData(data);
      if (!dateRange) setCache(data);
      setError(null);
    } catch (err) {
      setError(err.message);
      console.error('获取分析数据失败:', err);
    } finally {
      setLoading(false);
    }
  }, [timeRange, dateRange]);

  useEffect(() => {
    fetchAnalysisData();
  }, [fetchAnalysisData]);

  const handleDateRangeChange = (dates) => {
    setDateRange(dates);
    if (dates && dates[0] && dates[1]) {
      const fetchData = async () => {
        try {
          setLoading(true);
          const params = {
            start_date: dates[0].format('YYYY-MM-DD'),
            end_date: dates[1].format('YYYY-MM-DD')
          };
          const data = await api.get('/analysis', { params, timeout: 30000 });
          setAnalysisData(data);
          setError(null);
        } catch (err) {
          setError(err.message);
        } finally {
          setLoading(false);
        }
      };
      fetchData();
    }
  };

  const { isLight } = useTheme();
  // 财经专业配色 — 日间/夜间独立调色板（无绿色）
  const COLORS = isLight
    ? ['#E8753A', '#B83A3A', '#2B5B84', '#7A6B5D', '#8E6B9A', '#4A7A8A', '#C88B5E', '#9A5A6A']
    : ['#C4A35A', '#A8433E', '#5B8AB8', '#8B7E6B', '#9A7EC8', '#5A8E9A', '#C4956A', '#B8708A'];

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

  const hasData = analysisData && analysisData.categoryDistribution?.length > 0;

  return (
    <div className="analysis">
      <div className="analysis-header">
        <Title level={3}>经济新闻分析</Title>
        <p className="analysis-header-subtitle">通过数据可视化洞察市场趋势与情绪</p>
      </div>

      {/* 控制面板 — 毛玻璃效果 */}
      <Card className="glass-card" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap' }}>
          <div>
            <Text strong>时间范围:</Text>
            <Radio.Group value={timeRange} onChange={(e) => setTimeRange(e.target.value)} style={{ marginLeft: 16 }}>
              <Radio.Button value="day">今日</Radio.Button>
              <Radio.Button value="week">本周</Radio.Button>
              <Radio.Button value="month">本月</Radio.Button>
              <Radio.Button value="year">本年</Radio.Button>
            </Radio.Group>
          </div>
          <div>
            <Text strong>日期范围:</Text>
            <RangePicker
              style={{ marginLeft: 16 }}
              onChange={handleDateRangeChange}
            />
          </div>
        </div>
      </Card>

      {!hasData ? (
        <Empty description="暂无分析数据" />
      ) : (
        <>
          {/* 统计条 */}
          {analysisData.totalNews && (
            <div className="total-bar reveal visible">
              <CountUp end={analysisData.totalNews} suffix=" 条新闻" style={{ fontSize: 16, fontWeight: 600, color: 'var(--text-heading)' }} />
            </div>
          )}

          {/* 情感指标卡片 */}
          {analysisData.sentiment && (
            <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
              <Col xs={24} sm={8}>
                <Card className="stat-card" style={{ textAlign: 'center' }}>
                  <div className="stat-number" style={{
                    color: analysisData.sentiment.overallScore >= 0 ? COLORS[0] : COLORS[1],
                    fontSize: 32
                  }}>
                    {analysisData.sentiment.overallScore >= 0 ? '正面' : '负面'}
                  </div>
                  <div className="stat-label">
                    分数: <span className="mono">{analysisData.sentiment.overallScore?.toFixed(3)}</span>
                  </div>
                </Card>
              </Col>
              <Col xs={24} sm={8}>
                <Card className="stat-card">
                  <div style={{ display: 'flex', justifyContent: 'space-around', textAlign: 'center' }}>
                    <div>
                      <div className="stat-number" style={{ fontSize: 28, color: 'var(--gold)' }}>
                        <CountUp end={analysisData.sentiment.positiveCount} />
                      </div>
                      <div className="stat-label">正面</div>
                    </div>
                    <div>
                      <div className="stat-number" style={{ fontSize: 28, color: '#86868b' }}>
                        <CountUp end={analysisData.sentiment.neutralCount} />
                      </div>
                      <div className="stat-label">中性</div>
                    </div>
                    <div>
                      <div className="stat-number" style={{ fontSize: 28, color: 'var(--signal)' }}>
                        <CountUp end={analysisData.sentiment.negativeCount} />
                      </div>
                      <div className="stat-label">负面</div>
                    </div>
                  </div>
                </Card>
              </Col>
              <Col xs={24} sm={8}>
                <Card className="stat-card" style={{ textAlign: 'center' }}>
                  <div className="stat-number" style={{ color: 'var(--gold)', fontSize: 32 }}>
                    <CountUp end={analysisData.topKeywords?.length || 0} />
                  </div>
                  <div className="stat-label">热门关键词</div>
                </Card>
              </Col>
            </Row>
          )}

          {/* 图表 — 毛玻璃卡片 */}
          <Row gutter={[16, 16]}>
            <Col span={24}>
              <Card className="chart-card" title="分类分布">
                <ResponsiveContainer width="100%" height={300}>
                  <PieChart>
                    <Pie
                      data={analysisData.categoryDistribution}
                      cx="50%"
                      cy="50%"
                      labelLine={true}
                      outerRadius={80}
                      fill="#8884d8"
                      dataKey="value"
                      label={({ name, percent }) => `${name}: ${(percent * 100).toFixed(0)}%`}
                    >
                      {analysisData.categoryDistribution.map((entry, index) => (
                        <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                      ))}
                    </Pie>
                    <Tooltip />
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </Card>
            </Col>
          </Row>

          {analysisData.newsTrend?.length > 0 && (
            <Row gutter={[16, 16]} style={{ marginTop: 20 }}>
              <Col span={24}>
                <Card className="chart-card" title="新闻趋势">
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart
                      data={analysisData.newsTrend}
                      margin={{ top: 20, right: 30, left: 20, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="date" />
                      <YAxis />
                      <Tooltip />
                      <Legend />
                      <Bar dataKey="count" fill={COLORS[0]} name="新闻数量" radius={[4, 4, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </Card>
              </Col>
            </Row>
          )}

          {analysisData.sentimentTrend?.length > 0 && (
            <Row gutter={[16, 16]} style={{ marginTop: 20 }}>
              <Col span={24}>
                <Card className="chart-card" title="情感趋势">
                  <ResponsiveContainer width="100%" height={300}>
                    <LineChart
                      data={analysisData.sentimentTrend}
                      margin={{ top: 20, right: 30, left: 20, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="date" />
                      <YAxis yAxisId="left" />
                      <YAxis yAxisId="right" orientation="right" domain={[-1, 1]} />
                      <Tooltip />
                      <Legend />
                      <Line yAxisId="left" type="monotone" dataKey="positive" stroke={COLORS[0]} name="正面" strokeWidth={2} dot={false} />
                      <Line yAxisId="left" type="monotone" dataKey="negative" stroke={COLORS[1]} name="负面" strokeWidth={2} dot={false} />
                      <Line yAxisId="left" type="monotone" dataKey="neutral" stroke={COLORS[3]} name="中性" strokeWidth={1.5} dot={false} />
                      <Line yAxisId="right" type="monotone" dataKey="avgSentiment" stroke={COLORS[6] || '#C4956A'} name="平均情感" strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </Card>
              </Col>
            </Row>
          )}

          {analysisData.categoryHeat?.length > 0 && (
            <Row gutter={[16, 16]} style={{ marginTop: 20 }}>
              <Col span={24}>
                <Card className="chart-card" title="分类热度">
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart
                      data={analysisData.categoryHeat}
                      margin={{ top: 20, right: 30, left: 20, bottom: 5 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" />
                      <XAxis dataKey="category" />
                      <YAxis yAxisId="left" />
                      <YAxis yAxisId="right" orientation="right" domain={[-1, 1]} />
                      <Tooltip />
                      <Legend />
                      <Bar yAxisId="left" dataKey="count" fill={COLORS[0]} name="新闻数量" radius={[4, 4, 0, 0]} />
                      <Bar yAxisId="right" dataKey="avgSentiment" fill={COLORS[2]} name="平均情感" opacity={0.7} />
                    </BarChart>
                  </ResponsiveContainer>
                </Card>
              </Col>
            </Row>
          )}

          {/* 热门关键词 */}
          {analysisData.topKeywords?.length > 0 && (
            <Row gutter={[16, 16]} style={{ marginTop: 20 }}>
              <Col span={24}>
                <Card className="chart-card" title="热门关键词">
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', padding: '4px 0' }}>
                    {analysisData.topKeywords.map((item, index) => (
                      <Popover
                        key={index}
                        content={
                          <div className="keyword-popover">
                            <div className="keyword-popover-title">{item.keyword}</div>
                            <div className="keyword-popover-stat">出现次数：<strong>{item.count}</strong> 次</div>
                            <div className="keyword-popover-desc">该词在新闻标题、标签或分类中被提及的频率</div>
                          </div>
                        }
                        trigger="click"
                        placement="top"
                      >
                        <Tag
                          className="keyword-chip"
                          style={{
                            backgroundColor: COLORS[index % COLORS.length],
                            cursor: 'pointer',
                            fontSize: 14,
                            padding: '6px 16px',
                            margin: 0,
                          }}
                        >
                          {item.keyword} ({item.count})
                        </Tag>
                      </Popover>
                    ))}
                  </div>
                </Card>
              </Col>
            </Row>
          )}
        </>
      )}
    </div>
  );
};

export default Analysis;
