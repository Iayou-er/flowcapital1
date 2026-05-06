import React, { useState, useEffect } from 'react';
import { Row, Col, Spin, Pagination, Alert } from 'antd';
import NewsCard from './NewsCard';
import Reveal from './Reveal';

const NewsListInner = ({ news, loading = false, error = null, onPageChange, currentPage = 1, totalPages = 1, total = null }) => {
  const [currentPageState, setCurrentPageState] = useState(currentPage);
  const pageSize = 20;
  const effectiveTotal = total != null ? total : totalPages * pageSize;

  useEffect(() => {
    setCurrentPageState(currentPage);
  }, [currentPage]);

  if (loading) {
    return <div style={{ textAlign: 'center', padding: '40px' }}><Spin size="large" /></div>;
  }

  if (error) {
    return <Alert message="错误" description={error} type="error" showIcon />;
  }

  if (!news || news.length === 0) {
    return <div style={{ textAlign: 'center', padding: '40px' }}><p>暂无新闻数据</p></div>;
  }

  return (
    <>
      <Row gutter={[16, 16]}>
        {news.map((item, index) => (
          <Col xs={24} md={12} key={item.article_id}>
            <Reveal delay={index * 25}>
              <NewsCard news={item} />
            </Reveal>
          </Col>
        ))}
      </Row>
      {effectiveTotal > pageSize && (
        <div style={{ textAlign: 'center', marginTop: 20 }}>
          <Pagination
            current={currentPageState}
            total={effectiveTotal}
            pageSize={pageSize}
            onChange={p => {
              setCurrentPageState(p);
              if (onPageChange) onPageChange(p);
            }}
            showSizeChanger={false}
            showQuickJumper={false}
            showTotal={(t, range) => `显示 ${range[0]}-${range[1]} 条，共 ${t} 条`}
          />
        </div>
      )}
    </>
  );
};

const NewsList = React.memo(NewsListInner, (prev, next) =>
  prev.news === next.news &&
  prev.loading === next.loading &&
  prev.error === next.error &&
  prev.total === next.total &&
  prev.currentPage === next.currentPage
);

export default NewsList;
