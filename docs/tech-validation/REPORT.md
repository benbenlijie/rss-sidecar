# 技术风险验证报告

## 验证 1: trafilatura 全文提取

### 测试
- 7 个真实 URL（技术文档、博客、新闻、付费墙）
- 对比 default / markdown / xml 三种输出格式

### 结果

| 指标 | 结果 |
|------|------|
| 提取成功率 | 4/7 (57%) — 3 个 403 失败 |
| 默认输出段落 | **1 段**（段落全丢失！） |
| markdown 输出段落 | **99 段**（段落完整保留）|
| 代码块保留 | ❌ markdown 模式也不保留 |
| 表格保留 | ❌ include_tables 效果有限 |
| 标题保留 | ✅ markdown 模式保留 # 标题 |

### 关键发现
**必须用 `output_format='markdown'`**。默认纯文本模式把所有段落压成一行，无法做双语对照。

### 决策
```python
text = trafilatura.extract(html, output_format='markdown', include_tables=True, include_links=True)
```
- 代码块/表格丢失是已知限制，对"泛多语言信息消费者"目标用户可接受
- 403 失败文章用 RSS content/description fallback

---

## 验证 2: 翻译段落对齐

### 测试
- 5 段英文（Anthropic 博客）→ LLM 翻译 → 检查段落对齐

### 结果: ✅ 完美对齐

| 指标 | 结果 |
|------|------|
| 输入段落数 | 5 |
| 输出段落数 | 5 |
| 1:1 对齐 | ✅ 无合并/拆分 |
| Markdown 格式保留 | ✅ 加粗、引号完整 |
| 译文质量 | ✅ 自然流畅 |

### 样例
```
EN: **Update, Jan 21, 2026:** We've published a new version...
ZH: **更新，2026年1月21日：**我们发布了新版本的Claude宪法...
```

### 决策
段落级双语对照技术可行。翻译 prompt 加入 "preserve exact paragraph structure" 指令即可。

---

## 验证 3: FreshRSS API 对接

### 结果: ✅ 完全可行

FreshRSS 的 Google Reader 兼容 API 支持所有 sidecar 需求：

| 需求 | 端点 | 可行 |
|------|------|------|
| 发现用户订阅 | `GET /reader/api/0/subscription/list?output=json` | ✅ 返回所有 feed URL |
| 回写翻译 feed | `POST /reader/api/0/subscription/edit?ac=subscribe` | ✅ |
| 自动创建"翻译"分类 | 订阅时指定 `a=user/-/label/Translated` | ✅ 首次自动创建 |
| 标记已读 | `POST /reader/api/0/edit-tag` | ✅ |
| 获取文章内容 | `GET /reader/api/0/stream/contents/<id>` | ✅ |

### 关键发现
- **订阅操作不需要写 token**（只需要 Authorization header）→ 简化认证流程
- 认证：`POST /accounts/ClientLogin` → 获取 Auth token → 缓存
- 前提：用户需在 FreshRSS 中启用 API + 设置 API 密码

### 限制
- 无批量订阅端点（逐个调用或 OPML 导入）
- Fever API 只读，不支持订阅管理 → 不用 Fever，用 Google Reader API

---

## 总结

| 风险 | 状态 | 影响 |
|------|------|------|
| trafilatura 格式保留 | ⚠️ 部分通过 | 用 markdown 输出解决段落；代码块/表格丢失可接受 |
| 翻译段落对齐 | ✅ 通过 | 双语对照核心功能可行 |
| FreshRSS API | ✅ 通过 | sidecar 集成完全可行 |

**结论：三个技术风险都不阻塞 MVP。可以进入实现阶段。**
