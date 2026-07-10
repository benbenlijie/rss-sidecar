# RSS 翻译 + 知识图谱 Sidecar — 设计方案

## 一、产品定位

**一句话**：给 FreshRSS/Miniflux 等 RSS 后端加上 AI 翻译和知识图谱的 sidecar 服务。

**形态**：sidecar（兼容层），不是完整阅读器。用户不换客户端（继续用 Reeder/NetNewsWire），不换后端（继续用 FreshRSS/Miniflux）。sidecar 自动发现后端订阅，翻译+增强后回写为增强 RSS。

**核心价值**：
1. 翻译（沉浸式翻译体验，双语对照）
2. 知识图谱记忆库（用 graphify 构建，跨文章关联注入）

**目标用户**：泛多语言信息消费者（不只是开发者）
**商业模式**：纯开源
**资源约束**：一人项目

## 二、竞争分析（实时 star 数据）

### 传统巨头（用户基数大，无 AI/翻译）
| 项目 | Stars | AI/翻译 | Agent |
|------|------:|---------|-------|
| FreshRSS | 15.0k | 无 | 无 |
| Miniflux | 9.5k | 无 | 无 |
| NewsBlur | 7.5k | 有 Ask AI | 有 MCP |

### 翻译/聚合赛道（直接竞争）
| 项目 | Stars | 定位 |
|------|------:|------|
| RSSBox (versun) | 673 | 翻译管道，双语对照，前 RSS-Translator |
| Oksskolten | 441 | AI 原生阅读器+MCP，2026.3 发布，增长快 |
| RSSbrew | 288 | 聚合+摘要+digest |

### 小众项目
- Dibao（邸报）: 139, Precis: 93, Aurora RSS: 92, Newscope: 44
- 纯 MCP 工具（12+个）: 全部 <40 stars

### 市场缺口
"翻译 + Agent + 完整阅读体验 + 开源 + 生态兼容" 没有绝对王者。Oksskolten 最接近但才 2 个月大，RSSBox 翻译强但只是管道不是阅读器。

## 三、战略选择

### 主战场：翻译深度（沉浸式翻译）
- 翻译记忆库（自动一致性，跨文章术语统一）
- 双语对照
- 多引擎路由（DeepL/OpenAI/Ollama，每源可选）
- 格式保真（代码块、LaTeX、表格不破坏）

### 差异化杀手锏：知识图谱记忆库（用 graphify）
- 每读一篇文章 → graphify 提取实体/关系 → 更新个人知识图谱
- 新文章到达时 → 查询图谱找关联 → 在译文中注入"你读过的相关文章"和"关联洞察"
- 护城河：用户的个人知识图谱无法迁移，越用越离不开

### 获客通道：生态兼容
- FreshRSS sidecar 集成（自动发现订阅）
- Miniflux 集成（二期）
- 输出标准 RSS（任何阅读器可订阅）

## 四、技术栈

**语言：Python 3.11+**（因为 graphify 是 Python，直接 import）

| 层 | 选择 | 理由 |
|----|------|------|
| RSS 解析 | feedparser | 行业标准 |
| 全文提取 | trafilatura | 比 readability 好，专为 Web 文章 |
| HTTP | httpx | 异步 |
| LLM 翻译 | openai SDK（兼容所有 OpenAI 接口）, anthropic SDK | |
| NMT 翻译 | deep-translator | Google/DeepL/百度 |
| 本地模型 | ollama | 零成本 |
| 知识图谱 | graphify | 直接 import，基于 networkx |
| Embedding | sentence-transformers 或 OpenAI API | |
| Web 框架 | FastAPI | 异步 |
| 任务调度 | APScheduler | 轻量，单进程内 |
| 配置 | pydantic-settings | |
| 主数据库 | SQLite | 单文件，零运维 |
| 向量存储 | sqlite-vec | SQLite 扩展，不引入额外服务 |
| 图谱文件 | graph.json | graphify 原生格式 |
| 部署 | Docker + docker-compose | 和 FreshRSS 同一个 compose |

