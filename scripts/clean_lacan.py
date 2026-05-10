"""
clean_lacan.py

这个脚本做项目的第 1 步：把原始 txt 文本清理成可训练的数据。

你可以把整个项目理解为一条流水线：
1. clean_lacan.py          原始 txt -> 干净段落 JSONL
2. generate_sft_gemma.py  干净段落 -> 问答训练样本
3. run_pipeline.py        问答训练样本 -> LoRA 适配器

本脚本只做“规则清理”，不调用大模型，也不训练模型。
这样做的好处是：清理过程更可复现，后续写论文/预印本时可以清楚说明数据是怎么来的。
"""

import argparse
import json
import re
from pathlib import Path


# Path(__file__) 是当前脚本文件 scripts/clean_lacan.py 的路径。
# .resolve() 会把它变成绝对路径。
# .parents[1] 表示向上走两级：
#   scripts/clean_lacan.py -> scripts/ -> LacanLLM/
# 所以 PROJECT_ROOT 永远指向项目根目录，不依赖你从哪个目录运行脚本。
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 原始 txt 文件目录。这里保留原始材料，不应该被脚本删除或覆盖。
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "lacan_source_texts"

# 合并后的纯文本文件，主要用于人工快速阅读检查。
DEFAULT_CORPUS_FILE = PROJECT_ROOT / "data" / "lacan_full_corpus.txt"

# JSONL 数据文件。JSONL 是“一行一个 JSON 对象”的格式，很适合大规模训练数据。
DEFAULT_DATASET_FILE = PROJECT_ROOT / "data" / "lacan_dataset.jsonl"

# 过滤太短的段落。少于 120 字符时，经常是页眉、标题、目录项、脚注碎片。
MIN_PARAGRAPH_CHARS = 120

# 过滤太长的段落。太长的段落会导致后续生成问题和训练时被截断。
MAX_PARAGRAPH_CHARS = 6000

# mojibake 指“编码损坏后出现的奇怪字符”，比如英文引号被错误解码成一串乱码。
# 这里用 Unicode escape 写法，而不是直接写字符，是为了避免 Windows/GBK 终端再次把文件显示乱。
# 这些 marker 只用于统计风险，不会自动删除对应段落。
MOJIBAKE_MARKERS = (
    "\u923f",  # 常见乱码片段之一
    "\u6f0f",
    "\u7984",
    "\u8121",
    "\u951b",
    "\u7ecb",
    "\u99c3",
)


def normalize_text(text: str) -> str:
    """
    对单个 txt 文件的原始文本做“字符级”标准化。

    输入:
        text: 从 txt 文件直接读出来的字符串。

    输出:
        清理过换行、空白、页码、断词后的字符串。
    """

    # UTF-8 BOM 是某些文本文件开头隐藏的标记，留着会污染第一段文本。
    text = text.replace("\ufeff", "")

    # Windows 换行是 \r\n，Linux/macOS 常见换行是 \n。
    # 统一成 \n，后续正则处理会简单很多。
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 把连续的空格、tab 等“非换行空白”压缩成一个普通空格。
    # 注意这里不处理 \n，因为换行是判断段落的重要线索。
    text = re.sub(r"[ \t\f\v]+", " ", text)

    # 删除独占一行的页码。例如：
    #   \n  23  \n
    # 会变成一个普通换行。
    text = re.sub(r"\n\s*\d+\s*\n", "\n", text)

    # 修复 PDF/OCR 常见断词：
    #   uncon-
    #   scious
    # 会合并为 unconscious。
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)

    # 删除行尾多余空格。
    text = re.sub(r"[ ]+\n", "\n", text)

    # 3 个及以上连续换行压缩为 2 个换行。
    # 两个换行通常表示段落边界。
    text = re.sub(r"\n{3,}", "\n\n", text)

    # strip() 删除文本开头和结尾的空白。
    return text.strip()


def reconstruct_paragraphs(text: str) -> list[str]:
    """
    把标准化后的文本重组为段落列表。

    这里的策略比较保守：优先按“空行”切段，而不是按句号切段。
    原因是拉康文本句子很长，如果按句号/问号判断段落，容易误切或误合并。
    """

    # \n\s*\n 表示“中间可以有空格的空行”。
    blocks = re.split(r"\n\s*\n", text)
    paragraphs: list[str] = []

    for block in blocks:
        # block 里可能仍有很多因为 PDF 排版留下的单行换行。
        # 先去掉每一行首尾空格，再丢弃空行。
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        # 把同一段中的多行重新用空格连接起来。
        merged = " ".join(lines)

        # 再次压缩所有连续空白，保证输出是一段普通文本。
        merged = re.sub(r"\s+", " ", merged).strip()

        # 只有通过质量过滤的段落才保留。
        if is_useful_paragraph(merged):
            paragraphs.append(merged)

    return paragraphs


