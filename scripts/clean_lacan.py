import os
import re
import glob
import json

# ================= 配置区域 =================
INPUT_FOLDER = "lacan_source_texts"  # 你的txt文件夹路径
OUTPUT_FILE_TXT = "../data/lacan_full_corpus.txt"  # 输出的纯文本文件名
OUTPUT_FILE_JSONL = "../data/lacan_dataset.jsonl"  # 输出的微调格式文件名
MIN_PARAGRAPH_LEN = 50  # 过滤掉少于50个字符的段落（通常是页眉/标题/垃圾）


# ===========================================

def clean_text_block(text):
    """
    核心清洗逻辑：针对英文PDF转TXT的常见问题
    """
    # 1. 去除明显的页码 (单独一行的数字)
    # 匹配：前后是换行符，中间只有数字和可能的空格
    text = re.sub(r'\n\s*\d+\s*\n', '\n', text)

    # 2. 修复断词 (Hyphenation repair)
    # PDF中常有 "uncon-\nscious"，合并为 "unconscious"
    text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)

    # 3. 移除多余的空白字符，但保留换行符作为段落标记的线索
    # 先把连续的非换行空白变成一个空格
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)

    return text


def reconstruct_paragraphs(text):
    """
    重组段落：
    PDF转TXT通常每一行都有换行符。
    策略：如果一行以标点符号(.?!)结尾，可能是一个段落的结束。
    否则，应该把下一行拼接到这一行后面。
    """
    lines = text.split('\n')
    paragraphs = []
    buffer = ""

    for line in lines:
        line = line.strip()
        if not line:
            continue  # 跳过空行

        # 如果buffer为空，直接开始新的一段
        if not buffer:
            buffer = line
        else:
            # 如果上一行以连接符结尾（虽然上面修过了，以防万一）或者不是句号结尾
            # 我们假设它属于同一个段落，用空格连接
            # 简单的启发式：如果上一行结束符不是 . ! ? " ”，则拼接到上一行
            # 注意：拉康的句子很长，这里可能会有误判，但在大规模语料中通常可以接受
            if buffer.endswith(('.', '!', '?', '"', '”', ':')):
                # 上一段结束了，存入列表
                paragraphs.append(buffer)
                buffer = line
            else:
                # 拼接
                buffer += " " + line

    # 处理最后一段
    if buffer:
        paragraphs.append(buffer)

    return paragraphs


def process_files():
    all_paragraphs = []

    # 获取所有txt文件
    files = glob.glob(os.path.join(INPUT_FOLDER, "*.txt"))
    print(f"Found {len(files)} text files in {INPUT_FOLDER}...")

    for file_path in files:
        print(f"Processing: {os.path.basename(file_path)}")
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                raw_content = f.read()

            # 初步清洗
            cleaned_content = clean_text_block(raw_content)

            # 重组段落
            paras = reconstruct_paragraphs(cleaned_content)

            # 过滤过短的段落 (过滤掉目录、标题、页眉残留)
            valid_paras = [p for p in paras if len(p) >= MIN_PARAGRAPH_LEN]

            all_paragraphs.extend(valid_paras)

        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    print(f"\nTotal paragraphs extracted: {len(all_paragraphs)}")

    # 写入合并的TXT (用于阅读检查)
    with open(OUTPUT_FILE_TXT, 'w', encoding='utf-8') as f:
        f.write('\n\n'.join(all_paragraphs))
    print(f"Saved merged text to {OUTPUT_FILE_TXT}")

    # 写入 JSONL (用于微调)
    # 格式: {"text": "段落内容..."}
    with open(OUTPUT_FILE_JSONL, 'w', encoding='utf-8') as f:
        for p in all_paragraphs:
            json_obj = {"text": p}
            f.write(json.dumps(json_obj, ensure_ascii=False) + "\n")
    print(f"Saved dataset to {OUTPUT_FILE_JSONL}")


if __name__ == "__main__":
    # 确保你的文件夹路径存在
    if not os.path.exists(INPUT_FOLDER):
        print(f"Error: Folder '{INPUT_FOLDER}' does not exist. Please create it and put txt files inside.")
    else:
        process_files()