### 关键选择理由
- SQLite 而非 PostgreSQL：一人项目运维成本，树莓派也跑得动
- sqlite-vec 而非 ChromaDB：不引入额外服务，部署还是一个容器
- APScheduler 而非 Celery：定时抓取不需要分布式队列
- trafilatura 而非 readability-lxml：专为 Web 文章设计

## 五、架构：两层管线

### 关键设计：graphify 的语义提取需要 LLM（Claude），不是实时的。但 RSS 翻译需要及时输出。所以分两层：

```
实时层（快速管线）
  RSS源 → feedparser → trafilatura(全文) → Translator(翻译+记忆库) → 输出增强RSS
  延迟目标: 文章发布后 <10分钟出翻译版

知识层（慢速管线）
  文章存入 ./raw/ → graphify --update(定时,每1-6小时) → 更新graph.json → 关联查询 → 注入器(给RSS追加"相关文章"区块)
```

关联注入是回溯的：文章先出翻译版（实时层），图谱更新后给同一篇文章的 RSS 内容追加关联区块（知识层）。

### graphify 集成方式
```python
# 1. 摄取文章到图谱
from graphify.ingest import ingest
ingest(article_url, Path('./raw'), author=article.author)

# 2. 更新图谱（增量）
subprocess.run(['graphify', '--update', './raw'])

# 3. 查询关联（实时）
from networkx.readwrite import json_graph
G = json_graph.node_link_graph(json.loads(Path('graphify-out/graph.json').read_text()))
for entity in new_article_entities:
    neighbors = list(G.neighbors(entity))
```

graphify 自带 MCP 服务器（`python -m graphify.serve`），AI agent 可直接查询图谱。

## 六、功能路线图

```
MVP（4-6周）
├── feedparser 抓取 + trafilatura 全文
├── 单翻译引擎（OpenAI 兼容接口）
├── SQLite 存文章+译文
├── 输出标准 RSS feed
├── FreshRSS sidecar 集成
└── Docker 部署

V1（+4周）
├── 翻译记忆库（自动一致性）
├── graphify 集成（摄取 + 图谱更新）
├── 关联注入（"你读过的相关文章"）
└── 多翻译引擎（Ollama 本地模式）

V2（+4周）
├── graphify MCP 暴露（agent 查询）
├── 惊喜发现（surprising_connections 注入）
├── embedding 相似（补充 graphify 语义关联）
└── Miniflux 集成
```

## 七、平台风险评估

### FreshRSS 内置翻译的风险
- FreshRSS 是 PHP，AI/翻译生态在 Python/TS/Go，技术栈不对
- FreshRSS 定位"轻量、快、标准"，加 AI 会变重，违背项目哲学
- FreshRSS 有扩展系统，AI 功能以扩展存在多年，从未被吸收进核心
- 历史证据：全文提取这种基础功能都没内置

### 防御策略
1. 多后端兼容（不只 FreshRSS，还 Miniflux/TT-RSS）
2. 演进到透明代理形态（站在客户端和后端之间）
3. 数据护城河（翻译记忆库 + 个人知识图谱）

## 八、翻译记忆库设计（sidecar 无 UI 场景）

传统翻译记忆库靠"用户纠正"，但 sidecar 无界面。调整方案：
- 核心价值从"用户纠正"变为"自动一致性"：相同术语跨文章保持同一译法
- 术语表（"GPT 保留原文"）用 YAML 配置文件维护，极少改动
- 不需要 UI，纯靠积累

## 九、关键约束和风险

1. 一人项目：不能铺开做，必须极聚焦
2. graphify 语义提取需要 Claude API（有成本）
3. 知识图谱价值滞后：用户头一周用感觉不到（记忆库空），需要一个月才显出价值
4. sidecar 控制内容不控制呈现（沉浸式翻译的"段落对照"受限于客户端渲染能力）
5. 当前不做阅读界面，只输出 RSS
