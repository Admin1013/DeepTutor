"""B2: 关键词映射 + 抽检报告。

规则：
- 扫描 question + solution 文本
- 歧义关键词单独命中得 1 分，专属词得 3 分
- 总分 ≥3 才算命中
- 多候选时取总分最高的
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


DATA_DIR = Path(__file__).parent.parent / "data"
RAW = DATA_DIR / "cmm_math_g7_raw.json"
KEYWORDS = Path(__file__).parent / "kp_keywords.json"
PRESET = DATA_DIR / "preset.json"
EXISTING = DATA_DIR / "questions.json"
OUT = DATA_DIR / "questions_v2.json"
UNMAPPED_OUT = DATA_DIR / "_unmapped.json"

# 高歧义关键词：单独命中不足以判定
AMBIGUOUS = {"比较", "和", "差", "积", "商", "角", "整式", "应用", "完成", "相加",
             "相乘", "相减", "相除", "数轴上", "打折", "边", "顶点",
             "去括号", "去分母", "移项", "合并", "展开", "分配", "增加的人数"}


def main():
    raw = json.loads(RAW.read_text(encoding="utf-8"))
    kw_map: dict[str, list[str]] = json.loads(KEYWORDS.read_text(encoding="utf-8"))
    preset = json.loads(PRESET.read_text(encoding="utf-8"))
    existing: dict[str, dict] = json.loads(EXISTING.read_text(encoding="utf-8"))

    all_kps = []
    for m in preset["modules"]:
        for kp in m["knowledge_points"]:
            all_kps.append(kp["name"])

    by_kp: dict[str, list[dict]] = defaultdict(list)
    unmapped: list[dict] = []

    for q in raw:
        text = (q.get("question", "") + " " + q.get("solution", "")).lower()
        scores: list[tuple[str, int]] = []
        for kp, kws in kw_map.items():
            hits = 0
            weight = 0
            for kw in kws:
                if kw.lower() in text:
                    score = 1 if kw in AMBIGUOUS else 3
                    hits += 1
                    weight += score + len(kw) // 2
            if hits >= 1 and weight >= 3:
                scores.append((kp, weight))
        if scores:
            scores.sort(key=lambda x: -x[1])
            by_kp[scores[0][0]].append(q)
        else:
            unmapped.append(q)

    print("=" * 60)
    print(f"映射结果: {sum(len(v) for v in by_kp.values())} 题命中 / {len(unmapped)} 题未命中")
    print(f"命中率: {sum(len(v) for v in by_kp.values()) / len(raw):.1%}")
    print("=" * 60)
    print(f"{'KP 名':<30} {'题数':>4}")
    print("-" * 60)
    for kp in all_kps:
        n = len(by_kp.get(kp, []))
        marker = "✓" if n > 0 else "✗"
        print(f"{marker} {kp:<28} {n:>4}")
    print("-" * 60)
    print(f"未命中: {len(unmapped)} 题")

    # 合并到 v2
    v2: dict[str, dict] = {}
    for kp_name, entry in existing.items():
        v2[kp_name] = {
            "explanation": entry.get("explanation", ""),
            "questions": list(entry.get("questions", [])),
        }
    added = 0
    for kp, items in by_kp.items():
        if kp not in v2:
            v2[kp] = {"explanation": "", "questions": []}
        for q in items:
            v2[kp]["questions"].append({
                "id": q["id"],
                "question": q["question"],
                "options": q["options"],
                "answer": q["answer"],
                "analysis": q.get("solution", ""),
            })
            added += 1

    OUT.write_text(json.dumps(v2, ensure_ascii=False, indent=2), encoding="utf-8")
    UNMAPPED_OUT.write_text(json.dumps(unmapped, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n输出: {OUT}")
    print(f"  原 {sum(len(v['questions']) for v in existing.values())} 题 + CMM-Math {added} 题")


if __name__ == "__main__":
    main()
