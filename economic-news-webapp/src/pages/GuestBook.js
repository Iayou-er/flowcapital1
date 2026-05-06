import React, { useEffect, useState, useCallback } from 'react';
import { Input, Button, Pagination, Alert, Spin, Tag, Modal } from 'antd';
import api from '../services/api';
import './GuestBook.css';

const { TextArea } = Input;

const GUESTBOOK_SESSION_KEY = 'guestbook_session_token';
const GUESTBOOK_ID_KEY = 'guestbook_anonymous_id';

const GuestBook = () => {
  const [anonymousId, setAnonymousId] = useState(null);
  const [sessionToken, setSessionToken] = useState(null);
  const [loggedIn, setLoggedIn] = useState(false);
  const [pendingId, setPendingId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [posting, setPosting] = useState(false);
  const [content, setContent] = useState('');
  const [error, setError] = useState(null);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [modalVisible, setModalVisible] = useState(false);
  const [loginLoading, setLoginLoading] = useState(false);
  const pageSize = 15;

  const fetchMessages = useCallback(async () => {
    try {
      setLoading(true);
      const data = await api.get('/guest/messages', { params: { page, limit: pageSize } });
      const list = Array.isArray(data) ? data : [];
      setMessages(list);
      setTotal(data.total || list.length || 0);
      setError(null);
    } catch (err) {
      setError('获取留言失败: ' + err.message);
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { fetchMessages(); }, [fetchMessages]);

  useEffect(() => {
    const savedToken = localStorage.getItem(GUESTBOOK_SESSION_KEY);
    const savedId = localStorage.getItem(GUESTBOOK_ID_KEY);
    if (savedToken && savedId) {
      setSessionToken(savedToken);
      setAnonymousId(savedId);
      setLoggedIn(true);
    } else if (savedId) {
      setPendingId(savedId);
    }
  }, []);

  const handleOpenLogin = async () => {
    try {
      setError(null);
      const data = await api.get('/guest/assign-id');
      setPendingId(data.anonymous_id);
      setModalVisible(true);
    } catch (err) {
      if (err.message?.includes('Network') || err.message?.includes('网络')) {
        setError('无法连接服务器，请确认后端 API 已启动 (localhost:8001)');
      } else {
        setError('获取匿名身份失败: ' + err.message);
      }
    }
  };

  const handleLoginConfirm = async () => {
    try {
      setLoginLoading(true);
      const data = await api.post('/guest/login', { anonymous_id: pendingId });
      const token = data.session_token;
      localStorage.setItem(GUESTBOOK_SESSION_KEY, token);
      localStorage.setItem(GUESTBOOK_ID_KEY, pendingId);
      setSessionToken(token);
      setAnonymousId(pendingId);
      setLoggedIn(true);
      setModalVisible(false);
      setError(null);
    } catch (err) {
      setError('登录失败: ' + err.message);
    } finally {
      setLoginLoading(false);
    }
  };

  const handleLoginCancel = () => { setModalVisible(false); setPendingId(null); };

  const handleLogout = () => {
    localStorage.removeItem(GUESTBOOK_SESSION_KEY);
    localStorage.removeItem(GUESTBOOK_ID_KEY);
    setSessionToken(null);
    setAnonymousId(null);
    setLoggedIn(false);
    setContent('');
  };

  const handleSubmit = async () => {
    if (!content.trim() || !loggedIn || !sessionToken) return;
    try {
      setPosting(true);
      await api.post('/guest/message', { session_token: sessionToken, content: content.trim() });
      setContent('');
      setPage(1);
      fetchMessages();
    } catch (err) {
      if (err.message?.includes('会话')) {
        handleLogout();
        setError('会话已过期，请重新登录');
      } else if (err.message?.includes('Network') || err.message?.includes('网络')) {
        setError('无法连接服务器，请确认后端 API 已启动 (localhost:8001)');
      } else {
        setError('留言失败: ' + err.message);
      }
    } finally {
      setPosting(false);
    }
  };

  const formatTime = (timeStr) => {
    if (!timeStr) return '';
    return new Date(timeStr).toLocaleString('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    });
  };

  return (
    <div className="gb-page">
      {/* 头部 */}
      <div className="gb-hero">
        <h1 className="gb-title">留言板</h1>
        <p className="gb-sub">匿名身份 · 自由表达</p>
      </div>

      {error && <Alert message={error} type="error" showIcon className="gb-alert" />}

      {/* 编辑器 / 登录入口 */}
      <div className="gb-editor-area">
        {loggedIn ? (
          <div className="gb-editor">
            <div className="gb-editor-top">
              <Tag className="gb-identity-tag">{anonymousId}</Tag>
              <button className="gb-logout" onClick={handleLogout}>退出</button>
            </div>
            <TextArea
              rows={4} maxLength={2000} value={content}
              onChange={e => setContent(e.target.value)}
              placeholder="写下您的想法..."
              className="gb-input"
            />
            <div className="gb-editor-bottom">
              <span className="gb-count">{content.length}/2000</span>
              <Button type="primary" loading={posting} disabled={!content.trim()} onClick={handleSubmit}>
                发表留言
              </Button>
            </div>
          </div>
        ) : (
          <div className="gb-login-hint">
            <span>请先登录以发表留言</span>
            <Button type="primary" onClick={handleOpenLogin}>匿名登录</Button>
          </div>
        )}
      </div>

      {/* 时间线留言列表 */}
      {loading ? (
        <div className="gb-loading"><Spin size="large" /></div>
      ) : messages.length === 0 ? (
        <div className="gb-empty">暂无留言，快来发表第一条吧</div>
      ) : (
        <div className="gb-timeline">
          {messages.map((msg, i) => (
            <div className="gb-item" key={msg.id || i}>
              <div className="gb-dot" />
              <div className="gb-card">
                <div className="gb-card-head">
                  <span className="gb-card-id">{msg.anonymous_id}</span>
                  <span className="gb-card-time">{formatTime(msg.created_at)}</span>
                </div>
                <p className="gb-card-body">{msg.content}</p>
              </div>
            </div>
          ))}
        </div>
      )}

      {total > pageSize && (
        <div className="gb-pager">
          <Pagination current={page} total={total} pageSize={pageSize}
            onChange={p => setPage(p)} showTotal={t => `共 ${t} 条`} showQuickJumper />
        </div>
      )}

      {/* 登录弹窗 */}
      <Modal title="匿名用户登录" open={modalVisible} onCancel={handleLoginCancel}
        footer={[
          <Button key="cancel" onClick={handleLoginCancel}>取消</Button>,
          <Button key="confirm" type="primary" loading={loginLoading} onClick={handleLoginConfirm}>确认登录</Button>,
        ]}>
        <div style={{ textAlign: 'center', padding: '16px 0' }}>
          <p style={{ color: 'var(--dim)', marginBottom: 16 }}>系统为您分配以下匿名身份：</p>
          <Tag style={{ fontSize: 18, padding: '8px 24px', color: 'var(--gold)', background: 'var(--gold-subtle)', border: 'none' }}>
            {pendingId}
          </Tag>
          <p style={{ color: 'var(--dim)', fontSize: 13, marginTop: 16 }}>登录后即可在留言板发表观点</p>
        </div>
      </Modal>
    </div>
  );
};

export default GuestBook;
