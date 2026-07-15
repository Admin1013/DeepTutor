"""A1+A2: 从 CMM-Math 拉取七年级题，清洗后输出 cmm_math_g7_raw.json。

清洗规则：
- 过滤 level == "七年级"
- 识别填空题占位（options 字面量是 '[]'）
- options 是 '\n' 分隔的字符串，按行分割 + 剥离 "A./B./C./D." 前缀
- answer 字母转索引（A→0, B→1, ...）
- 过滤掉 question 长度 < 5 或 options 不足 2 项的脏数据
- 按 question 文本 hash 去重
"""
from __future__ import annotations

import json
import os
import re
import hashlib
from pathlib import Path

# 使用 HF 镜像（主站不通）
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from datasets import load_dataset


DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT = DATA_DIR / "cmm_math_g7_raw.json"

# 选项前缀剥离：A. / A、 / A） / A) 等
_OPT_PREFIX_RE = re.compile(r"^\s*([A-Da-d])\s*[\.、）)]\s*")
# <ImageHere> 占位符
_IMG_PLACEHOLDER_RE = re.compile(r"<ImageHere>")


def clean_text(s: str) -> str:
    """清洗题目文本：去占位符、合并空白。"""
    if not s:
        return ""
    s = _IMG_PLACEHOLDER_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def split_options(opts_raw) -> list[str]:
    """options 可能是 '\\n' 分隔的字符串，也可能是字面量 '[]'（填空题占位）。
    返回空 list 表示非选择题。"""
    if opts_raw is None:
        return []
    # 填空题占位：字面量 '[]' 或空 list
    if opts_raw == "[]" or opts_raw == []:
        return []
    if isinstance(opts_raw, str):
        parts = [p.strip() for p in opts_raw.split("\n") if p.strip()]
        if len(parts) < 2:
            parts = re.split(r"(?=[A-D]\s*[\.、）)])", opts_raw)
            parts = [p.strip() for p in parts if p.strip()]
        return parts
    if isinstance(opts_raw, list):
        if opts_raw and isinstance(opts_raw[0], str) and len(opts_raw[0]) <= 1 and len(opts_raw) > 20:
            joined = "".join(opts_raw)
            return split_options(joined)
        return [str(p).strip() for p in opts_raw if str(p).strip()]
    return []


def clean_options(opts_raw) -> list[str] | None:
    """统一选项格式：分割 + 剥离字母前缀。返回 None 表示脏数据。"""
    parts = split_options(opts_raw)
    cleaned = []
    for opt in parts:
        opt = clean_text(opt)
        opt = _OPT_PREFIX_RE.sub("", opt)
        if opt:
            cleaned.append(opt)
    return cleaned if len(cleaned) >= 2 else None


def answer_to_idx(ans: str, n_opts: int) -> int | None:
    """字母答案转索引。"""
    if not ans:
        return None
    ans = ans.strip().upper()
    valid_letters = ["A", "B", "C", "D"][:n_opts]
    if ans in valid_letters:
        return ord(ans) - ord("A")
    if ans.isdigit():
        idx = int(ans) - 1
        if 0 <= idx < n_opts:
            return idx
    return None


def main():
    print("拉取 CMM-Math (streaming)...")
    ds = load_dataset("ecnu-icalk/cmm-math", split="train", streaming=True)
    g7 = [x for x in ds if x.get("level") == "七年级"]
    print(f"七年级原始题数: {len(g7)}")

    seen_hashes: set[str] = set()
    cleaned: list[dict] = []
    dropped = {"dup": 0, "no_answer": 0, "fill_in_blank": 0, "bad_options": 0, "too_short": 0}

    for i, x in enumerate(g7):
        q = clean_text(x.get("question", ""))
        opts_raw = x.get("options")
        if opts_raw == "[]" or opts_raw == []:
            dropped["fill_in_blank"] += 1
            continue
        opts = clean_options(opts_raw)
        if not opts or len(opts) < 2:
            dropped["bad_options"] += 1
            continue
        ans_idx = answer_to_idx(str(x.get("answer", "")), len(opts))
        if ans_idx is None:
            dropped["no_answer"] += 1
            continue
        if len(q) < 5:
            dropped["too_short"] += 1
            continue

        h = hashlib.md5(q.encode("utf-8")).hexdigest()
        if h in seen_hashes:
            dropped["dup"] += 1
            continue
        seen_hashes.add(h)

        cleaned.append({
            "id": f"cmm_g7_{i:04d}",
            "question": q,
            "options": opts,
            "answer": ans_idx,
            "solution": clean_text(x.get("solution", "")) or clean_text(x.get("analysis", "")),
            "subject": x.get("subject", ""),
        })

    OUTPUT.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n清洗后: {len(cleaned)} 题")
    print(f"丢弃: {dropped}")
    print(f"输出: {OUTPUT}")


if __name__ == "__main__":
    main()
