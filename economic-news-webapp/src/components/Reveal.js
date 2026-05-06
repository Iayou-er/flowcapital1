import React from 'react';
import { useReveal } from '../hooks/useReveal';

const Reveal = ({ children, delay = 0, threshold = 0.1, maxDelay = 150 }) => {
  const [ref, isVisible] = useReveal(threshold);
  // 前 3 项无延迟直接显示
  const effectiveDelay = delay < 100 ? 0 : Math.min(delay, maxDelay);
  return (
    <div
      ref={ref}
      className={`reveal ${isVisible ? 'visible' : ''}`}
      style={{ transitionDelay: `${effectiveDelay}ms`, transitionDuration: '0.3s' }}
    >
      {children}
    </div>
  );
};

export default Reveal;
