# RSS 翻译 + 知识图谱 Sidecar — 设计方案 v2

> v2 修订：解决 Codex 评审 P1 问题 + graphify 验证结果 + 极简 Web 阅读页

## 一、产品定位（不变）

FreshRSS/Miniflux 的 AI 翻译 + 知识图谱 sidecar。用户不换后端、不换客户端。sidecar 自动发现订阅，翻译+增强后回写。

**两个出口**（共享同一引擎）：
1. **RSS 输出**：增强 feed，用户在 Reeder/NetNewsWire 里读
2. **极简 Web 阅读页**：段落级双语对照 + 知识图谱关联侧栏（给想要沉浸式体验的用户）

---

## 二、Codex P1 问题修复

### P1-1：RSS guid 缓存策略

**问题**：RSS 客户端按 `guid` 去重。关联注入改了 content 但 guid 不变 → 用户看不到更新。

**方案**：双 feed + 版本化 guid

```
Feed A（稳定版）:
  guid = "original-article-url"（永不变）
  content = 纯译文（一次翻译，不改）
  → 客户端缓存友好，翻译版稳定可用

Feed B（增强版）:
  guid = "original-article-url#v{content_version}"
  content_version 随关联注入递增
  content = 译文 + 双语对照 + 关联区块
  → guid 变化时客户端显示为"新条目"
  → 用户可选择订阅 A 或 B
```

**content_version 递增规则**：
- 初次翻译：v1
- 翻译修正（记忆库更新）：v2
- 关联注入（图谱更新）：v3, v4...
- 每个 feed 独立追踪版本

### P1-2：沉浸式翻译 vs 呈现控制（已解决）

**用户决策**：加极简 Web 阅读页。

**设计**（见第五节）：
- 只做文章详情页，不做订阅管理（那是 FreshRSS 的职责）
- 段落级双语对照（原文/译文交替渲染）
- 知识图谱关联侧栏
- 不和 Reeder 竞争——是 RSS 输出的补充出口

### P1-3：慢速图谱回写竞态

**问题**：翻译 feed 已发布 → 客户端缓存 → 图谱更新 → content 变了 → 客户端不刷新。

**方案**：Feed B 版本化（见 P1-1）+ 图谱注入是追加而非替换

```
翻译阶段（实时）:
  content = 译文
  guid = "url#v1"
  状态 = published

图谱注入阶段（慢速）:
  不修改已发布内容
  而是生成新版本：
  content = 译文 + 关联区块
  guid = "url#v2"（新 guid → 客户端显示为新条目）
  状态 = injected
```

关键原则：**从不修改已发布的条目，只发新版本**。

### P1-4 & P1-5：graphify 验证（已解决）

验证结果：60 节点、95 边、13 聚类。跨文章关联推荐覆盖率 7/8。"共享概念列表"差异化确认有效。

**已知工程问题**：
- edge 的 `source_file` 需改为 `source_files` 列表（支持多文章来源）
- 使用 graphify 的 `--update` 增量模式
- graph.json 读写需要原子锁

### P1-6：护城河重新定义

**Codex 正确指出**：开源+本地文件+可导出 = 迁移成本低。不应把护城河建立在"用户走不了"上。

**调整**：
- **核心护城河**：翻译质量（记忆库一致性）+ 成本可控（本地 Ollama）
- **辅助护城河**：知识图谱关联推荐质量（越用越准，但可导出）
- **不依赖**：数据锁定

### P1-7：MVP 工期修正

Codex 正确：4-6 周低估。修正为 **6-8 周**。

### P1-8：路线顺序修正

```
正确顺序（已验证）:
  MVP: 翻译管道 + RSS输出 + Web阅读页 + 状态机 + 成本控制
  V1:  翻译记忆库 → graphify 集成 → 关联注入
  V2:  MCP暴露 → Miniflux → 可观测性
```

翻译记忆库在 graphify 之前（保证翻译质量和成本可控，再叠知识图谱）。

---

## 三、P1 架构风险修复

### 状态机设计

