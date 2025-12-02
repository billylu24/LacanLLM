import os
import json
import random
import torch
import numpy as np
from datasets import load_dataset
from unsloth import FastLanguageModel
from trl import SFTTrainer
from transformers import TrainingArguments, TextStreamer

# ================= 0. 全局配置 =================
# 路径配置
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW_DATA_FILE = os.path.join(PROJECT_ROOT, "data", "lacan_sft_pairs.jsonl")
PROCESSED_DATA_FILE = os.path.join(PROJECT_ROOT, "data", "lacan_training_data.jsonl")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "adapters", "lacan_gemma3_lora")

# 模型配置
MODEL_ID = "unsloth/gemma-3-4b-it-bnb-4bit"  # 4B Instruct 版本
MAX_SEQ_LENGTH = 2048  # 初始设定，后面会根据数据统计微调
TARGET_DATA_COUNT = 30000  # 也就是你想要的 3w 条
NUM_EPOCHS = 1  # 3w条数据跑1轮足够了

# 过滤关键词 (垃圾数据特征)
BAD_PHRASES = [
    "I cannot answer", "I am an AI", "text provided does not",
    "context is missing", "sorry", "cannot provide"
]


def step1_prepare_data():
    print(f"\n{'=' * 20} 步骤 1: 数据清洗与统计 {'=' * 20}")

    if not os.path.exists(RAW_DATA_FILE):
        raise FileNotFoundError(f"❌ 找不到原始数据: {RAW_DATA_FILE}")

    data = []
    lengths = []

    # 临时加载 Tokenizer 用于精确统计 Token 数 (不需要加载模型)
    print("⏳ 正在加载 Tokenizer 用于统计数据长度...")
    tokenizer = FastLanguageModel.get_chat_template(
        None, mapping={"role": "role", "content": "content", "user": "user", "assistant": "assistant"}
    )[1]  # Unsloth helper to get tokenizer
    # 注意：如果上面报错，可以用 AutoTokenizer.from_pretrained(MODEL_ID)

    with open(RAW_DATA_FILE, 'r', encoding='utf-8') as f:
        print("📊 正在扫描原始数据...")
        for line in f:
            try:
                entry = json.loads(line)
                inst = entry.get("instruction", "")
                out = entry.get("output", "")

                # --- 基础过滤 ---
                if len(inst) < 10 or len(out) < 20: continue
                if any(bp in out.lower() for bp in BAD_PHRASES): continue

                # --- 评分策略 ---
                # 优先保留回复较长、内容丰富的数据
                score = len(out)
                if len(inst) < 20: score *= 0.8  # 惩罚短问题

                entry["_score"] = score
                data.append(entry)
            except:
                continue

    # 排序并截取
    data.sort(key=lambda x: x["_score"], reverse=True)
    if len(data) > TARGET_DATA_COUNT:
        selected_data = data[:TARGET_DATA_COUNT]
        print(f"✂️ 数据裁剪: 从 {len(data)} 条中选取了最优质的 {TARGET_DATA_COUNT} 条")
    else:
        selected_data = data
        print(f"⚠️ 数据不足 {TARGET_DATA_COUNT} 条，使用全部 {len(data)} 条")

    # 打乱顺序
    random.shuffle(selected_data)

    # 移除临时分数
    for d in selected_data:
        del d["_score"]
        # 简单估算 Token (字符数/3.5) 或用 Tokenizer
        # 这里仅做简单长度统计用于打印
        lengths.append(len(d["output"]) + len(d["instruction"]))

    # 保存清洗后的数据
    with open(PROCESSED_DATA_FILE, 'w', encoding='utf-8') as f:
        for d in selected_data:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    # --- 统计报告 ---
    avg_char = np.mean(lengths)
    max_char = np.max(lengths)
    estimated_tokens = int(avg_char / 3.5)  # 粗略估算

    print(f"\n📊 [数据统计报告]")
    print(f"   - 有效数据量: {len(selected_data)}")
    print(f"   - 平均字符数: {int(avg_char)}")
    print(f"   - 估算平均 Token: ~{estimated_tokens}")
    print(f"   - 估算最大 Token: ~{int(max_char / 3.5)}")

    # 动态建议
    suggested_ctx = 2048
    if int(max_char / 3) > 2048:
        print(f"⚠️ 警告: 部分数据可能超过 2048 长度，建议训练时截断或增加 max_seq_length")

    return selected_data


def step2_train():
    print(f"\n{'=' * 20} 步骤 2: 开始微调 (LoRA) {'=' * 20}")

    # 1. 加载模型
    print(f"⚙️ 加载模型: {MODEL_ID}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_ID,
        max_seq_length=MAX_SEQ_LENGTH,
        dtype=None,
        load_in_4bit=True,
    )

    # 2. 添加 LoRA 适配器
    print("🛠️ 配置 LoRA 参数...")
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,  # 秩 (Rank): 16 是经典值，想要更强学习能力可设为 32 或 64
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
        use_rslora=False,
    )

    # 3. 准备数据格式
    # Gemma 3 需要特定的 chat 模板
    def formatting_prompts_func(examples):
        instructions = examples["instruction"]
        outputs = examples["output"]
        texts = []
        for inst, out in zip(instructions, outputs):
            # 构建对话
            messages = [
                {"role": "user", "content": inst},
                {"role": "assistant", "content": out}
            ]
            # 转换为 Gemma 输入格式
            text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
            texts.append(text)
        return {"text": texts}

    dataset = load_dataset("json", data_files=PROCESSED_DATA_FILE, split="train")
    dataset = dataset.map(formatting_prompts_func, batched=True)

    # 4. 训练参数设置
    print(f"🚀 开始训练: Epochs = {NUM_EPOCHS}")
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=MAX_SEQ_LENGTH,
        dataset_num_proc=2,
        packing=False,
        args=TrainingArguments(
            per_device_train_batch_size=2,  # 显存如果不够改成 1
            gradient_accumulation_steps=4,  # 累计梯度，模拟大 Batch Size
            warmup_steps=100,
            num_train_epochs=NUM_EPOCHS,  # 设为 1
            learning_rate=2e-4,  # 学习率
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=10,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=3407,
            output_dir="outputs",
            save_strategy="no",  # 训练完最后再保存，节省空间
        ),
    )

    trainer.train()

    # 5. 保存模型
    print(f"💾 保存适配器到: {OUTPUT_DIR}")
    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)

    # 6. 简单测试
    print("\n🧐 测试模型效果 (推理):")
    FastLanguageModel.for_inference(model)

    test_q = "What is the mirror stage?"
    messages = [{"role": "user", "content": test_q}]
    inputs = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_tensors="pt").to(
        "cuda")

    outputs = model.generate(inputs, max_new_tokens=200, use_cache=True)
    response = tokenizer.decode(outputs[0], skip_special_tokens=True)
    print("-" * 50)
    print(response)
    print("-" * 50)


if __name__ == "__main__":
    # 运行 Step 1
    step1_prepare_data()

    # 运行 Step 2
    step2_train()

    print("\n✅ 端到端流程执行完毕！")