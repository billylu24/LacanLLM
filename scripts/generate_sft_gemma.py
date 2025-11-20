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

    print("📊 正在扫描数据集...")

    # 1. 读取所有原始输入
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        raw_lines = f.readlines()

    # 2. 筛选有效数据
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
    print(f"   - 总有效任务数: {total_valid}")

    # ==================【关键升级：断点检测】==================
    processed_count = 0
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, 'r', encoding='utf-8') as f_out_read:
            # 统计输出文件里已经有多少行了
            processed_count = sum(1 for _ in f_out_read)
        print(f"✅ 检测到历史记录: 已完成 {processed_count} 条")

    # 计算还需要跑多少
    if processed_count >= total_valid:
        print("🎉 所有数据已全部处理完毕！无需运行。")
        return

    # 自动跳过已经跑过的数据
    # start_index 就是我们要开始的地方
    start_index = processed_count

    # 截取剩下的任务
    remaining_data = valid_data[start_index:]
    print(f"🚀 正在启动断点续传... 本次将从第 {start_index + 1} 条开始，处理剩余 {len(remaining_data)} 条。")
    # =========================================================

    if TEST_MODE:
        print("\n⚠️ 【测试模式】只跑 10 条看看。")
        remaining_data = remaining_data[:10]

    # 加载模型
    model, tokenizer = load_model()

    current_session_count = 0

    # 追加写入
    with open(OUTPUT_FILE, 'a', encoding='utf-8') as f_out:
        # 这里的 tqdm 进度条会显示剩余的任务
        for text in tqdm(remaining_data, desc="Resuming"):

            question = generate_question(model, tokenizer, text)

            if question and len(question) > 5:
                entry = {
                    "instruction": question,
                    "input": "",
                    "output": text
                }
                f_out.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f_out.flush()
                current_session_count += 1

    print(f"\n🎉 本次运行结束！新增生成: {current_session_count} 条")



if __name__ == "__main__":
    main()