每篇文章在 SQLite 中的生命周期：

```
┌─────────┐     ┌──────────┐     ┌───────────┐     ┌───────────┐
│ fetched │────►│ extracted│────►│ translated│────►│ published │
│  RSS抓取 │     │ 全文提取  │     │  翻译完成  │     │  RSS已输出 │
└────┬────┘     └────┬─────┘     └─────┬─────┘     └─────┬─────┘
     │               │                 │                 │
     ▼ fail          ▼ fail           ▼ fail            ▼ (图谱阶段)
┌─────────┐     ┌──────────┐     ┌───────────┐     ┌────────────┐
│fetch_err│     │extract_err│    │translate_err│   │graph_pending│
│ 重试3次  │     │ 用summary │    │ 重试2次    │    │  等待图谱   │
└─────────┘     │  兜底     │    └───────────┘    └──────┬─────┘
                └──────────┘                            ▼
                                              ┌──────────────┐
                                              │  graph_done  │
                                              │ 图谱已更新    │
                                              └──────┬───────┘
                                                     ▼
                                              ┌──────────────┐
                                              │   injected   │
                                              │ 关联已注入RSS │
                                              └──────────────┘
```

**SQLite schema**：
```sql
CREATE TABLE articles (
  id INTEGER PRIMARY KEY,
  feed_url TEXT NOT NULL,
  original_url TEXT NOT NULL,        -- canonical URL
  original_guid TEXT NOT NULL,       -- RSS item GUID
  title_orig TEXT,
  content_orig TEXT,                 -- 原文（提取后）
  title_trans TEXT,
  content_trans TEXT,                -- 译文
  content_version INTEGER DEFAULT 0, -- 版本号
  state TEXT DEFAULT 'fetched',     -- 状态机
  retry_count INTEGER DEFAULT 0,
  
  -- Translation provenance
  trans_engine TEXT,                 -- 'openai' / 'deepl' / 'ollama'
  trans_model TEXT,                  -- 'gpt-4o' / 'deepseek-v3'
  trans_prompt_version TEXT,         -- prompt 模板版本
  glossary_version TEXT,             -- 术语表版本
  
  -- Cost tracking
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0,
  
  -- Timestamps
  fetched_at TEXT,
  translated_at TEXT,
  published_at TEXT,
  graph_done_at TEXT,
  injected_at TEXT,
  
  UNIQUE(original_url)
);

CREATE INDEX idx_state ON articles(state);
CREATE INDEX idx_feed ON articles(feed_url);
```

### APScheduler 多 Worker 问题

**方案**：Docker 中强制 `--workers 1` + 文件锁双重保险

```python
# scheduler 用文件锁确保单实例
import fcntl

try:
    lock_file = open('/tmp/scheduler.lock', 'w')
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    # 另一个 worker 已在跑调度器
    scheduler = None
else:
    scheduler = AsyncIOScheduler()
    scheduler.start()
```

Docker Compose：
```yaml
command: uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

### graph.json 原子读写

```python
import tempfile, shutil, fcntl

