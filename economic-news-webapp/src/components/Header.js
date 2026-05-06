import React, { useState, useEffect } from 'react';
import { Layout, Menu, Drawer, Button } from 'antd';
import { Link, useLocation } from 'react-router-dom';
import { MenuOutlined, ThunderboltOutlined, SunOutlined, MoonOutlined } from '@ant-design/icons';
import { isApiOnline, onConnectionChange } from '../services/api';
import './Header.css';

const { Header: AntHeader } = Layout;

const menuItems = [
  { key: 'home', label: '首页', path: '/' },
  { key: 'category', label: '分类', path: '/category' },
  { key: 'media', label: '自媒体', path: '/media' },
  { key: 'analysis', label: '分析', path: '/analysis' },
  { key: 'guestbook', label: '留言板', path: '/guestbook' },
  { key: 'about', label: '关于', path: '/about' },
];

const HeaderComponent = ({ isLight, onToggleTheme }) => {
  const location = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [online, setOnline] = useState(isApiOnline());

  useEffect(() => onConnectionChange(setOnline), []);

  const getSelectedKey = () => {
    const path = location.pathname;
    if (path.startsWith('/category')) return 'category';
    if (path.startsWith('/media')) return 'media';
    if (path.startsWith('/analysis')) return 'analysis';
    if (path.startsWith('/guestbook')) return 'guestbook';
    if (path.startsWith('/about')) return 'about';
    return 'home';
  };

  return (
    <AntHeader className="header">
      <div className="header-inner">
        {/* Logo */}
        <Link to="/" className="logo-link">
          <div className="logo-icon">
            <ThunderboltOutlined />
          </div>
          <span className="logo-text">FlowCapital</span>
        </Link>

        {/* 桌面菜单 */}
        <Menu theme="light" mode="horizontal" selectedKeys={[getSelectedKey()]} className="desktop-menu">
          {menuItems.map(item => (
            <Menu.Item key={item.key}>
              <Link to={item.path}>{item.label}</Link>
            </Menu.Item>
          ))}
        </Menu>

        {/* 右侧操作区 */}
        <div className="header-actions">
          {/* API 连接状态指示器 */}
          <span
            title={online ? 'API 已连接' : 'API 连接断开'}
            style={{
              display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
              background: online ? 'var(--gold)' : 'var(--signal)',
              opacity: online ? 1 : 0.7,
              marginRight: 12, transition: 'background 0.3s',
            }}
          />

          {/* 主题切换按钮 */}
          <button
            className="theme-toggle"
            onClick={onToggleTheme}
            title={isLight ? '切换为夜间模式' : '切换为日间模式'}
          >
            {isLight ? <MoonOutlined /> : <SunOutlined />}
          </button>

          {/* 汉堡按钮 */}
          <Button
            className="hamburger-btn"
            type="text"
            icon={<MenuOutlined />}
            onClick={() => setDrawerOpen(true)}
          />
        </div>

        {/* 移动端抽屉菜单 */}
        <Drawer
          title="导航"
          placement="right"
          onClose={() => setDrawerOpen(false)}
          open={drawerOpen}
          width={240}
          className="mobile-drawer"
        >
          <Menu
            theme="dark"
            mode="vertical"
            selectedKeys={[getSelectedKey()]}
            onClick={({ key }) => {
              const item = menuItems.find(i => i.key === key);
              if (item) setDrawerOpen(false);
            }}
          >
            {menuItems.map(item => (
              <Menu.Item key={item.key}>
                <Link to={item.path}>{item.label}</Link>
              </Menu.Item>
            ))}
          </Menu>
        </Drawer>
      </div>
    </AntHeader>
  );
};

export default React.memo(HeaderComponent, (prev, next) =>
  prev.isLight === next.isLight && prev.onToggleTheme === next.onToggleTheme
);
