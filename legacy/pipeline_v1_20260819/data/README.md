# LacanLLM 数据构建与筛选流程

本文档记录 LacanLLM 当前数据的来源、旧版 SFT 生成方式、第一阶段评估集的筛选流程、质量约束、输出结构和已知限制。

## 1. 数据集定位

当前第一阶段产物不是新的 SFT 训练集，而是从 legacy 合成问答中恢复、筛选出的评估候选集：

- Validation：250 组问答；
- Test：250 组问答；
- 总计：500 组问答；
- 每条记录均带原始来源和证据段落；
- Validation 与 Test 按来源文件隔离；
- 当前状态为 `pending_human_review`。

这些数据应被称为：

> 带原文证据、经过规则筛选和来源隔离的 synthetic QA evaluation candidates。

在完成人工理论审核前，不能将其称为专家标注的 gold benchmark。

## 2. Legacy 原始材料

旧项目的全部代码、数据、模型、实验和输出已归档至 `legacy/`。

当前数据构建使用两个 legacy 输入：

1. `legacy/data/lacan_dataset.jsonl`
   - 51,427 个清洗后的文本段落；
   - 包含 `source_file`、`paragraph_index`、`text` 等来源信息。
2. `legacy/data/lacan_sft_pairs_checked.jsonl`
   - 46,839 个旧版合成问答候选；
   - 大部分记录缺少有效的 `source_file`，因此需要重新恢复来源。

输入文件的 SHA256 会写入构建审计报告，确保后续可以判断输入内容是否发生变化。

## 3. 旧版 SFT 是如何生成的

旧版流程如下：

```text
原始拉康文本
  → 规则清洗与段落切分
  → 将段落提交给 Gemma
  → Gemma 根据段落反推一个英文问题
  → 原始段落直接作为回答
```

生成提示词的核心逻辑为：

```text
Given the Lacan passage below, write one concise English question
that the passage could answer. Only output the question.
```

因此旧版问答在概念上是：

```json
{
  "instruction": "模型根据原文反推的问题",
  "input": "",
  "output": "原始拉康文本段落"
}
```

旧版生成脚本保存在：

```text
legacy/scripts/generate_sft_gemma.py
```

这种方式存在明显限制：

- 回答通常是长段落复制，不是针对问题组织的回答；
- 原文中的 OCR 错误会直接进入回答；
- 问题有时只覆盖段落的一部分；
- 同一段原文可能生成多个不同问题；
- 可能出现模板化问题和 meta-question；
- 问答对未经过拉康研究者的理论审核。

## 4. 第一阶段完整构建流程

第一阶段不重新调用大模型生成问答，而是对 legacy 候选进行来源恢复、规则清洗、去重和隔离拆分。

```text
legacy QA 候选（46,839）
  → 文本规范化
  → 问题/回答规则筛选
  → 从 51,427 个段落中恢复来源
  → 检查证据对回答的覆盖率
  → 问题、回答、证据三级去重
  → 按来源文件隔离 Validation/Test
  → 按问题类型均衡选择
  → 250 Validation + 250 Test
  → 自动审计 + 500 条人工复核表
```

### 4.1 文本规范化

问题、回答和证据在比较前统一执行：

- Unicode NFKC 规范化；
- 删除 BOM；
- 合并连续空格、Tab 和换行；
- 比较时统一大小写；
- 比较时忽略标点差异；
- 转换成规范化英文 token 序列。

实现：`src/lacanllm/data/text.py`。

### 4.2 问题筛选

问题必须满足：

- 长度在 35–280 字符之间；
- 以问号结尾；
- 不是明显的 meta-question；
- 不是请求用户补充信息的模板问题。

以下形式会被拒绝：

```text
What specific aspects of Lacan are you interested in exploring?
Could you please specify what you would like to know?
```

### 4.3 回答筛选

回答必须满足：

- 长度在 160–1,800 字符之间；
- 不包含拒答或占位文本；
- 不包含明显版权页、ISBN、目录和网址 boilerplate；
- 不包含已知 mojibake 字符；
- 英文字母占比不低于 55%；
- 不出现同一个 8-token 片段重复 3 次以上。

实现：`src/lacanllm/data/quality.py`。

### 4.4 来源与证据恢复

由于 legacy QA 的来源字段大量缺失，管线通过回答文本反向匹配原始段落。

匹配顺序：

1. 规范化后的完整文本精确匹配；
2. 使用前 12 个 token 定位候选段落；
3. 计算回答和候选段落的连续前缀覆盖；
4. 如果回答跨越段落边界，按同一来源连续拼接最多 4 个段落；
5. 重新计算证据对完整回答的覆盖率。

