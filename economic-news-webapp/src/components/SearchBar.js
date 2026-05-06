import React, { useState, useEffect } from 'react';
import { Input, Button, Space, Dropdown, Menu } from 'antd';
import { SearchOutlined, HistoryOutlined, CloseCircleOutlined } from '@ant-design/icons';
import './SearchBar.css';

const SearchBar = ({ onSearch, placeholder = '输入关键词搜索经济新闻...', storageKey = 'newsSearchHistory' }) => {
  const [searchKeyword, setSearchKeyword] = useState('');
  const [searchHistory, setSearchHistory] = useState([]);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    const savedHistory = localStorage.getItem(storageKey);
    if (savedHistory) {
      setSearchHistory(JSON.parse(savedHistory));
    }
  }, [storageKey]);

  const saveSearchHistory = (keyword) => {
    if (!keyword.trim()) return;
    const updatedHistory = [keyword, ...searchHistory.filter(item => item !== keyword)].slice(0, 10);
    setSearchHistory(updatedHistory);
    localStorage.setItem(storageKey, JSON.stringify(updatedHistory));
  };

  const handleSearch = () => {
    if (searchKeyword.trim()) {
      onSearch(searchKeyword);
      saveSearchHistory(searchKeyword);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSearch();
    }
  };

  const handleHistoryItemClick = (keyword) => {
    setSearchKeyword(keyword);
    onSearch(keyword);
  };

  const clearSearchHistory = () => {
    setSearchHistory([]);
    localStorage.removeItem(storageKey);
  };

  const clearInput = () => {
    setSearchKeyword('');
    onSearch('');  // 触发父组件恢复全量列表
  };

  const searchHistoryMenu = (
    <Menu className="search-history-menu">
      {searchHistory.length > 0 ? (
        <>
          {searchHistory.map((keyword, index) => (
            <Menu.Item key={index} className="search-history-item" onClick={() => handleHistoryItemClick(keyword)}>
              <HistoryOutlined style={{ marginRight: 8 }} />
              {keyword}
            </Menu.Item>
          ))}
          <Menu.Divider />
          <Menu.Item onClick={clearSearchHistory} className="search-history-clear">
            清除历史记录
          </Menu.Item>
        </>
      ) : (
        <Menu.Item disabled className="search-history-empty">暂无搜索历史</Menu.Item>
      )}
    </Menu>
  );

  return (
    <div className={`search-wrapper ${focused ? 'focused' : ''}`}>
      <div className="search-container">
        <Dropdown overlay={searchHistoryMenu} trigger={['click']} placement="bottomLeft">
          <Input
            className="search-input"
            placeholder={placeholder}
            value={searchKeyword}
            onChange={(e) => setSearchKeyword(e.target.value)}
            onPressEnter={handleKeyPress}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            size="large"
            suffix={
              <Space size={4}>
                {searchKeyword && (
                  <CloseCircleOutlined className="search-clear-btn" onClick={clearInput} />
                )}
                {searchHistory.length > 0 && (
                  <HistoryOutlined className="search-history-icon" />
                )}
              </Space>
            }
            prefix={<SearchOutlined className="search-icon" />}
          />
        </Dropdown>
        <Button
          className="search-btn"
          type="primary"
          size="large"
          onClick={handleSearch}
        >
          <SearchOutlined /> 搜索
        </Button>
      </div>
    </div>
  );
};

export default React.memo(SearchBar);
