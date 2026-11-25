# AI 相关性筛选与分类规则

规则集中维护在 `config/rules.json`，代码在 `ainews/classify.py`。不调用任何模型或 API，**只看 RSS 标题和简介，不读全文**。

## 第一步：判断是否与 AI 相关

**匹配方式**
- 英文和拉丁字母词按词边界匹配：前后不能是英文字母或数字，大小写不敏感。词尾带 `*` 表示前缀匹配，例如 `model*` 能匹配 models。
- 中文词按子串匹配。中英混合的词（如“AI芯片”）在英文那一端也检查词边界。
- 独立缩写 `AI` 区分大小写，正则为 `(?<![A-Za-z0-9])AI(?![A-Za-z0-9])`：
  - “用AI写”、“AI 公司”能命中。
  - said、Thailand、Dubai、Mainland 不会命中。
  - 所以不会因为字母 ai 出现就收录。

**计分**

| 信号 | 标题 | 简介 |
|---|---|---|
| 核心术语/行业实体（人工智能、大模型、LLM、OpenAI、Claude、DeepSeek…） | 每个 3 分 | 每个 2 分 |
| 独立 `AI` 缩写 | 3 分 | 1 分 |
| 芯片算力实体（Nvidia、英伟达、台积电、黄仁勋…） | 每个 2 分 | 每个 1 分 |
| 语境词（芯片、GPU、训练、模型、参数…） | 每个 1 分，标题和简介合计最多 2 分 | （同左） |

**判定**
1. 标题出现体育、娱乐或社会语境词（足球、明星、电影、NBA、World Cup…），且标题里没有核心术语 → **排除**。
   - 典型例子是“用 AI 写作的体育新闻”。命中“AI写作 / AI-generated”这类表述时，会写进排除原因。
2. 得分 ≥ 3，且命中核心术语或标题里有 AI → **收录**，进入第二步分类。
3. 得分 ≥ 2，且有任一 AI 信号 → **可能相关**，归入“待确认”。
4. 其余 → **排除**。只计入运行统计，不写入日报。

## 第二步：分类（固定四类）

| 分类 | 含义 | 典型词 |
|---|---|---|
| 模型与研究 | 模型发布与能力、论文、基准评测、训练、开源权重 | 模型、论文、benchmark、training、weights |
| 产品与工具 | AI 产品、功能、应用、Agent 及应用、开发者工具 | 上线、推出、app、feature、agent、API |
| 行业与政策 | 融资并购、芯片算力、政策监管、诉讼版权、人事、市场 | 融资、raises、IPO、芯片、算力、监管、lawsuit |
| 待确认 | 相关但分类不清，或相关性较弱 | — |

**计分规则**
- 标题命中一个分类词记 2 分，简介命中记 1 分。
- 最高分唯一且 ≥ 2 → 归入该类。
- 并列或分数不足 → “待确认”。
- 每条都会保存 `category_reason`（命中的信号和分类依据），在日报里显示。

## 示例

`config/rules.json` 的 `examples` 字段里有示例，由 `tests/test_pipeline.py::TestClassify::test_rule_examples` 自动校验：

| 标题 | 结果 |
|---|---|
| OpenAI 发布新一代推理模型，基准成绩大幅提升 | 模型与研究 |
| Anthropic launches Claude app for Windows with new agent features | 产品与工具 |
| AI chip startup raises $200 million in Series C funding | 行业与政策 |
| 欧盟就人工智能法案执行细则征求意见 | 行业与政策 |
| AI 观察：本周值得关注的几件事 | 待确认（相关但分类不清） |
| Jensen Huang says Nvidia demand remains strong | 待确认（芯片实体，相关性弱） |
| 某球队用AI写作生成赛后新闻稿，球迷热议比赛结果 | 排除 |
| Thailand said the festival drew record crowds | 排除 |

## 已知局限

- 只看标题和简介，可能误判。例如简介很短的中文标题党文章，常被归入“待确认”。
- 两个来源都是 AI 专栏或 AI 媒体，排除规则主要用来过滤偶发的非 AI 条目；测试数据覆盖了综合新闻场景。
- 调整规则只需修改 `config/rules.json`，然后运行测试确认示例仍然通过。
