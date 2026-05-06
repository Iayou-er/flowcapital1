import React, { Suspense, lazy } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Spin } from 'antd';
import Sidebar from './components/Sidebar';
import Home from './pages/Home';
import NewsDetail from './pages/NewsDetail';
import Category from './pages/Category';
import ParticleCanvas from './components/ParticleCanvas';
import ScrollToTop from './components/ScrollToTop';
import { ThemeProvider, useTheme } from './context/ThemeContext';
import './styles/index.css';

const Analysis = lazy(() => import('./pages/Analysis'));
const Media = lazy(() => import('./pages/Media'));
const GuestBook = lazy(() => import('./pages/GuestBook'));
const About = lazy(() => import('./pages/About'));

const PageLoader = () => (
  <div style={{ display: 'flex', justifyContent: 'center', padding: '80px 0' }}>
    <Spin size="large" />
  </div>
);

function AppContent() {
  const { isLight, toggleTheme } = useTheme();
  return (
    <Router>
      <div className="App">
        <ParticleCanvas />
        <div className="mouse-glow" />
        <Sidebar isLight={isLight} onToggleTheme={toggleTheme} />
        <div className="content-layer">
          <ScrollToTop />
          <main className="main-content">
            <Suspense fallback={<PageLoader />}>
              <Routes>
                <Route path="/" element={<Home />} />
                <Route path="/news/:id" element={<NewsDetail />} />
                <Route path="/category" element={<Category />} />
                <Route path="/category/:category" element={<Category />} />
                <Route path="/analysis" element={<Analysis />} />
                <Route path="/media" element={<Media />} />
                <Route path="/guestbook" element={<GuestBook />} />
                <Route path="/about" element={<About />} />
              </Routes>
            </Suspense>
          </main>
        </div>
      </div>
    </Router>
  );
}

function App() {
  return (
    <ThemeProvider>
      <AppContent />
    </ThemeProvider>
  );
}

export default App;