def safe_write_graph(graph_data, path):
    """原子写入 graph.json"""
    lock_path = str(path) + '.lock'
    with open(lock_path, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # 写临时文件 → 原子替换
        with tempfile.NamedTemporaryFile(
            mode='w', dir=str(path.parent), suffix='.tmp', delete=False
        ) as tmp:
            json.dump(graph_data, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
        shutil.move(tmp.name, str(path))

def safe_read_graph(path):
    """安全读取 graph.json"""
    # 读取时复制一份，避免读到半写状态
    tmp = tempfile.NamedTemporaryFile(delete=False)
    shutil.copy2(str(path), tmp.name)
    with open(tmp.name) as f:
        data = json.load(f)
    os.unlink(tmp.name)
    return data
```

### 全文提取 Fallback 策略

```python
async def extract_full_content(rss_item):
    """三级 fallback"""
    # 1. 尝试 trafilatura 全文提取
    html = await fetch_url(rss_item.link, timeout=10)
    full_text = trafilatura.extract(
        html,
        include_tables=True,      # Codex 指出：需显式启用
        include_images=False,
        include_links=True,
        favor_recall=True,        # 偏向召回（宁可多提取）
    )
    if full_text and len(full_text) > 200:
        return full_text, 'trafilatura'
    
    # 2. fallback: RSS 自带的 content:encoded 或 description
    if rss_item.get('content_encoded'):
        return rss_item['content_encoded'], 'rss_content'
    if rss_item.get('description'):
        return rss_item['description'], 'rss_summary'
    
    # 3. 最后兜底: 只翻译标题
    return None, 'title_only'
```

**失败率预期**：约 20-30% 的文章会走 fallback（付费墙、JS 渲染、反爬）。这是常态。

### Translation Provenance

每次翻译记录完整上下文，确保可复现：

```python
translation_record = {
    'article_id': 42,
    'engine': 'openai',
    'model': 'gpt-4o-2024-08-06',
    'prompt_template': 'v1.2',        # 模板版本
    'glossary_hash': 'a3f2...',       # 术语表内容哈希
    'temperature': 0.3,
    'input_tokens': 3200,
    'output_tokens': 2800,
    'cost_usd': 0.024,
    'timestamp': '2026-07-09T12:00:00Z'
}
```

翻译结果不可复现时（模型升级、prompt 改了），可触发重翻译。

### 关联注入质量控制

```python
def should_inject_connection(article, related_article, shared_concepts):
    """决定是否注入关联"""
    # 置信度阈值
    if len(shared_concepts) < 2:
        return False  # 至少共享 2 个概念
    
    # 不注入低质量关联
    if any(c.confidence == 'AMBIGUOUS' for c in shared_concepts):
        return False
    
    # 不注入太旧的文章（用户可能不记得了）
    if days_since_read(related_article) > 90:
        return False
    
    return True
```

注入格式：
```html
<div class="knowledge-connections">
  <h3>📎 你读过的相关文章</h3>
  <ul>
    <li>
      <a href="...">《Anthropic Constitutional AI 解释》</a>
      <small>共同概念: Claude, Constitutional AI, AI safety</small>
    </li>
  </ul>
</div>
```

用户可在 Web 阅读页中隐藏/纠错某个关联。

---

## 四、成本控制模型

### 每日成本预算

```python
# 配置
DAILY_BUDGET_USD = 1.00       # 每日上限
PER_FEED_BUDGET_USD = 0.10    # 单 feed 每日上限
MAX_RETRIES = 2               # 翻译失败重试上限
MAX_ARTICLES_PER_DAY = 100    # 每日处理上限

# 成本计算
def estimate_cost(text_length_chars, engine='openai', model='gpt-4o'):
    chars_per_token = 4
    tokens = text_length_chars / chars_per_token
    if engine == 'openai' and model == 'gpt-4o':
        input_cost = tokens * 2.50 / 1_000_000   # $2.50/M input
        output_cost = tokens * 10.00 / 1_000_000  # $10/M output (假设1:1)
        return input_cost + output_cost
    return 0.01  # 估算默认
```

### 限流策略

```python
# 按 feed 限流
feed_intervals = {
    'high_frequency': 300,    # 5分钟（新闻类）
    'normal': 3600,           # 1小时（博客类）
    'low_frequency': 21600,   # 6小时（月刊类）
}

# 按 provider 退避
async def translate_with_backoff(text, engine):
    for attempt in range(MAX_RETRIES):
        try:
            return await translate(text, engine)
        except RateLimitError:
            wait = 2 ** attempt * 5  # 5s, 10s, 20s
            await asyncio.sleep(wait)
        except Exception as e:
            logger.warning(f'Translation attempt {attempt} failed: {e}')
            if attempt == MAX_RETRIES - 1:
                raise
```

### 月度成本估算

```
假设: 20 个 feed, 每个 feed 每日 3 篇新文章 = 60 篇/天
每篇: 全文 ~2000 词 ≈ 5000 chars ≈ 1250 tokens
翻译: input 1250 + output 1250 = 2500 tokens

GPT-4o: $2.50/M input + $10/M output
  每天: 60 × (1250×2.50 + 1250×10) / 1M = $0.094/天
  每月: ~$2.82

GPT-4o-mini: $0.15/M input + $0.60/M output
  每月: ~$0.14

Ollama (本地): $0（但有硬件成本+延迟）

知识图谱 (V1, graphify):
  每篇: 实体提取 ~1000 input + 500 output tokens (Claude Haiku)
  每天: 60 × 1500 tokens = 90K tokens
  Claude Haiku: $0.25/M input + $1.25/M output
  每月: ~$0.17
```

**结论**：用 GPT-4o-mini + Claude Haiku 做图谱，月成本可控制在 **$0.50 以内**。

---

## 五、极简 Web 阅读页设计

### 范围（只做这些）

- ✅ 文章详情页（段落级双语对照）
- ✅ 知识图谱关联侧栏
- ✅ 阅读进度同步（标记已读/未读）
- ❌ 不做：订阅管理（用 FreshRSS）、Feed 浏览列表（用 FreshRSS/Reeder）、搜索（V2）

### 页面结构

```
┌──────────────────────────────────────────────┐
│  [← 返回 FreshRSS]    RSS Translator Reader  │
├─────────────────────────────────┬────────────┤
│                                 │            │
│  文章标题（原文 + 译文）         │  📎 关联   │
│                                 │  阅读      │
│  ┌───────────────────────────┐  │            │
│  │ Original paragraph 1      │  │  《相关    │
│  │ 原文段落 1                 │  │  文章 A》  │
│  ├───────────────────────────┤  │  共同概念: │
│  │ 译文段落 1                 │  │  Claude,  │
│  │ Translated paragraph 1    │  │  RLHF     │
│  └───────────────────────────┘  │            │
│                                 │  《相关    │
│  ┌───────────────────────────┐  │  文章 B》  │
│  │ Original paragraph 2      │  │  共同概念: │
│  │ 原文段落 2                 │  │  安全,    │
│  ├───────────────────────────┤  │  对齐      │
│  │ 译文段落 2                 │  │            │
│  └───────────────────────────┘  │            │
│                                 │            │
│  [显示/隐藏原文] [仅译文]        │            │
│                                 │            │
├─────────────────────────────────┴────────────┤
│  💡 AI 摘要: 本文讲述了...                     │
└──────────────────────────────────────────────┘
```

### 技术实现

**极简方案**：FastAPI 直接返回 HTML（不用前端框架）

```python
@app.get("/article/{article_id}")
async def read_article(article_id: int):
    article = await get_article(article_id)
    connections = await get_connections(article_id)  # 图谱关联
    
    # 服务端渲染双语对照 HTML
    return templates.TemplateResponse("article.html", {
        "article": article,
        "bilingual_blocks": split_into_bilingual_blocks(article),
        "connections": connections,
    })
```

**双语对照渲染**：段落对齐是核心挑战

```python
def split_into_bilingual_blocks(article):
    """将原文和译文按段落对齐"""
    orig_paragraphs = article.content_orig.split('\n\n')
    trans_paragraphs = article.content_trans.split('\n\n')
    
    # 简单策略：按段落数对齐（翻译保持段落结构）
    blocks = []
    for i, orig in enumerate(orig_paragraphs):
        trans = trans_paragraphs[i] if i < len(trans_paragraphs) else ''
        blocks.append({'original': orig, 'translated': trans})
    
    return blocks
```

**翻译时保持段落结构**（prompt 工程）：
```
Translate the following text to Chinese. 
CRITICAL: Preserve the exact paragraph structure. 
Each paragraph in the input MUST correspond to exactly one paragraph in the output.
Do not merge or split paragraphs.
```

### CSS（沉浸式阅读体验）

```css
.bilingual-block {
  margin-bottom: 1.5em;
}
.bilingual-block .original {
  color: var(--text-muted);
  font-size: 0.95em;
  /* 原文默认半透明，hover 高亮 */
  opacity: 0.5;
  transition: opacity 0.2s;
}
.bilingual-block .original:hover {
  opacity: 1;
}
.bilingual-block .translated {
  color: var(--text-primary);
  font-size: 1.05em;
}

/* 仅译文模式 */
.body.translation-only .original { display: none; }
```

---

## 六、安全设计

### SSRF 防护

```python
import ipaddress, socket

BLOCKED_RANGES = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),
]

