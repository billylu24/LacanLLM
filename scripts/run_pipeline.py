"""
run_pipeline.py

这个脚本做项目的第 3 步：准备训练集，并用 QLoRA 微调一个 Gemma 模型。

整体流程：
1. 读取 generate_sft_gemma.py 生成的 data/lacan_sft_pairs.jsonl
2. 过滤过短、过长、质量较差的样本
3. 拆分 train / validation
4. 加载基础模型，例如 google/gemma-4-E4B-it
5. 使用 4-bit QLoRA 训练 LoRA adapter
6. 保存 adapter 和训练元数据

几个初学者需要先理解的概念：

- Fine-tuning:
  在已有大模型基础上继续训练，让它更适合某个任务或领域。

- LoRA:
  Low-Rank Adaptation。它不会更新整个大模型的所有参数，而是在模型部分线性层旁边加一小组可训练参数。
  好处是显存占用低、训练快、输出文件小。

- QLoRA:
  Quantized LoRA。把基础模型以 4-bit 量化方式加载，再训练 LoRA。
  你的 RTX 5070 约 12GB 显存，不适合全量微调 8B 模型，但适合尝试 4-bit QLoRA。

- Adapter:
  LoRA 训练出来的小权重文件。推理时需要“基础模型 + adapter”一起使用。
"""

import argparse
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
from datasets import Dataset, load_dataset
from peft import LoraConfig, TaskType, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoProcessor,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)


# 项目根目录，保证路径不依赖当前 shell 所在目录。
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# generate_sft_gemma.py 的输出：一行一个 instruction/output 样本。
RAW_DATA_FILE = PROJECT_ROOT / "data" / "lacan_sft_pairs.jsonl"

# 本脚本准备出来的训练集和验证集。
TRAINING_DATA_FILE = PROJECT_ROOT / "data" / "lacan_training_data.jsonl"
VALIDATION_DATA_FILE = PROJECT_ROOT / "data" / "lacan_validation_data.jsonl"

# 最终 LoRA adapter 保存目录。
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "adapters" / "lacan_lora"

# Trainer checkpoint 保存目录。中途断掉时可以从这里续训。
DEFAULT_CHECKPOINT_DIR = PROJECT_ROOT / "outputs"

# 默认基础模型。Gemma 4 E4B 是当前更适合这个项目的默认选择。
DEFAULT_MODEL_ID = "google/gemma-4-E4B-it"

# 单条样本最大 token 长度。2048 对 12GB 显存更稳。
# 如果你想保留更长上下文，可以升到 4096，但显存压力会明显变大。
DEFAULT_MAX_SEQ_LENGTH = 2048

# 最多选多少条训练样本。不是越多越好，低质量样本会伤害训练。
DEFAULT_TARGET_DATA_COUNT = 30000

# 验证集比例。0.05 表示约 5% 数据用于验证，不参与训练。
DEFAULT_VAL_RATIO = 0.05

# 随机种子。固定 seed 可以让打乱和拆分结果更可复现。
DEFAULT_SEED = 3407

# output 是 assistant 要学习回答的内容，也就是拉康段落。
# 太短没有信息量，太长会被截断。
DEFAULT_MIN_OUTPUT_CHARS = 120
DEFAULT_MAX_OUTPUT_CHARS = 6000

# 质量打分会偏好接近这个长度的回答。
# 1200 字符大约是数百 token，对 2048 max_seq_length 比较健康。
DEFAULT_PREFERRED_OUTPUT_CHARS = 1200

# 过滤明显是模型拒答或无效输出的样本。
BAD_PHRASES = (
    "i cannot answer",
    "i am an ai",
    "as an ai",
    "text provided does not",
    "context is missing",
    "sorry",
    "cannot provide",
)

# LoRA 会挂在哪些模块上。
# q/k/v/o 是 attention 的关键投影层。
# gate/up/down 是 MLP 前馈层。
# 这些是 LLaMA/Gemma 系模型常见的 LoRA target modules。
DEFAULT_LORA_TARGET_MODULES = (
    "q_proj",
    "k_proj",
    "v_proj",
    "o_proj",
    "gate_proj",
    "up_proj",
    "down_proj",
)


