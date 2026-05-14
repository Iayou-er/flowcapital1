# 财经情感词典规范

## 现状问题

SnowNLP 使用通用中文词典，对财经领域特有表述不敏感。例如：

- "公司回购股份" → SnowNLP 倾向中性，实际为利好
- "股东减持套现" → SnowNLP 可能判断不准，实际为利空

导致情感分析结果与实际市场解读偏差。

## 目标

- 在不替换 SnowNLP 的前提下叠加财经领域词典
- 正面/负面词直接加减分权重，覆盖常见财经语境
- 处理否定前缀（"并非利好""算不上突破"），避免误判
- 前后兼容，词典可逐步扩充

## 实施方案

### 1. 词典文件

新建 `backend/analyzer/finance_sentiment_dict.py`：

```python
# 财经情感词典
# 格式: {词: 权重}，正数正面，负数负面，范围 [-1.0, 1.0]
# 注意: 长词必须排在短词前面，避免子串误匹配（如 "不及预期" 必须在 "超预期" 之前）

POSITIVE_WORDS = [
    # 利好事件（长→短排序，防止子串误匹配）
    ('业绩预增', 0.7), ('订单增长', 0.5), ('技术突破', 0.5), ('政策利好', 0.6),
    ('营收增长', 0.6), ('净利增长', 0.7), ('毛利率提升', 0.5), ('现金流改善', 0.5),
    ('ROE提升', 0.5), ('资产负债率下降', 0.4), ('产能释放', 0.4),
    ('买入评级', 0.6), ('超出预期', 0.8), ('超预期', 0.8),
    ('回购', 0.7), ('增持', 0.8), ('分红', 0.6), ('涨停', 0.7),
    ('突破', 0.4), ('新高', 0.6), ('扭亏', 0.7), ('中标', 0.5), ('获批', 0.5),
    ('补贴', 0.4), ('减免', 0.4), ('扩张', 0.3), ('并购', 0.3), ('上市', 0.3),
    ('加仓', 0.5), ('看多', 0.6),
    ('放水', 0.3), ('降息', 0.4), ('降准', 0.5), ('宽松', 0.3),
]

NEGATIVE_WORDS = [
    # 利空事件（长→短排序）
    ('资产负债率上升', -0.4), ('不及预期', -0.7), ('重组失败', -0.7), ('立案调查', -0.8),
    ('卖出评级', -0.6), ('业绩预亏', -0.7), ('营收下滑', -0.6), ('净利下滑', -0.6),
    ('毛利率下降', -0.5), ('现金流恶化', -0.6), ('ROE下降', -0.4),
    ('商誉减值', -0.7), ('订单下滑', -0.5), ('产能过剩', -0.5),
    ('减持', -0.7), ('套现', -0.7), ('爆雷', -0.9), ('跌停', -0.7), ('退市', -0.9),
    ('亏损', -0.6), ('下滑', -0.5), ('腰斩', -0.8), ('崩盘', -0.9), ('踩雷', -0.7),
    ('违约', -0.8), ('破产', -0.9),
    ('处罚', -0.6), ('罚款', -0.5), ('诉讼', -0.5), ('冻结', -0.6), ('查封', -0.7),
    ('做空', -0.5), ('看空', -0.5),
    ('裁员', -0.6), ('关停', -0.6),
    # 宏观利空
    ('加息', -0.4), ('收紧', -0.4), ('通胀', -0.3), ('衰退', -0.6), ('危机', -0.5),
    ('贸易战', -0.6), ('制裁', -0.5), ('脱钩', -0.4),
]

# 程度修饰词（调节相邻情感词权重，需在匹配到情感词后向前查找）
MODIFIERS = {
    '大幅': 1.5, '显著': 1.3, '明显': 1.2, '略微': 0.5, '小幅': 0.5,
    '持续': 1.2, '连续': 1.1, '首次': 1.1, '再次': 1.0,
}

# 否定前缀（出现于情感词前 5 字符内时反转极性）
NEGATION_PREFIXES = {'并非', '不算', '算不上', '谈不上', '未必', '不算是', '没有', '不是', '绝非'}
```

### 2. 子串安全匹配（含否定处理）

核心原则：按词长降序排列，先匹配长词，匹配成功后标记该文本区间为已占用，短词不再在该区间内匹配。同时检测情感词前方是否有否定前缀。

```python
def _match_sentiment_words(text: str, word_list: list) -> list:
    """
    按词长降序匹配，已匹配区间加锁防止子串冲突。
    返回 [(word, weight, position), ...]
    """
    hits = []
    occupied = set()  # 已匹配字符位置集合

    # 按词长降序
    for word, weight in sorted(word_list, key=lambda x: -len(x[0])):
        start = 0
        while True:
            pos = text.find(word, start)
            if pos == -1:
                break
            # 检查是否与已匹配区间重叠
            positions = set(range(pos, pos + len(word)))
            if not positions & occupied:
                occupied |= positions
                hits.append((word, weight, pos))
            start = pos + 1

    return hits
```

### 3. 情感计算叠加

修改 `backend/analyzer/sentiment.py`，在 `SentimentAnalyzer` 中新增融合方法：

