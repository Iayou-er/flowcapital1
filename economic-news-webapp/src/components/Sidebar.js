import React, { useState, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ThunderboltOutlined, SunOutlined, MoonOutlined, MenuOutlined } from '@ant-design/icons';
import { Drawer } from 'antd';
import { isApiOnline, onConnectionChange } from '../services/api';
import './Sidebar.css';

const menuItems = [
  { key: 'home', label: '首页', path: '/', icon: '⌂' },
  { key: 'category', label: '分类', path: '/category', icon: '☷' },
  { key: 'media', label: '自媒体', path: '/media', icon: '◇' },
  { key: 'analysis', label: '分析', path: '/analysis', icon: '◉' },
  { key: 'guestbook', label: '留言', path: '/guestbook', icon: '✎' },
  { key: 'about', label: '关于', path: '/about', icon: '?' },
];

const Sidebar = ({ isLight, onToggleTheme }) => {
  const location = useLocation();
  const [online, setOnline] = useState(isApiOnline());
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => onConnectionChange(setOnline), []);

  const getActiveKey = () => {
    const p = location.pathname;
    if (p.startsWith('/category')) return 'category';
    if (p.startsWith('/media')) return 'media';
    if (p.startsWith('/analysis')) return 'analysis';
    if (p.startsWith('/guestbook')) return 'guestbook';
    if (p.startsWith('/about')) return 'about';
    if (p.startsWith('/news/')) return null;
    return 'home';
  };

  const activeKey = getActiveKey();

  const renderMenu = (onClick) => (
    <nav className="sidebar-nav">
      {menuItems.map(item => (
        <Link
          key={item.key}
          to={item.path}
          className={`sidebar-item ${activeKey === item.key ? 'active' : ''}`}
          onClick={onClick}
        >
          <span className="sidebar-icon">{item.icon}</span>
          <span className="sidebar-label">{item.label}</span>
        </Link>
      ))}
    </nav>
  );

  return (
    <>
      {/* 桌面端固定侧边栏 */}
      <aside className="sidebar">
        <Link to="/" className="sidebar-logo">
          <ThunderboltOutlined className="sidebar-logo-icon" />
          <span className="sidebar-logo-text">FC</span>
        </Link>

        <div className="sidebar-menu">
          {renderMenu()}
        </div>

        <div className="sidebar-footer">
          <span
            className={`sidebar-dot ${online ? 'online' : 'offline'}`}
            title={online ? 'API 已连接' : 'API 连接断开'}
          />
          <button className="sidebar-theme-btn" onClick={onToggleTheme} title={isLight ? '夜间模式' : '日间模式'}>
            {isLight ? <MoonOutlined /> : <SunOutlined />}
          </button>
        </div>
      </aside>

      {/* 移动端汉堡按钮 + 抽屉 */}
      <button className="sidebar-mobile-btn" onClick={() => setMobileOpen(true)}>
        <MenuOutlined />
      </button>
      <Drawer
        placement="left"
        open={mobileOpen}
        onClose={() => setMobileOpen(false)}
        width={220}
        className="sidebar-drawer"
        closable={false}
      >
        <div className="sidebar-drawer-inner">
          <Link to="/" className="sidebar-logo" onClick={() => setMobileOpen(false)}>
            <ThunderboltOutlined className="sidebar-logo-icon" />
            <span className="sidebar-logo-text">FlowCapital</span>
          </Link>
          {renderMenu(() => setMobileOpen(false))}
          <div className="sidebar-footer">
            <span
              className={`sidebar-dot ${online ? 'online' : 'offline'}`}
              title={online ? 'API 已连接' : 'API 连接断开'}
            />
            <button className="sidebar-theme-btn" onClick={onToggleTheme} title={isLight ? '夜间模式' : '日间模式'}>
              {isLight ? <MoonOutlined /> : <SunOutlined />}
            </button>
          </div>
        </div>
      </Drawer>
    </>
  );
};

export default Sidebar;