async def safe_fetch(url):
    """防止 SSRF：解析 IP，拒绝私有地址"""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise ValueError(f'Blocked: scheme {parsed.scheme}')
    
    # 解析域名到 IP
    ip = socket.gethostbyname(parsed.hostname)
    ip_obj = ipaddress.ip_address(ip)
    
    for network in BLOCKED_RANGES:
        if ip_obj in network:
            raise ValueError(f'Blocked: private IP {ip}')
    
    # 限制重定向次数
    return await httpx.get(url, follow_redirects=True, max_redirects=3, timeout=10)
```

### HTML 清洗

```python
import bleach

ALLOWED_TAGS = ['p', 'br', 'strong', 'em', 'code', 'pre', 'a', 'ul', 'ol', 'li', 'blockquote', 'h1', 'h2', 'h3']
ALLOWED_ATTRS = {'a': ['href', 'title']}

def sanitize_html(html_content):
    return bleach.clean(
        html_content,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=['https', 'mailto'],
        strip=True,
    )
```

### API Key 管理

```yaml
# config.yaml（不进 Git）
translation:
  openai:
    api_key: ${OPENAI_API_KEY}     # 环境变量注入
  deepseek:
    api_key: ${DEEPSEEK_API_KEY}
  ollama:
    base_url: http://localhost:11434

