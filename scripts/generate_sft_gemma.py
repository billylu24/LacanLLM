import os
import json
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from huggingface_hub import login
from tqdm import tqdm

# ================= 1. 路径配置 =================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
INPUT_FILE = os.path.join(PROJECT_ROOT, "data", "lacan_dataset.jsonl")
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "data", "lacan_sft_pairs.jsonl")

# ================= 2. 参数配置 =================
MODEL_ID = "google/gemma-3-4b-it"
HF_TOKEN = "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"  # 你的 Key

# --- 策略控制 ---
TEST_MODE = False  # 【重要】True=只跑前10条测试; False=跑全量数据
MIN_TEXT_LEN = 80  # 太短的文本（少于80字）不要，可能是垃圾数据
MAX_INPUT_TOKENS = 2048  # 限制输入长度，防止爆显存 (4B模型处理2k token很轻松)
MAX_NEW_TOKENS = 150  # 生成的问题长度 (问题通常1-2句话，150 token足够了)

# 登录 HF
login(token=HF_TOKEN)


# ================= 3. 加载模型 =================
def load_model():
    print(f"⚙️ 正在加载模型: {MODEL_ID}")
    try:
        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, token=HF_TOKEN)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.bfloat16,
            device_map="cuda",
            token=HF_TOKEN,
            trust_remote_code=True
        )
        return model, tokenizer
    except Exception as e:
        print(f"❌ 加载失败: {e}")
        exit()


# ================= 4. 生成逻辑 =================
def generate_question(model, tokenizer, lacan_text):
    user_content = f"""
You are an expert on Jacques Lacan.
I will provide a text excerpt (Output) from Lacan.
Please reverse-engineer the specific QUESTION (Instruction) that prompted this response.

Rules:
1. The question must be in English.
2. Keep it short (1-2 sentences).
3. Only output the question text.

Lacan's Text:
"{lacan_text}"
"""
    messages = [{"role": "user", "content": user_content}]

    # 应用聊天模板
    text_input = tokenizer.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True
    )

    # 【关键修改】动态截断，防止输入太长爆显存
    inputs = tokenizer(
        text_input,
        return_tensors="pt",
        max_length=MAX_INPUT_TOKENS,  # 限制最大输入长度
        truncation=True  # 超过就截断
    ).to("cuda")

    try:
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,  # 输出长度限制
                temperature=0.7,
                do_sample=True,
                pad_token_id=tokenizer.eos_token_id
            )

        # 解码
        generated_ids = outputs[0][len(inputs['input_ids'][0]):]
        response = tokenizer.decode(generated_ids, skip_special_tokens=True)

        # 清洗
        question = response.replace('"', '').strip()
        for prefix in ["The question is:", "Here is the question:", "Question:"]:
            if question.startswith(prefix):
                question = question.replace(prefix, "").strip()

        return question

    except Exception as e:
        return None


# ================= 5. 主程序 (带统计功能) =================
def main():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ 找不到文件: {INPUT_FILE}")
        return

    print("📊 正在扫描数据集统计信息...")
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        raw_lines = f.readlines()

    total_raw = len(raw_lines)

    # 预处理：筛选出有效行
    valid_data = []
    for line in raw_lines:
        try:
            d = json.loads(line)
            text = d.get("text", "")
            if len(text) >= MIN_TEXT_LEN:
                valid_data.append(text)
        except:
            continue

    total_valid = len(valid_data)

    print(f"   - 原始行数: {total_raw}")
    print(f"   - 有效行数 (长度>={MIN_TEXT_LEN}): {total_valid}")
    print(f"   - 过滤掉: {total_raw - total_valid} 行")

    # 决定要跑多少条
    if TEST_MODE:
        print("\n⚠️ 【测试模式】只处理前 10 条数据。")
        target_data = valid_data[:10]
    else:
        print(f"\n🚀 【全量模式】准备处理所有 {total_valid} 条数据。")
        target_data = valid_data

    # 加载模型
    model, tokenizer = load_model()

    processed_count = 0

    # 开始跑
    print(f"💾 结果将追加保存到: {OUTPUT_FILE}")

    with open(OUTPUT_FILE, 'a', encoding='utf-8') as f_out:
        # 使用 tqdm 显示进度
        for text in tqdm(target_data, desc="Generating"):

            question = generate_question(model, tokenizer, text)

            if question and len(question) > 5:
                entry = {
                    "instruction": question,
                    "input": "",
                    "output": text
                }
                f_out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f_out.flush()
                processed_count += 1

    print(f"\n🎉 全部完成！")
    print(f"   - 成功生成: {processed_count} 条")
    print(f"   - 可以在 {OUTPUT_FILE} 查看结果")


if __name__ == "__main__":
    main()