"""
generate_sft_gemma.py

这个脚本做项目的第 2 步：把“拉康段落”变成“问答训练样本”。

为什么要生成问答？
    微调聊天模型时，常见格式是：
        user: 一个问题
        assistant: 一个回答

    但我们现在只有拉康文本段落，没有人工写好的问题。
    所以这个脚本会让一个现有的指令模型读段落，然后反推：
        “什么问题可以由这段话回答？”

    输出格式是 JSONL：
        {"instruction": 问题, "input": "", "output": 原始段落, ...}

注意：
    这类数据是 synthetic data / distillation data，意思是“模型生成的问题”。
    论文或预印本里不能把它描述成人工标注问答。
"""

import argparse
import json
import os
from pathlib import Path

import torch
from tqdm import tqdm
from transformers import AutoModelForMultimodalLM, AutoProcessor

# 项目根目录。使用绝对路径可以避免“从不同目录运行脚本导致找不到文件”的问题。
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 输入来自 clean_lacan.py 的输出。
DEFAULT_INPUT_FILE = PROJECT_ROOT / "data" / "lacan_dataset.jsonl"

# 先写入 raw 文件，再由 quality_check.py 过滤成 canonical 文件。
DEFAULT_OUTPUT_FILE = PROJECT_ROOT / "data" / "lacan_sft_pairs_raw.jsonl"

# 项目固定使用的基础模型。训练脚本必须使用同一个模型。
DEFAULT_MODEL_ID = "google/gemma-4-E2B-it"

# 太短的段落通常没有足够语义，生成的问题也会很泛。
MIN_TEXT_CHARS = 160

# 输入 token 上限。超过这个长度会截断，防止显存爆掉。
MAX_INPUT_TOKENS = 2048

# 生成问题的最大长度。问题通常很短，120 token 已经足够。
MAX_NEW_TOKENS = 120


def require_hf_token() -> str:
    """
    从环境变量读取 Hugging Face token。

    为什么不把 token 写进代码？
        1. 代码可能被 push 到 GitHub，token 会泄漏。
        2. token 属于密钥，不应该进入版本控制。

    PowerShell 设置方式：
        $env:HF_TOKEN = "your_token_here"
    """

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError("Set HF_TOKEN in the environment before loading gated HF models.")
    return token


def load_model(model_id: str, token: str):
    """
    加载用于生成问题的模型和 tokenizer。

    tokenizer:
        负责把文本变成 token id，也负责把模型输出 id 解码回文字。

    model:
    AutoModelForMultimodalLM 是 Gemma 4 使用的多模态因果语言模型。
    """

    # 这个脚本会加载 4B/8B 级别模型，没有 CUDA 会非常慢甚至不可用。
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA GPU is required for this generation script.")

    processor = AutoProcessor.from_pretrained(model_id, token=token)
    model = AutoModelForMultimodalLM.from_pretrained(
        model_id,

        # bf16 在新 NVIDIA GPU 上通常更稳定；如果 GPU 不支持 bf16，就用 fp16。
        torch_dtype=torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16,

        # device_map="auto" 让 Transformers/Accelerate 自动把模型放到 GPU。
        device_map="auto",

        # 访问 gated model 时需要 Hugging Face token。
        token=token,

        # sdpa 是 PyTorch 内置 attention 实现，通常比朴素 attention 更省显存/更快。
        attn_implementation="sdpa",
    )

    # eval() 表示推理模式，不启用 dropout，不记录训练梯度。
    model.eval()
    return model, processor


