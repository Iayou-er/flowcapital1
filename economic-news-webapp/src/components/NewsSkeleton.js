import React from 'react';
import { Card, Row, Col } from 'antd';

const NewsSkeleton = ({ count = 6 }) => {
  const items = Array.from({ length: count }, (_, i) => i);
  return (
    <Row gutter={[16, 16]}>
      {items.map(i => (
        <Col span={24} key={i}>
          <Card className="news-card" style={{ opacity: 0.6 }}>
            <div style={{
              height: 12, width: '30%', background: 'var(--border)',
              borderRadius: 4, marginBottom: 12,
            }} />
            <div style={{
              height: 16, width: '80%', background: 'var(--border)',
              borderRadius: 4, marginBottom: 8,
            }} />
            <div style={{
              height: 16, width: '60%', background: 'var(--border)',
              borderRadius: 4, marginBottom: 8,
            }} />
            <div style={{
              height: 12, width: '50%', background: 'var(--border)',
              borderRadius: 4,
            }} />
          </Card>
        </Col>
      ))}
    </Row>
  );
};

export default NewsSkeleton;