```python
from backend.analyzer.finance_sentiment_dict import (
    POSITIVE_WORDS, NEGATIVE_WORDS, MODIFIERS, NEGATION_PREFIXES
)

def analyze_sentiment(self, text: str) -> dict:
    """保持原签名不变，内部调用融合逻辑"""
    if not text or not text.strip():
        return self._empty_result()

    if HAS_SNOWNLP:
        result = self._snownlp_analysis(text)
    else:
        result = self._dict_analysis(text)

    # 叠加财经词典修正
    return self._apply_finance_dict(text, result)


def _apply_finance_dict(self, text: str, base_result: dict) -> dict:
    """在 SnowNLP/词典 结果之上叠加财经词典"""
    finance_score = 0.0
    hit_words = []

    # 正/负词匹配（长词优先，避免子串冲突）
    all_hits = []
    all_hits.extend(_match_sentiment_words(text, POSITIVE_WORDS))
    all_hits.extend(_match_sentiment_words(text, NEGATIVE_WORDS))

    for word, weight, pos in all_hits:
        # 查找前文否定前缀（10 字符窗口内，中文否定词可能距情感词较远）
        prefix = text[max(0, pos - 10):pos]
        negated = any(neg in prefix for neg in NEGATION_PREFIXES)
        # 同时检查是否有否定词紧邻（如"不构成利好"中"不"紧邻"利好"但不在前缀集合中）
        if not negated and pos > 0 and text[pos - 1] == '不':
            negated = True

        # 查找前文程度修饰词（20 字符窗口内）
        modifier = 1.0
        prefix_20 = text[max(0, pos - 20):pos]
        for mod_word, mod_weight in sorted(MODIFIERS.items(), key=lambda x: -len(x[0])):
            if mod_word in prefix_20:
                modifier = mod_weight
                break

        effective = (-weight if negated else weight) * modifier
        finance_score += effective
        neg_label = '(否定反转)' if negated else ''
        hit_words.append(f'{effective:+.1f}:{word}{neg_label}(x{modifier})')

    # 融合权重: SnowNLP 0.6 + 财经词典 0.4
    base_score = base_result.get('sentiment_score', 0.0)
    clipped_finance = max(-1.0, min(1.0, finance_score))
    final_score = round(base_score * 0.6 + clipped_finance * 0.4, 4)
    # 确保最终分数在 [-1, 1] 范围内
    final_score = max(-1.0, min(1.0, final_score))

    # 标签判定（复用现有逻辑：>0.15 positive, <-0.15 negative）
    if final_score > 0.15:
        label = 'positive'
    elif final_score < -0.15:
        label = 'negative'
    else:
        label = 'neutral'

    return {
        **base_result,
        'sentiment_score': final_score,
        'sentiment_label': label,
        'finance_hits': hit_words,
    }
```

### 4. 词典热更新（运行时重载）

词典文件修改后无需重启服务，通过 API 触发重载：

```python
# backend/analyzer/finance_sentiment_dict.py 末尾
import importlib

def reload_dict():
    """热更新：重新导入词典模块"""
    import backend.analyzer.finance_sentiment_dict as mod
    importlib.reload(mod)
    # 更新全局引用
    global POSITIVE_WORDS, NEGATIVE_WORDS, MODIFIERS, NEGATION_PREFIXES
    POSITIVE_WORDS = mod.POSITIVE_WORDS
    NEGATIVE_WORDS = mod.NEGATIVE_WORDS
    MODIFIERS = mod.MODIFIERS
    NEGATION_PREFIXES = mod.NEGATION_PREFIXES
```

暴露 API 端点：

```python
# backend/api/routes/analysis.py
@router.post("/api/analysis/reload-dict")
async def reload_finance_dict():
    from backend.analyzer.finance_sentiment_dict import reload_dict
    reload_dict()
    return {'code': 0, 'message': '词典已重载'}
```

### 5. 融合权重

| 组件 | 权重 | 说明 |
|------|------|------|
| SnowNLP | 0.6 | 通用情感基线 |
| 财经词典 | 0.4 | 财经语境修正（含否定反转，已 clip 到 [-1, 1]） |

### 6. 已知局限

| 局限 | 说明 | 缓解措施 |
|------|------|----------|
| 否定窗口有限 | 10 字符窗口外的前置否定词无法检测。如"市场普遍认为并非如某些分析师所说的重大利好"中"并非"距"利好"14 字符 | 加"不"字紧邻检测覆盖部分场景；长句否定建议依赖 SnowNLP 通用模型补位 |
| 多 worker 热更新 | `importlib.reload()` 仅影响处理 reload API 请求的那个 uvicorn worker，其他 worker 仍用旧词典 | 生产环境使用单 worker 或重启所有 worker；个人/小项目单 worker 影响可忽略 |
| 上下文盲区 | "公司回购用于员工持股" vs "公司回购注销" 词典无法区分 | 属 NLP 语义理解范畴，超出词典方案能力边界 |

## 文件变更清单

| 文件 | 变更 |
|------|------|
| `backend/analyzer/finance_sentiment_dict.py` | **新建**，词典数据 + `NEGATION_PREFIXES` + `reload_dict()` |
| `backend/analyzer/sentiment.py` | `analyze_sentiment()` 调用 `_apply_finance_dict()`；新增 `_apply_finance_dict()` 和 `_match_sentiment_words()` |
| `backend/api/routes/analysis.py` | 新增 `/api/analysis/reload-dict` |

## 验收标准

- [ ] "公司宣布回购10亿股份" → 正面得分 > 0.5
- [ ] "大股东减持套现5亿" → 负面得分 < -0.3
- [ ] "业绩不及预期" 不会因包含"超预期"而被判为正面
- [ ] "并非如市场预期的利好" → 不会因"利好"被判为正面（否定反转生效）
- [ ] 财经词典命中词记录在 `finance_hits` 中（含否定标记）
- [ ] 无财经关键词时结果与纯 SnowNLP 无显著偏离
- [ ] 修改词典文件后调用 reload API，新词立即生效
