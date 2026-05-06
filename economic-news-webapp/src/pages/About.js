import React from 'react';
import { Row, Col, Card, Typography, List, Divider, Space } from 'antd';
import './About.css';

const { Text, Paragraph } = Typography;

const About = () => {
  return (
    <div className="about-page">
      <div className="about-header">
        <h2>关于 <span className="neon-glow">FlowCapital</span></h2>
        <p className="about-header-subtitle">自动化采集、存储与智能分析平台</p>
      </div>

      <div className="about-content">
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <Card>
            <Paragraph>
              这是一个完整的经济新闻采集、存储和分析系统，能够自动化抓取经济新闻并进行智能分析。
            </Paragraph>

            <Divider orientation="left">系统特性</Divider>
            <List
              size="small"
              bordered
              dataSource={[
                '多源新闻采集 - 14个数据源并行聚合（东方财富、新浪财经、金十数据、华尔街见闻、第一财经、网易财经、同花顺、财联社、新华网RSS、财新RSS等）',
                '智能降级策略 - 各数据源独立容错，失败自动跳过，确保整体可用性',
                '昼夜双主题 - 日间白底橙绿配色 / 夜间深蓝鎏金风格，一键切换自动记忆',
                '情感分析引擎 - 基于SnowNLP的中文情感评分，支持正/中/负三维统计',
                '关键词提取 - 自动抽取高频经济术语，支持浮窗查看详情',
                '数据看板 - 情感趋势折线图、分类热度柱状图、饼图分布可视化',
                '本地数据存储 - SQLite长连接模式，WAL并发写入优化',
                'RESTful API服务 - 新闻查询、搜索、分析、分类统计等完整接口',
                '图谱检索引擎 - 基于GraphRAG的知识图谱构建与智能查询',
                '定时任务调度 - 每30分钟轻量爬取 + 每日凌晨2点全面分析'
              ]}
              renderItem={(item) => <List.Item>{item}</List.Item>}
            />

            <Divider orientation="left">技术栈</Divider>
            <List
              size="small"
              bordered
              dataSource={[
                '后端: Python 3.8+, FastAPI, SQLite, schedule',
                '前端: React.js, Ant Design, Recharts 数据可视化',
                '爬虫: Requests, BeautifulSoup4, feedparser(RSS解析)',
                '分析: jieba 分词, SnowNLP 情感分析, NetworkX 图谱',
                '知识图谱: GraphRAG, 支持多云端LLM接入',
                '部署: Windows本地环境, uvicorn 服务, npm 前端构建'
              ]}
              renderItem={(item) => <List.Item>{item}</List.Item>}
            />

            <Divider orientation="left">使用说明</Divider>
            <Space direction="vertical">
              <Text>1. 运行 python run_project.py 启动项目</Text>
              <Text>2. 选择 3（同时启动调度器和API服务）</Text>
              <Text>3. 前端自动访问 http://localhost:3000</Text>
              <Text>4. 首页浏览新闻，分类页按领域筛选，分析页查看数据看板</Text>
              <Text>5. 点击导航栏 ☀️/🌙 按钮切换日间/夜间主题</Text>
            </Space>
          </Card>
        </Col>
      </Row>
      </div>
    </div>
  );
};

export default About;
