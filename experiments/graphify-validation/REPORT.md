# Graphify 验证实验报告

## 实验设置
- 语料: 8 篇真实 RSS 文章（AI/LLM 主题）
- 提取: LLM agent → 60 节点、119 边
- 分析: graphify build → cluster → analyze

## 结果: ✅ 通过

### 聚类（13 个有意义的集群）
- Transformer 谱系 (11 成员): Google, Attention, LSTM, RNN, ViT, AlphaFold, Gemini
- OpenAI 生态 (10 成员): GPT-4, ChatGPT, BARD, LLaMA, Meta, Mistral
- 对齐方法 (7 成员): RLHF, DPO, fine-tuning, alignment
- Constitutional AI (5 成员): Claude, Constitution, Principles, Values
- AI 安全阶段 (4 成员): pre-training, post-training, inference

### 跨文章关联推荐
从 1 篇文章出发，通过共享实体能找到 7/8 篇相关文章。
推荐带有"共享概念列表"，解释为什么相关。

### 核心指标
- 60 节点, 95 边, 13 聚类
- 30% 实体出现在多篇文章中
- God nodes: Transformer(15), Claude(11), GPT-4(11)

## 文件说明
- `extraction.json` — LLM 提取的实体/关系
- `raw/` — 8 篇原始文章 (markdown)
- `graphify-out/graph.json` — 知识图谱数据
- `graphify-out/graph.html` — 可交互可视化（浏览器打开）

## 如何查看
```bash
# 方式 1: 直接打开 HTML
xdg-open graphify-out/graph.html

# 方式 2: 启动本地服务器
python3 -m http.server 8080
# 然后访问 http://localhost:8080/graphify-out/graph.html
```