只有来源覆盖率不低于 `0.72` 的候选才允许进入后续流程。当前最终 500 条的平均来源覆盖率约为 `0.99`。

实现：`src/lacanllm/data/provenance.py`。

### 4.5 问题、回答和证据三级去重

管线分别对以下内容计算规范化 SHA256 指纹：

- question；
- answer；
- evidence。

候选按质量分和来源覆盖率排序，然后依次删除：

1. 相同回答；
2. 相同问题；
3. 相同证据段落。

这样可以防止：

- 同一原文配上不同问题后进入多个 split；
- 同一问题配上不同长段落重复出现；
- 同一证据单元被多次用于评估。

当前构建统计：

- 规则和来源筛选后：28,829 条；
- 三级去重后：25,027 条；
- 删除重复回答：3,668 条；
- 删除重复问题：40 条；
- 删除重复证据：94 条。

具体数字以 `data/reports/evaluation_v1_audit.json` 为准。

### 4.6 问题类型分类

候选问题通过透明规则划分为六类：

- `definition`：概念定义；
- `comparison`：概念区别或关系；
- `explanation`：原因、机制和功能解释；
- `clinical`：分析、患者、精神病、神经症等临床问题；
- `textual_interpretation`：研讨班、文章和文本解释；
- `other`：无法归入上述类别的问题。

实现：`src/lacanllm/data/quality.py` 中的 `classify_question()`。

### 4.7 启发式质量排序

当前质量分不是理论正确性分数，只用于通过硬性规则后的候选排序。

排序主要考虑：

- 回答长度与约 650 字符的距离；
- 问题长度与约 110 字符的距离；
- 来源匹配覆盖率；
- 是否触发质量风险。

问题类型之间使用轮流选取，避免单一问题类型占据整个评估集。

每个 split 当前的问题类型分布为：

```text
Validation: clinical 42, comparison 43, definition 54, explanation 40, other 43, textual interpretation 28
Test:       clinical 40, comparison 48, definition 48, explanation 39, other 48, textual interpretation 27
```

### 4.8 Validation/Test 来源隔离

拆分发生在来源文件级，而不是问答行级：

```text
一组完整来源文件 → Validation
另一组完整来源文件 → Test
```

约束包括：

- 同一 `source_file` 不得同时出现在 Validation 和 Test；
- 每个来源最多贡献 50 条；
- Validation 固定为 250 条；
- Test 固定为 250 条；
- 使用 seed `3407` 保证确定性；
- 最终输出顺序进行确定性打乱。

当前结果：

| Split | 问答数 | 来源数 |
|---|---:|---:|
| Validation | 250 | 5 |
| Test | 250 | 5 |
| 合计 | 500 | 10 |

另外保留 35 个完全不与评估集交叉的来源，专门用于第二阶段 SFT 训练数据生成。

实现：`src/lacanllm/data/split.py`。

## 5. 输出字段

每条问答记录包含：

```json
{
  "qa_id": "规范化问题和回答的哈希",
  "question": "问题",
  "answer": "回答",
  "question_type": "comparison",
  "source_file": "lacan_text_016.txt",
  "paragraph_index": 116,
  "evidence": "匹配到的原文证据",
  "quality_score": 0.99,
  "provenance_score": 1.0,
  "provenance_method": "exact",
  "question_fingerprint": "...",
  "answer_fingerprint": "...",
  "evidence_fingerprint": "...",
  "legacy_candidate_line": 10591,
  "dataset_version": "evaluation_v1",
  "split": "test",
  "review_status": "pending_human_review"
}
```

## 6. 构建产物

```text
data/processed/evaluation_v1/
├── validation.jsonl     # 250 条
├── test.jsonl           # 250 条
└── human_review.csv     # 500 条人工审核队列

data/reports/
└── evaluation_v1_audit.json
```

审计报告记录：

- 输入文件路径与 SHA256；
- 输入段落数量；
- 各类拒绝数量；
- 去重前后数量；
- split 大小与来源数量；
- 问题类型分布；
- 平均问题、回答长度；
- 平均来源覆盖率；
- 来源、问题、回答和证据交叉检查；
- 单一来源数量上限检查；
- 完整构建配置。

## 7. 人工复核

全部 500 条记录目前都标记为：

```json
"review_status": "pending_human_review"
```

人工审核表位于：

```text
data/processed/evaluation_v1/human_review.csv
```

