import { useRef, useEffect } from 'react';
import { useTheme } from '../context/ThemeContext';
import { useLocation } from 'react-router-dom';

const ParticleCanvas = () => {
  const { isLight } = useTheme();
  const location = useLocation();
  const canvasRef = useRef(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    let animId;
    let w = canvas.width = window.innerWidth;
    let h = canvas.height = window.innerHeight;

    // 优化1: 粒子数 100→50, 首页全量/其他页减半
    const isHome = location.pathname === '/';
    const PARTICLE_COUNT = isHome ? 50 : 25;
    const particles = [];

    const primaryColor = isLight ? '232,117,58' : '196,163,90';
    const accentColor = isLight ? '92,184,92' : '168,67,62';
    const lineColor = isLight ? '232,117,58' : '196,163,90';
    // 优化2: 连线距离阈值 120→80
    const LINE_DIST = 80;
    // 优化3: 网格加速 O(n²)→O(n)
    const GRID_SIZE = LINE_DIST;

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      particles.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.3) * 0.4,
        vy: (Math.random() - 0.5) * 0.2,
        size: Math.random() * 2 + 0.5,
        alpha: Math.random() * 0.3 + 0.08,
        color: Math.random() < 0.12 ? accentColor : primaryColor,
      });
    }

    const draw = () => {
      ctx.clearRect(0, 0, w, h);

      // 空间哈希网格
      const grid = new Map();
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x > w + 10) p.x = -10;
        if (p.x < -10) p.x = w + 10;
        if (p.y > h + 10) p.y = -10;
        if (p.y < -10) p.y = h + 10;

        const gx = Math.floor(p.x / GRID_SIZE);
        const gy = Math.floor(p.y / GRID_SIZE);
        const key = `${gx},${gy}`;
        if (!grid.has(key)) grid.set(key, []);
        grid.get(key).push(p);
      }

      // 画粒子
      for (const p of particles) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, Math.max(0.5, p.size), 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${p.color},${p.alpha})`;
        ctx.fill();
      }

      // 网格加速连线：只检查相邻格子
      const drawn = new Set();
      for (const [key, cell] of grid) {
        const [gx, gy] = key.split(',').map(Number);
        for (const p of cell) {
          for (let dx = -1; dx <= 1; dx++) {
            for (let dy = -1; dy <= 1; dy++) {
              const neighbors = grid.get(`${gx + dx},${gy + dy}`) || [];
              for (const q of neighbors) {
                if (p === q) continue;
                const pairKey = p.x < q.x ? `${p.x},${p.y},${q.x},${q.y}` : `${q.x},${q.y},${p.x},${p.y}`;
                if (drawn.has(pairKey)) continue;
                drawn.add(pairKey);
                const dxp = p.x - q.x;
                const dyp = p.y - q.y;
                const dist = Math.sqrt(dxp * dxp + dyp * dyp);
                if (dist < LINE_DIST) {
                  ctx.beginPath();
                  ctx.moveTo(p.x, p.y);
                  ctx.lineTo(q.x, q.y);
                  const alpha = (1 - dist / LINE_DIST) * 0.06;
                  ctx.strokeStyle = `rgba(${lineColor},${alpha})`;
                  ctx.lineWidth = 0.4;
                  ctx.stroke();
                }
              }
            }
          }
        }
      }

      animId = requestAnimationFrame(draw);
    };

    draw();

    const handleResize = () => {
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    return () => {
      cancelAnimationFrame(animId);
      window.removeEventListener('resize', handleResize);
    };
  }, [isLight, location.pathname]);

  return <canvas id="particleCanvas" ref={canvasRef} />;
};

export default ParticleCanvas;
