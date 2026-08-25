# LacanLLM Unsloth QLoRA 自动超参数搜索与评估计划

## 目标

在 RTX 5070 12GB 上，以 Unsloth 为正式训练后端，对 Gemma 4 E2B 进行标准 4-bit NF4 QLoRA 闭卷问答微调。量化、数据和 LoRA target modules 固定，用 16 组 Optuna TPE 搜索学习率、rank、alpha 比例、dropout、有效 batch、warmup、scheduler、weight decay 和最佳 epoch。

训练只计算 assistant 回答的 loss。Validation 和 Challenge 用于选优与安全门禁；winner 锁定后 sealed Test 只运行一次。最终产物是标准 PEFT adapter，不合并基础模型。

## Unsloth 与正确性基准

Unsloth 官方对 Gemma 4 的参考口径约为 1.5 倍速度和约 60% 更低显存，但最终结论必须来自本机实测。项目将用相同模型 revision、数据、token、label、rank、batch 和步数，对原生 PEFT 与 Unsloth 分别记录冷启动、预热、稳定 tokens/s、训练墙钟和峰值显存。

Gemma 4 共享 KV 层必须先通过正确性门禁：训练 forward 与 `use_cache=True` 的 top-1 token 一致、最大 logits 差异不超过 `1e-3`，20 step loss 有限、梯度非零，并能保存、重载 adapter 后生成回答。原生后端若门禁失败，不作为有效速度或 loss 基线。

## 固定训练设置

- 模型：`google/gemma-4-E2B-it`
- 4-bit NF4、double quant、BF16 compute
- thinking disabled，Gemma 4 非 thinking chat template
- 仅语言 attention/MLP LoRA，视觉和音频冻结
- max sequence length 192，per-device batch 1
- AdamW 8-bit、gradient clip 1.0
- 最多 4 epochs，每个 epoch 保存并计算 response-only validation loss
- seed 3407，`use_gradient_checkpointing="unsloth"`

## 16 组搜索

使用 `TPESampler(seed=3407)` 和 Hyperband pruning。前 6 组为可解释 anchor：三档学习率、rank 8/16/32 和 dropout 0/.05；后 10 组由 TPE 搜索。

搜索空间：learning rate `5e-5`–`3e-4`（log）、rank `8/16/32/64`、alpha/r `1/2`、dropout `0/.05`、有效 batch `4/8/16`、warmup `0/.03/.05/.1`、cosine/linear scheduler、weight decay `0/.01/.05`。最佳 epoch 从 1–4 的 checkpoint 中选择。

每组先在按题型固定抽样的 36 条 Validation 和 18 条 Challenge 上快筛；E4B 对 candidate 和缓存 base 输出进行 A/B 双顺序盲评。Challenge 比 base 下降超过 5 个百分点时 objective 记为 0。16 组结束后，前三名在完整 Validation 200 和 Challenge 70 上评估。

## 评估与 Test 保护

生成统一使用 greedy、temperature 0、thinking disabled、max new tokens 192。E4B 判断正确性、忠实度、覆盖度、矛盾、过度断言，以及三类 Challenge 行为。主要指标为 `(wins + 0.5 * ties) / consensus_cases`；生成成功率必须至少 99%。

辅助报告 response-only loss、各题型 rubric、矛盾率、过度断言率、词汇指标、输出长度、重复率、吞吐和显存、bootstrap 95% CI、Optuna 参数重要性。winner 未写入带配置哈希的锁定清单前，CLI 必须拒绝读取 Test；锁定后校验 Test SHA-256，只执行一次并写完成标记。

最终生成 30 条按题型分层、隐藏模型身份的人工复核表。全部自动结论继续标记为 silver，不宣称专家 gold。