def is_useful_paragraph(text: str) -> bool:
    """
    判断一个段落是否适合进入训练数据。

    这个函数是清理逻辑的核心。它不是完美分类器，只是一组保守规则。
    对预印本/论文来说，规则清理后仍建议人工抽样检查。
    """

    # 太短或太长都不要。
    if len(text) < MIN_PARAGRAPH_CHARS or len(text) > MAX_PARAGRAPH_CHARS:
        return False

    # 如果空格少于 10 个，大概率不是正常英文段落，可能是标题、目录项、乱码。
    if text.count(" ") < 10:
        return False

    # 如果整段都是数字、标点、符号，也不是可训练的自然语言段落。
    if re.fullmatch(r"[\W\d_]+", text):
        return False

    # lower() 用于大小写不敏感匹配。例如 ISBN / isbn 都能被检测。
    lowered = text.lower()

    # 这些通常是版权页、目录、出版信息、网页信息，不是拉康正文。
    boilerplate = (
        "contents",
        "isbn",
        "all rights reserved",
        "library of congress",
        "british library cataloguing",
        "printed in",
        "translation of:",
        "translated by",
        "published by",
        "copyright",
        "www.",
    )
    if any(term in lowered for term in boilerplate):
        return False

    # 粗略过滤书目信息，例如 "London: Routledge"、"Paris: Seuil"。
    if re.search(r"\b(london|new york|paris):\s+[a-z]", lowered):
        return False

    # 粗略过滤 citation/bibliography 风格的行，例如 "1977, Jacques Lacan..."。
    if re.search(r"\b\d{4}[;,.]\s+[A-Z][A-Za-z]+", text):
        return False

    # 过滤形如 "2 [Translator note...]" 的编号脚注。
    if re.match(r"^\d+\s*\[", text):
        return False

    # 目录行常含有 |，而且词数很短。
    if "|" in text and len(text.split()) < 35:
        return False

    return True


def mojibake_score(text: str) -> int:
    """
    统计一个段落里疑似编码损坏字符出现了多少次。

    注意：这里不直接删除段落。
    原因是有些文本即使有少量编码问题，主体内容仍可能有用。
    我们把分数写入 JSONL，方便后续按分数过滤或抽样检查。
    """

    return sum(text.count(marker) for marker in MOJIBAKE_MARKERS)


def process_files(input_dir: Path, corpus_file: Path, dataset_file: Path) -> None:
    """
    主处理函数：读取所有 txt，清理，分段，输出 corpus txt 和 dataset jsonl。
    """

    # glob("*.txt") 找到目录下所有 txt 文件。
    # sorted() 保证每次运行顺序一致，便于复现。
    source_files = sorted(input_dir.glob("*.txt"))
    if not source_files:
        raise FileNotFoundError(f"No .txt source files found in {input_dir}")

    # all_paragraphs 保存所有段落的结构化信息，而不只是纯文本。
    all_paragraphs: list[dict[str, str | int]] = []

    # mojibake_hits 统计有编码风险的段落数量。
    mojibake_hits = 0

    for file_path in source_files:
        # errors="replace" 的意思是：遇到坏字符时用替代符号，不直接报错中断。
        raw_content = file_path.read_text(encoding="utf-8", errors="replace")

        # 第一步：标准化空白、换行、页码、断词。
        cleaned = normalize_text(raw_content)

        # 第二步：从文本中抽取段落。
        paragraphs = reconstruct_paragraphs(cleaned)

        for index, paragraph in enumerate(paragraphs):
            score = mojibake_score(paragraph)
            mojibake_hits += int(score > 0)

            # 每个段落保存为一个 dict。
            # source_file 和 paragraph_index 很重要：
            # 它们让你知道训练样本来自哪一个源文件、哪一个段落。
            all_paragraphs.append(
                {
                    "text": paragraph,
                    "source_file": file_path.name,
                    "paragraph_index": index,
                    "char_count": len(paragraph),
                    "mojibake_score": score,
                }
            )

        print(f"{file_path.name}: kept {len(paragraphs)} paragraphs")

    # mkdir(parents=True, exist_ok=True)：
    # 如果 data/ 不存在就创建；如果已经存在也不报错。
    corpus_file.parent.mkdir(parents=True, exist_ok=True)

    # 写一个纯文本版本，便于人类打开检查。
    corpus_file.write_text(
        "\n\n".join(item["text"] for item in all_paragraphs),
        encoding="utf-8",
    )

    # 写 JSONL 版本，供后续脚本读取。
    with dataset_file.open("w", encoding="utf-8") as output:
        for item in all_paragraphs:
            # ensure_ascii=False 保留原始字符，不把中文/特殊符号转成 \uXXXX。
            output.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Total paragraphs kept: {len(all_paragraphs)}")
    print(f"Paragraphs with mojibake markers: {mojibake_hits}")
    print(f"Saved corpus: {corpus_file}")
    print(f"Saved dataset: {dataset_file}")


def parse_args() -> argparse.Namespace:
    """
    定义命令行参数。

    例如：
        python scripts/clean_lacan.py
        python scripts/clean_lacan.py --input-dir data/my_txts
    """

    parser = argparse.ArgumentParser(description="Clean Lacan source txt files into JSONL.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--corpus-file", type=Path, default=DEFAULT_CORPUS_FILE)
    parser.add_argument("--dataset-file", type=Path, default=DEFAULT_DATASET_FILE)
    return parser.parse_args()


# 这个判断表示：只有当你直接运行本文件时，才执行下面的逻辑。
# 如果其他 Python 文件 import clean_lacan.py，则不会自动开始清理。
if __name__ == "__main__":
    args = parse_args()
    process_files(args.input_dir, args.corpus_file, args.dataset_file)