建议逐条填写：

- `answerability_1_5`：问题是否能由证据回答；
- `theoretical_accuracy_1_5`：问答是否符合拉康理论；
- `evidence_fidelity_1_5`：回答是否忠实于原始证据；
- `wording_quality_1_5`：问题和回答是否自然清晰；
- `decision`：accept / revise / reject；
- `reviewer`：审核者；
- `notes`：问题与修改意见。

人工审核被拒绝的记录应从同一 split 的候选来源池中补位，不能从另一 split 的来源中补位，否则会破坏来源隔离。

## 8. 复现命令

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m lacanllm.data.build `
  --config configs\data\evaluation_v1.json
```

运行自动测试与静态检查：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
```

核心配置位于：

```text
configs/data/evaluation_v1.json
```

## 9. 自动审计保证

当前构建必须同时满足以下条件，否则构建失败：

- `target_total_met`：Validation + Test 等于 500；
- `source_isolation`：两个 split 没有共享来源；
- `question_isolation`：两个 split 没有共享问题；
- `answer_isolation`：两个 split 没有共享回答；
- `evidence_isolation`：两个 split 没有共享证据；
- `source_cap_respected`：单一来源不超过 50 条；
- `all_provenance_scores_pass`：所有来源覆盖率达到配置阈值。

对应持续测试位于：

```text
tests/test_dataset_artifacts.py
```

## 10. 已知限制

当前 500 条仍有以下限制：

1. 问题来自 legacy 模型生成，不是人工撰写；
2. 回答主要是原始段落，不是重新组织的简洁答案；
3. 自动规则无法判断复杂的拉康理论准确性；
4. 来源文件名目前是匿名编号，尚未映射到正式书目；
5. 仍可能存在规则无法识别的 OCR 拼写错误；
6. 问题类型分类是启发式规则，不是人工标签；
7. 尚未执行模型裁判或领域专家审核；
8. 这 500 条只能用于 Validation/Test，不能进入 SFT 训练集。

## 11. 第二阶段：新 SFT 训练数据

真正的新 SFT 训练数据应重新构建：

```text
有来源的清洗段落
  → 按书籍/章节预先分配 split
  → 生成问题
  → 生成针对问题组织的简洁回答
  → 保存支持回答的 evidence span
  → 规则审核
  → 独立模型审核可回答性与证据忠实度
  → 近重复检测
  → 分层人工抽检
  → 形成新的 SFT training dataset
```

新生成流程不能继续采用“模型生成问题、整段原文直接作为回答”的旧方案。

第二阶段配置位于 `configs/data/sft_v2.json`，目标是从 35 个训练专属来源准备 1,100 个候选段落，经过生成和筛选后得到 500 条训练问答。候选队列按约 45%–50% 的最终通过率留有余量；验证集和测试集仍分别保持 250 条。

执行顺序：

```powershell
# 1. 构建来源隔离的生成队列
.\.venv\Scripts\python.exe -m lacanllm.data.sft prepare `
  --config configs\data\sft_v2.json

# 2. 可恢复生成；已完成的 evidence_id 会自动跳过
.\.venv\Scripts\python.exe -m lacanllm.data.sft generate `
  --config configs\data\sft_v2.json

# 3. 自动过滤、去重并构建最终训练集
.\.venv\Scripts\python.exe -m lacanllm.data.sft filter `
  --config configs\data\sft_v2.json

# 查看长任务进度
.\.venv\Scripts\python.exe -m lacanllm.data.sft status `
  --config configs\data\sft_v2.json
```

新生成格式要求模型输出直接、独立的问题，2–5 句精炼回答，以及一段 30–500 字符的原文连续证据。中间结果逐条写入 JSONL，因此生成任务可以安全中断和续跑。

自动筛选包括：

- 问题和回答长度；
- meta-question 和依赖上下文的模糊指代；
- JSON/标签解析完整性；
- evidence quote 是否为原文连续片段；
- 回答与证据的内容词重合度；
- 长段原文直接复制；
- 8-gram 重复；
- 问题、回答和证据精确去重；
- 基于 SimHash 的问题与回答近重复检测；
- 与 Validation/Test 的来源隔离。

输出：

```text
data/interim/sft_v2/generation_queue.jsonl  # 1,100 个生成任务
data/interim/sft_v2/generations.jsonl       # 可恢复的模型原始输出
data/processed/sft_v2/train.jsonl            # 目标 500 条
data/processed/sft_v2/human_review_sample.csv
data/reports/sft_v2_audit.json
```