def generate_question(model, processor, lacan_text: str) -> str | None:
    """
    给定一段拉康文本，让模型生成一个英文问题。

    返回:
        str: 成功生成的问题
        None: 输出太差或不像问题时，丢弃
    """

    # 这是给模型看的指令。写得越明确，输出越稳定。
    prompt = (
        "You are preparing supervised fine-tuning data for a scholarly assistant "
        "specialized in Jacques Lacan.\n\n"
        "Given the Lacan passage below, write one concise English question that the "
        "passage could answer. Only output the question.\n\n"
        f"Passage:\n{lacan_text}"
    )

    # chat 模型一般接受“多轮对话”格式。
    # 这里我们只有一轮 user 消息。
    messages = [{"role": "user", "content": prompt}]

    # apply_chat_template 会把 messages 变成模型真正需要的聊天格式。
    # tokenize=False 表示先返回字符串，不马上转 token。
    # add_generation_prompt=True 表示在末尾加上 assistant 开始回答的提示。
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
        enable_thinking=False,
    )
    inputs = {key: value[:, -MAX_INPUT_TOKENS:] if value.ndim == 2 else value for key, value in inputs.items()}
    inputs = {key: value.to(model.device) if hasattr(value, "to") else value for key, value in inputs.items()}

    # torch.no_grad() 表示不计算梯度，节省显存和时间。
    with torch.no_grad():
        outputs = model.generate(
            **inputs,

            # 最多生成多少新 token。这里生成的是问题，不需要很长。
            max_new_tokens=MAX_NEW_TOKENS,

            # temperature 越低越保守，越高越随机。0.3 适合生成稳定短问题。
            temperature=0.3,

            # do_sample=True 表示按概率采样，而不是每步都选最高概率 token。
            do_sample=True,

            # 如果模型没有显式 pad token，用 eos token 作为 padding。
            pad_token_id=processor.tokenizer.eos_token_id,
        )

    # outputs 包含“输入 prompt + 新生成内容”。
    # 这里切掉输入部分，只保留模型新生成的问题。
    generated_ids = outputs[0][inputs["input_ids"].shape[-1] :]

    # decode 把 token id 转回字符串。
    question = processor.decode(generated_ids, skip_special_tokens=True).strip()

    # 清理常见多余引号。
    question = question.strip('"').strip()

    # 有些模型会输出 "Question: ..."，这里把前缀去掉。
    for prefix in ("Question:", "The question is:", "Here is the question:"):
        if question.lower().startswith(prefix.lower()):
            question = question[len(prefix) :].strip()

    # 简单质量过滤：太短或没有问号，就认为不是合格问题。
    if len(question) < 8 or "?" not in question:
        return None

    return question


def iter_input_rows(path: Path):
    """
    逐行读取 clean_lacan.py 生成的 JSONL。

    使用 yield 的好处：
        可以一条一条产生数据，而不是必须一次性把所有东西塞进内存。
    """

    with path.open("r", encoding="utf-8") as input_file:
        for line in input_file:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # 坏行直接跳过，不让整个任务中断。
                continue

            text = row.get("text", "").strip()
            if len(text) >= MIN_TEXT_CHARS:
                yield row, text


def count_existing_rows(path: Path) -> int:
    """
    统计输出文件里已经有多少行。

    这是断点续跑的关键：
        如果之前已经生成了 1000 条，脚本下次会从第 1001 条继续。
    """

    if not path.exists():
        return 0

    with path.open("r", encoding="utf-8") as output_file:
        return sum(1 for _ in output_file)


def main() -> None:
    """
    命令行入口。

    常用命令：
        python scripts/generate_sft_gemma.py

    小规模测试：
        python scripts/generate_sft_gemma.py --limit 10
    """

    parser = argparse.ArgumentParser(description="Generate instruction/output SFT pairs.")
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT_FILE)
    parser.add_argument("--output-file", type=Path, default=DEFAULT_OUTPUT_FILE)

    # --limit 用于测试。比如先跑 10 条，看输出质量是否正常。
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not args.input_file.exists():
        raise FileNotFoundError(args.input_file)

    # 读取所有合格段落。
    rows = list(iter_input_rows(args.input_file))

    # 断点续跑：已经写过多少行，就跳过多少条输入。
    processed = count_existing_rows(args.output_file)
    remaining = rows[processed:]

    if args.limit is not None:
        remaining = remaining[: args.limit]

    print(f"Valid passages: {len(rows)}")
    print(f"Already processed: {processed}")
    print(f"This run: {len(remaining)}")

    token = require_hf_token()
    model, processor = load_model(args.model_id, token)

    # 确保输出目录存在。
    args.output_file.parent.mkdir(parents=True, exist_ok=True)

    # 用 append 模式写入，这样中断后可以继续。
    with args.output_file.open("a", encoding="utf-8") as output:
        for row, text in tqdm(remaining, desc="Generating SFT pairs"):
            question = generate_question(model, processor, text)
            if not question:
                continue

            # instruction 是用户问题，output 是拉康文本段落。
            # source_file / paragraph_index 用于追踪来源。
            entry = {
                "schema_version": 1,
                "instruction": question,
                "input": "",
                "output": text,
                "source_file": row.get("source_file"),
                "paragraph_index": row.get("paragraph_index"),
                "char_count": len(text),
            }

            output.write(json.dumps(entry, ensure_ascii=False) + "\n")

            # flush() 立刻把缓冲区写到磁盘。
            # 好处：即使中途停止，已生成数据也更不容易丢。
            output.flush()


if __name__ == "__main__":
    main()