graphify:
  anthropic_api_key: ${ANTHROPIC_API_KEY}
```

Docker：
```yaml
env_file:
  - .env  # gitignored
```

---

## 七、测试策略

### Fixture 准备

```
tests/
├── fixtures/
│   ├── rss/
│   │   ├── techcrunch.xml          # 标准 RSS 2.0
│   │   ├── anthropic_atom.xml      # Atom feed
│   │   ├── malformed.xml           # 损坏的 feed
│   │   └── empty.xml               # 空 feed
│   ├── html/
│   │   ├── article_full.html       # 正常文章
│   │   ├── paywall.html            # 付费墙
│   │   ├── js_rendered.html        # JS 渲染（无内容）
│   │   └── newsletter.html         # newsletter 格式
│   └── expected/
│       ├── translation.json        # 翻译预期输出
│       └── extraction.json         # 实体提取预期
├── test_fetcher.py                 # RSS 抓取
├── test_extractor.py               # 全文提取 + fallback
├── test_translator.py              # 翻译 + provenance
├── test_state_machine.py           # 状态机 + 崩溃恢复
├── test_rss_output.py              # RSS 输出 + guid 版本
├── test_scheduler.py               # 调度器 + 文件锁
├── test_graph_integration.py       # graphify 集成
└── test_cost_control.py            # 成本上限
```

### 关键测试

```python
# 状态机崩溃恢复
async def test_crash_recovery():
    """翻译到一半崩溃，重启后能恢复"""
    article = await create_article(state='translated')
    # 模拟崩溃：不更新状态就退出
    
    # 重启服务
    await restart_service()
    
    # 验证：文章被重新处理
    article = await get_article(article.id)
    assert article.state in ('published', 'translate_err')

# guid 版本化
def test_guid_versioning():
    """关联注入后 guid 变化"""
    feed_a = generate_rss(article, version='stable')
    assert '<guid>https://example.com/article</guid>' in feed_a
    
    feed_b = generate_rss(article, version='enhanced')
    assert '<guid>https://example.com/article#v2</guid>' in feed_b