def require_hf_token() -> str:
    """
    从环境变量读取 Hugging Face token。

    为什么需要 token：
        Gemma 模型通常需要你在 Hugging Face 上接受 license，并用 token 下载。

    PowerShell 设置方式：
        $env:HF_TOKEN = "your_token_here"
    """

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN in the environment before loading gated HF models.")
    return token


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """
    读取 JSONL 文件。

    JSONL 格式：
        每一行都是一个独立 JSON 对象。

    返回:
        list[dict]: 所有能成功解析的行。
    """

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # 遇到坏行直接跳过，避免整个训练准备中断。
                continue
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    """
    写 JSONL 文件。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output_file:
        for row in rows:
            output_file.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_quality_score(entry: dict[str, Any], preferred_output_chars: int) -> int:
    """
    给一个训练样本打粗略质量分。

    注意：这不是严格学术指标，只是训练前筛选样本的启发式规则。

    设计思路：
    - 回答长度接近 preferred_output_chars 更好
    - instruction 里有问号更像真实问题
    - 有 source_file 说明可追踪来源
    - 有 mojibake_score 说明可能有编码损坏，扣分
    """

    instruction = entry["instruction"]
    output = entry["output"]
    output_len = len(output)

    # 越接近 preferred_output_chars，分数越高。
    score = 1000 - abs(output_len - preferred_output_chars)

    # 像问题的 instruction 加分。
    score += 50 if "?" in instruction else 0

    # 有来源信息加分，方便后续验证和论文记录。
    score += 25 if entry.get("source_file") else 0

    # 编码风险扣分。
    score -= 250 if entry.get("mojibake_score", 0) else 0

    return score


def prepare_training_data(
    raw_file: Path,
    train_file: Path,
    validation_file: Path,
    target_count: int,
    val_ratio: float,
    seed: int,
    min_output_chars: int,
    max_output_chars: int,
    preferred_output_chars: int,
) -> None:
    """
    从原始 SFT pairs 准备 train/validation 数据。

    这个函数只处理数据，不加载模型。
    """

    if not raw_file.exists():
        raise FileNotFoundError(raw_file)

    rows = []
    for entry in read_jsonl(raw_file):
        instruction = entry.get("instruction", "").strip()
        output = entry.get("output", "").strip()

        # instruction 太短，往往不是有效问题。
        if len(instruction) < 10 or len(output) < min_output_chars:
            continue

        # output 太长会在训练 tokenization 阶段被截断，浪费训练。
        if len(output) > max_output_chars:
            continue

        # 过滤明显的拒答/垃圾输出。
        if any(phrase in output.lower() for phrase in BAD_PHRASES):
            continue

        # 写回 strip 后的干净版本。
        entry["instruction"] = instruction
        entry["output"] = output

        # 临时分数只用于排序，写文件前会删除。
        entry["_score"] = row_quality_score(entry, preferred_output_chars)
        rows.append(entry)

    if not rows:
        raise RuntimeError("No usable SFT rows found after filtering.")

    # 先按质量分从高到低排序，再截取 target_count 条。
    rows.sort(key=lambda item: item["_score"], reverse=True)
    selected = rows[:target_count]

    rng = random.Random(seed)

    # 优先按 source_file 拆验证集。
    # 这样同一个源文件的段落不会同时大量出现在 train 和 validation，
    # 可以减少数据泄漏，让 validation 更可信。
    source_files = sorted({row.get("source_file") for row in selected if row.get("source_file")})
    validation_sources = set(rng.sample(source_files, max(1, round(len(source_files) * val_ratio)))) if source_files else set()

    train_rows = []
    validation_rows = []
    for row in selected:
        row.pop("_score", None)
        if row.get("source_file") in validation_sources:
            validation_rows.append(row)
        else:
            train_rows.append(row)

    # 如果没有 source_file，或者 validation 为空，就退回到普通随机拆分。
    if not validation_rows:
        rng.shuffle(selected)
        split_at = max(1, round(len(selected) * val_ratio))
        validation_rows = selected[:split_at]
        train_rows = selected[split_at:]

    # 打乱顺序，避免训练时先看到同一类样本。
    rng.shuffle(train_rows)
    rng.shuffle(validation_rows)

    write_jsonl(train_file, train_rows)
    write_jsonl(validation_file, validation_rows)
    print_data_summary(train_rows, validation_rows, train_file, validation_file)


def print_data_summary(
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    train_file: Path,
    validation_file: Path,
) -> None:
    """
    打印数据准备结果，帮助你判断数据长度是否合理。
    """

    lengths = [len(row["instruction"]) + len(row["output"]) for row in train_rows + validation_rows]
    print(f"Train rows: {len(train_rows)} -> {train_file}")
    print(f"Validation rows: {len(validation_rows)} -> {validation_file}")
    print(f"Average chars: {float(np.mean(lengths)):.0f}")
    print(f"Max chars: {float(np.max(lengths)):.0f}")

    # 粗略估算 token 数。英文里 1 token 大约 3-4 个字符，这里用 3.5。
    print(f"Estimated avg tokens: {float(np.mean(lengths)) / 3.5:.0f}")
    print(f"Estimated max tokens: {float(np.max(lengths)) / 3.5:.0f}")


def load_text_processor(model_id: str, token: str):
    """
    加载 tokenizer/processor。

    Gemma 4 模型卡推荐 AutoProcessor，因为 Gemma 4 支持多模态。
    但本项目目前只训练文本，所以最终仍需要拿到 processor.tokenizer。

    如果某个模型没有 AutoProcessor，就退回 AutoTokenizer。
    """

    try:
        processor = AutoProcessor.from_pretrained(model_id, token=token)
        tokenizer = getattr(processor, "tokenizer", processor)
        return processor, tokenizer
    except Exception:
        tokenizer = AutoTokenizer.from_pretrained(model_id, token=token)
        return tokenizer, tokenizer


def apply_chat_template(processor, tokenizer, instruction: str, output: str) -> str:
    """
    把 instruction/output 转成模型聊天训练格式。

    例子概念上类似：
        <user> instruction
        <assistant> output

    不同模型的真实 special token 不同，所以不要手写模板；
    应该用 tokenizer/processor 自带的 apply_chat_template。
    """

    messages = [
        {"role": "user", "content": instruction},
        {"role": "assistant", "content": output},
    ]

    template_kwargs = {
        "tokenize": False,
        "add_generation_prompt": False,
    }

    try:
        # Gemma 4 支持 enable_thinking。训练普通回答时先关掉 thinking。
        return processor.apply_chat_template(messages, enable_thinking=False, **template_kwargs)
    except TypeError:
        # 旧模型或普通 tokenizer 可能不接受 enable_thinking 参数。
        return tokenizer.apply_chat_template(messages, **template_kwargs)


def tokenize_dataset(dataset: Dataset, processor, tokenizer, max_seq_length: int) -> Dataset:
    """
    把字符串数据集转成 token id 数据集。

    训练模型不直接读文字，而是读 input_ids 这样的数字序列。
    """

    def format_and_tokenize(example):
        text = apply_chat_template(processor, tokenizer, example["instruction"], example["output"])
        return tokenizer(
            text,

            # 超过 max_seq_length 的样本会被截断。
            truncation=True,
            max_length=max_seq_length,

            # 不在这里 padding，让 data_collator 在 batch 级别动态 padding。
            padding=False,
        )

    return dataset.map(format_and_tokenize, remove_columns=dataset.column_names)


def find_latest_checkpoint(checkpoint_dir: Path) -> str | None:
    """
    在 outputs/ 下找最新 checkpoint。

    Trainer 保存的目录一般叫：
        checkpoint-500
        checkpoint-1000

    数字越大，训练步数越靠后。
    """

    if not checkpoint_dir.exists():
        return None
    checkpoints = [path for path in checkpoint_dir.glob("checkpoint-*") if path.is_dir()]
    if not checkpoints:
        return None
    return str(max(checkpoints, key=lambda path: int(path.name.split("-")[-1])))


def save_training_metadata(args: argparse.Namespace, output_dir: Path, train_count: int, validation_count: int) -> None:
    """
    保存训练元数据。

    这对预印本/论文很重要，因为你需要记录：
    - 用了哪个基础模型
    - 训练集多少条
    - LoRA 超参数是多少
    - 学习率是多少
    - CUDA/GPU 环境是什么
    """

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_id": args.model_id,
        "max_seq_length": args.max_seq_length,
        "train_rows": train_count,
        "validation_rows": validation_count,
        "lora_r": args.lora_r,
        "lora_alpha": args.lora_alpha,
        "lora_dropout": args.lora_dropout,
        "learning_rate": args.learning_rate,
        "num_train_epochs": args.num_train_epochs,
        "per_device_train_batch_size": args.per_device_train_batch_size,
        "gradient_accumulation_steps": args.gradient_accumulation_steps,
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "cuda_runtime": torch.version.cuda,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def train(args: argparse.Namespace) -> None:
    """
    真正执行 QLoRA 训练。
    """

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for LoRA training.")

    token = require_hf_token()
    processor, tokenizer = load_text_processor(args.model_id, token)

    # 右侧 padding 更适合 causal LM 训练。
    tokenizer.padding_side = "right"

    # 有些模型没有 pad_token，就用 eos_token 代替。
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # 4-bit 量化配置，这是 QLoRA 的 Q。
    quantization_config = BitsAndBytesConfig(
        # 以 4-bit 加载基础模型，显著降低显存占用。
        load_in_4bit=True,

        # 计算时使用 bf16 或 fp16。RTX 5070 支持 bf16，所以通常会选 bf16。
        bnb_4bit_compute_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,

        # nf4 是 QLoRA 论文中常用的 4-bit quantization 类型，适合正态分布权重。
        bnb_4bit_quant_type="nf4",

        # double quantization 会进一步压缩量化参数，节省显存。
        bnb_4bit_use_double_quant=True,
    )

    model = AutoModelForCausalLM.from_pretrained(
        args.model_id,
        token=token,

        # device_map="auto" 自动把模型放到 GPU。
        device_map="auto",

        # 使用上面的 4-bit 配置。
        quantization_config=quantization_config,

        # sdpa 是 PyTorch 内置 attention 实现，通常稳定且省显存。
        attn_implementation=args.attn_implementation,
    )

    # 训练时关闭 cache，因为 gradient checkpointing 和 cache 通常不兼容。
    model.config.use_cache = False

    # 对 4-bit 模型做 PEFT/QLoRA 训练前准备。
    model = prepare_model_for_kbit_training(model)

    # LoRA 超参数配置。
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,

        # r 是 LoRA rank。越大，可训练容量越强，但显存/计算也越高。
        # 16 是常用起点；如果效果不够且显存够，可以试 32。
        r=args.lora_r,

        # alpha 是 LoRA 缩放系数。常见经验是 alpha = 2 * r。
        # 当前默认 r=16, alpha=32。
        lora_alpha=args.lora_alpha,

        # dropout 给 LoRA 分支加随机丢弃，减少过拟合。
        # 数据量较大时 0.05 是温和选择。
        lora_dropout=args.lora_dropout,

        # bias="none" 表示不训练 bias，减少参数量，也更常见。
        bias="none",

        # 指定把 LoRA 插入哪些模块。
        target_modules=list(DEFAULT_LORA_TARGET_MODULES),
    )

    # 把 LoRA adapter 挂到基础模型上。
    model = get_peft_model(model, lora_config)

    # 打印可训练参数比例。LoRA 通常只训练很小一部分参数。
    model.print_trainable_parameters()

    # 读取训练集和验证集。
    train_dataset = load_dataset("json", data_files=str(args.training_file), split="train")
    eval_dataset = load_dataset("json", data_files=str(args.validation_file), split="train")

    # 转换成 token id。
    train_dataset = tokenize_dataset(train_dataset, processor, tokenizer, args.max_seq_length)
    eval_dataset = tokenize_dataset(eval_dataset, processor, tokenizer, args.max_seq_length)

    # Hugging Face Trainer 的训练参数。
    training_args = TrainingArguments(
        # checkpoint 输出目录。
        output_dir=str(args.checkpoint_dir),

        # 每张 GPU 每步喂几个样本。12GB 显存下 1 最稳。
        per_device_train_batch_size=args.per_device_train_batch_size,
        per_device_eval_batch_size=args.per_device_eval_batch_size,

        # 梯度累积步数。
        # 有效 batch size = per_device_train_batch_size * gradient_accumulation_steps。
        # 默认 1 * 8 = 8。这样显存占用仍像 batch=1，但优化器每 8 步更新一次。
        gradient_accumulation_steps=args.gradient_accumulation_steps,

        # 训练轮数。1 epoch 表示完整看一遍训练集。
        num_train_epochs=args.num_train_epochs,

        # 如果 max_steps > 0，会覆盖 epoch 设置，用固定 step 数训练。
        # 调试时可以用 --max-steps 20 快速跑通。
        max_steps=args.max_steps,

        # 学习率。LoRA 常见范围大约 1e-4 到 3e-4。
        # 2e-4 是 QLoRA 常用起点。
        learning_rate=args.learning_rate,

        # warmup_ratio 表示前 3% step 逐渐升高学习率，避免训练一开始震荡。
        warmup_ratio=args.warmup_ratio,

        # 每多少 step 打印一次 loss。
        logging_steps=args.logging_steps,

        # 每多少 step 在验证集上评估一次。
        eval_strategy="steps",
        eval_steps=args.eval_steps,

        # 每多少 step 保存一次 checkpoint。
        save_strategy="steps",
        save_steps=args.save_steps,

        # 最多保留几个 checkpoint，避免磁盘爆满。
        save_total_limit=args.save_total_limit,

        # 根据 GPU 能力选择 bf16 或 fp16。
        bf16=torch.cuda.is_bf16_supported(),
        fp16=not torch.cuda.is_bf16_supported(),

        # gradient checkpointing 用计算换显存，适合显存紧张的训练。
        gradient_checkpointing=True,

        # bitsandbytes 的 8-bit AdamW，节省优化器状态显存。
        optim=args.optim,

        # weight_decay 是权重衰减，轻微正则化，减少过拟合。
        weight_decay=args.weight_decay,

        # cosine 学习率调度：学习率逐渐按余弦曲线下降。
        lr_scheduler_type=args.lr_scheduler_type,

        # 随机种子，保证结果更可复现。
        seed=args.seed,
        data_seed=args.seed,

        # report_to="none" 表示不连接 wandb/tensorboard 等外部记录器。
        report_to=args.report_to,
        run_name=args.run_name,

        # 因为我们自己控制 dataset 字段，关闭自动删除未使用列的行为更稳。
        remove_unused_columns=False,
    )

    # Causal LM 训练用的数据整理器。
    # 它会把 batch 里的样本 padding 到同一长度，并创建 labels。
    data_collator = DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False)

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        data_collator=data_collator,
    )

    # 续训逻辑：
    # - 如果用户传 --resume-from-checkpoint，就从指定 checkpoint 继续。
    # - 如果用户传 --auto-resume，就自动找 outputs/ 里最新 checkpoint。
    resume_checkpoint = args.resume_from_checkpoint
    if args.auto_resume and resume_checkpoint is None:
        resume_checkpoint = find_latest_checkpoint(args.checkpoint_dir)
        if resume_checkpoint:
            print(f"Auto-resuming from {resume_checkpoint}")

    trainer.train(resume_from_checkpoint=resume_checkpoint)

    # 训练结束后跑一次最终验证集评估。
    metrics = trainer.evaluate()
    print(metrics)

    # 保存 LoRA adapter 和 tokenizer。
    args.output_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)

    # 保存训练元数据，方便写论文和复现实验。
    save_training_metadata(args, args.output_dir, len(train_dataset), len(eval_dataset))
    print(f"Saved LoRA adapter to {args.output_dir}")


def parse_args() -> argparse.Namespace:
    """
    定义所有命令行参数。

    你可以通过 --help 查看完整列表：
        python scripts/run_pipeline.py --help
    """

    parser = argparse.ArgumentParser(description="Prepare Lacan SFT data and train a PEFT QLoRA adapter.")

    # 模型和文件路径。
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--raw-data-file", type=Path, default=RAW_DATA_FILE)
    parser.add_argument("--training-file", type=Path, default=TRAINING_DATA_FILE)
    parser.add_argument("--validation-file", type=Path, default=VALIDATION_DATA_FILE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--checkpoint-dir", type=Path, default=DEFAULT_CHECKPOINT_DIR)

    # 流程控制。
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--skip-prepare", action="store_true")

    # 数据筛选超参数。
    parser.add_argument("--target-count", type=int, default=DEFAULT_TARGET_DATA_COUNT)
    parser.add_argument("--val-ratio", type=float, default=DEFAULT_VAL_RATIO)
    parser.add_argument("--min-output-chars", type=int, default=DEFAULT_MIN_OUTPUT_CHARS)
    parser.add_argument("--max-output-chars", type=int, default=DEFAULT_MAX_OUTPUT_CHARS)
    parser.add_argument("--preferred-output-chars", type=int, default=DEFAULT_PREFERRED_OUTPUT_CHARS)
    parser.add_argument("--max-seq-length", type=int, default=DEFAULT_MAX_SEQ_LENGTH)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)

    # 训练超参数。
    parser.add_argument("--num-train-epochs", type=float, default=1.0)
    parser.add_argument("--max-steps", type=int, default=-1)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--warmup-ratio", type=float, default=0.03)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--lr-scheduler-type", default="cosine")
    parser.add_argument("--per-device-train-batch-size", type=int, default=1)
    parser.add_argument("--per-device-eval-batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=8)

    # LoRA 超参数。
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)

    # 优化器和 attention 实现。
    parser.add_argument("--optim", default="paged_adamw_8bit")
    parser.add_argument("--attn-implementation", default="sdpa")

    # 日志、评估、保存频率。
    parser.add_argument("--logging-steps", type=int, default=10)
    parser.add_argument("--eval-steps", type=int, default=100)
    parser.add_argument("--save-steps", type=int, default=500)
    parser.add_argument("--save-total-limit", type=int, default=2)

    # 断点续训。
    parser.add_argument("--auto-resume", action="store_true")
    parser.add_argument("--resume-from-checkpoint", default=None)

    # 外部实验记录。默认 none，不上传到 wandb 等服务。
    parser.add_argument("--report-to", default="none")
    parser.add_argument("--run-name", default="lacan-lora")
    return parser.parse_args()


def main() -> None:
    """
    主入口。

    默认会先准备数据，再训练。
    如果只想准备数据：
        python scripts/run_pipeline.py --prepare-only

    如果已经准备过数据，只想训练：
        python scripts/run_pipeline.py --skip-prepare
    """

    args = parse_args()

    if not args.skip_prepare:
        prepare_training_data(
            raw_file=args.raw_data_file,
            train_file=args.training_file,
            validation_file=args.validation_file,
            target_count=args.target_count,
            val_ratio=args.val_ratio,
            seed=args.seed,
            min_output_chars=args.min_output_chars,
            max_output_chars=args.max_output_chars,
            preferred_output_chars=args.preferred_output_chars,
        )

    if not args.prepare_only:
        train(args)


if __name__ == "__main__":
    main()
