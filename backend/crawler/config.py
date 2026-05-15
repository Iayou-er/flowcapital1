"""爬虫常量配置：超时、并发、分类映射、选择器"""
import re

# 预编译正则
SKIP_PATTERN = re.compile(
    r'(nav|menu|sidebar|footer|comment|share|related|ad[_-]|banner|toolbar|'
    r'recommend|subscribe|login|register|breadcrumb|pager|page[_-]?nav|hot[_-]|rank[_-])', re.I
)

ARTICLE_CLASS_PATTERN = re.compile(
    r'^(article[-_]?body|article[-_]?content|news[-_]?content|post[-_]?content|'
    r'story[-_]?content|detail[-_]?content|content[-_]?detail|article-box)', re.I
)
ARTICLE_ID_PATTERN = re.compile(r'^(article|content|newsContent|detail|arcBody|storyBody)', re.I)
ARTICLE_CLASS_SIMPLE = re.compile(r'^(article|news-content|post-content|detail|story|content-wrap)', re.I)

# 域名 → 正文选择器
DOMAIN_SELECTORS = {
    'sina.com.cn': [
        {'name': 'div', 'id': 'artibody'},
        {'name': 'div', 'class': 'article'},
        {'name': 'div', 'class': 'main-article'},
    ],
    'eastmoney.com': [
        {'name': 'div', 'class': 'Body'},
        {'name': 'div', 'id': 'ContentBody'},
        {'name': 'div', 'class': 'newsContent'},
    ],
    '10jqka.com.cn': [
        {'name': 'div', 'class': 'txtContent'},
        {'name': 'div', 'class': 'detail-txt'},
        {'name': 'div', 'class': 'article-content'},
    ],
    'yicai.com': [
        {'name': 'div', 'class': 'article-detail'},
        {'name': 'div', 'class': 'm-detail-content'},
    ],
    'cls.cn': [
        {'name': 'div', 'class': 'detail-content'},
        {'name': 'div', 'class': 'article-detail'},
    ],
    'wallstreetcn.com': [
        {'name': 'div', 'class': 'article__content'},
        {'name': 'div', 'class': 'live-item-detail'},
    ],
    '163.com': [
        {'name': 'div', 'id': 'endText'},
        {'name': 'div', 'class': 'post_body'},
    ],
    'finance.sina.com.cn': [
        {'name': 'div', 'id': 'artibody'},
        {'name': 'div', 'class': 'article'},
    ],
    'caixin.com': [
        {'name': 'div', 'id': 'Main_Content_Val'},
        {'name': 'div', 'class': 'article-content'},
        {'name': 'div', 'class': 'textbox'},
    ],
    'jiemian.com': [
        {'name': 'div', 'class': 'article-content'},
        {'name': 'div', 'class': 'article-main'},
        {'name': 'div', 'class': 'main-content'},
    ],
    'tmtpost.com': [
        {'name': 'div', 'class': 'article-body'},
        {'name': 'div', 'class': 'post-content'},
        {'name': 'div', 'class': 'inner'},
    ],
    '36kr.com': [
        {'name': 'div', 'class': 'article-body'},
        {'name': 'div', 'class': 'common-width'},
    ],
}

GENERIC_SELECTORS = [
    {'name': 'div', 'class': ARTICLE_CLASS_PATTERN},
    {'name': 'article'},
    {'name': 'main'},
    {'name': 'div', 'id': ARTICLE_ID_PATTERN},
    {'name': 'div', 'class': ARTICLE_CLASS_SIMPLE},
]

# 分类正则
CATEGORY_PATTERNS = {}
for _cat, _keywords in {
    '宏观经济': ['宏观', '经济', 'GDP', 'CPI', 'PPI', '央行', '统计局', '财政', '货币', '降息', '加息', '通胀', '通缩'],
    '股市动态': ['A股', '沪指', '深成指', '创业板', '沪深', '大盘', '涨停', '跌停', '板块', '牛市', '熊市', '指数'],
    '债券市场': ['债券', '国债', '信用债', '城投债', '企业债', '违约', '评级'],
    '外汇交易': ['汇率', '美元', '欧元', '日元', '人民币', '外汇', '美联储', 'Fed'],
    '期货市场': ['期货', '原油', '黄金', '铜', '铁矿石', '大豆', '农产品'],
    '公司财报': ['财报', '业绩', '利润', '营收', '净利润', '年报', '季报', '公告', '披露'],
    '政策法规': ['政策', '法规', '监管', '证监会', '国务院', '改革', '意见', '通知'],
    '行业分析': ['行业', '赛道', '产业链', '新能源', '半导体', '医药', '消费', '科技'],
    '国际市场': ['美股', '港股', '纳指', '标普', '日经', '欧洲', '全球', '海外', '国际'],
    '投资策略': ['策略', '研报', '机构', '基金', '配置', '布局', '加仓', '减持', '估值'],
}.items():
    CATEGORY_PATTERNS[_cat] = re.compile('|'.join(re.escape(kw) for kw in _keywords), re.I)

# 爬取常量
CRAWL_TIMEOUT = 180
DEFAULT_DELAY = 0.3

# 快/慢源分类
FAST_SOURCES = [
    "jinshi", "wallstreetcn", "ths_api", "cls_api", "xueqiu", "eastmoney_api",
    "rss_xinhua", "rss_caixin", "rss_ce", "rss_huxiu", "rss_sspai", "rss_guancha",
    "36kr", "akshare",
]
SLOW_SOURCES = [
    "eastmoney_web", "sina_finance", "wechat_sogou", "tmtpost",
    "caixin", "jiemian", "netease_finance", "yicai", "10jqka", "baidu_finance",
]
ALL_SOURCES = FAST_SOURCES + SLOW_SOURCES