# 成本上限
async def test_daily_budget_limit():
    """超出日预算后停止翻译"""
    await set_daily_spent(DAILY_BUDGET_USD)  # 已达上限
    
    article = await fetch_new_article()
    result = await process_article(article)
    
    assert result == 'budget_exceeded'
```

---

## 八、可观测性

### 日志结构

```python
import structlog

logger = structlog.get_logger()

# 每篇文章处理日志
logger.info('article_processed',
    article_id=42,
    feed='anthropic-blog',
    state='published',
    engine='openai-gpt4o',
    cost_usd=0.024,
    duration_ms=3200,
    extraction_method='trafilatura',
)

# 每日成本汇总
logger.info('daily_cost_report',
    date='2026-07-09',
    articles_processed=58,
    articles_failed=4,
    total_cost_usd=0.82,
    budget_utilization=0.82,  # 82% of $1.00
)
```

### 健康检查端点

```python
@app.get("/health")
async def health():
    return {
        'status': 'ok',
        'queue_depth': await get_queue_depth(),
        'daily_cost': await get_daily_cost(),
        'daily_budget': DAILY_BUDGET_USD,
        'feeds_active': await count_active_feeds(),
        'last_error': await get_last_error(),
    }
```

---

## 九、修订后路线图

```
MVP（6-8周）
├── 核心：RSS 抓取 + 全文提取(含fallback) + 翻译(含provenance)
├── 状态机 + SQLite schema + 成本控制
├── RSS 输出（双 feed：稳定版+增强版，guid 版本化）
├── 极简 Web 阅读页（双语对照 + CSS）
├── FreshRSS sidecar 集成
├── Docker（--workers 1 + 文件锁）
├── 安全（SSRF + HTML清洗 + env管理）
└── 基础测试（fetcher/extractor/translator/state_machine）

V1（+6周）
├── 翻译记忆库（自动一致性 + YAML术语表）
├── graphify 集成（摄取+增量更新+原子读写）
├── 关联注入（"相关文章"+共享概念列表）
├── 多翻译引擎 + Ollama 本地模式
├── 历史导入（冷启动）
└── Web 阅读页知识图谱侧栏

V2（+4周）
├── graphify MCP 暴露（agent 查询）
├── surprising_connections 注入
├── Miniflux 集成
├── 可观测性面板
└── embedding 相似（补充图谱）
```

---

## 十、修订总结

| Codex P1 发现 | 状态 | 修复方案 |
|--------------|------|---------|
| RSS guid 缓存 | ✅ 修复 | 双 feed + 版本化 guid |
| 沉浸式翻译 vs 无UI | ✅ 修复 | 加极简 Web 阅读页 |
| 慢速图谱竞态 | ✅ 修复 | 新版本而非修改已发布 |
| graphify 质量 | ✅ 验证通过 | 8篇测试，13聚类，7/8关联覆盖 |
| graphify API | ✅ 验证可用 | Python 直接 import |
| 护城河逻辑 | ✅ 调整 | 核心=翻译质量+成本，辅助=图谱 |
| MVP 工期 | ✅ 修正 | 6-8周 |
| 路线顺序 | ✅ 修正 | 翻译记忆→图谱 |
| 状态机 | ✅ 设计 | 7状态生命周期 |
| APScheduler 多worker | ✅ 修复 | workers=1 + 文件锁 |
| graph.json 原子锁 | ✅ 设计 | fcntl + 临时文件替换 |
| 全文提取 fallback | ✅ 设计 | 三级 fallback |
| Translation provenance | ✅ 设计 | 完整上下文记录 |
| 关联注入质量控制 | ✅ 设计 | 置信度阈值+可纠错 |
| 成本预算 | ✅ 设计 | 日/Feed 预算 + 退避 |
| 限流策略 | ✅ 设计 | 按 feed/provider/失败类型 |
| 数据模型 | ✅ 设计 | 完整 SQLite schema |
| 安全 | ✅ 设计 | SSRF + HTML + Key |
| 测试 | ✅ 设计 | Fixture + 关键测试 |
| 可观测性 | ✅ 设计 | structlog + health endpoint |
