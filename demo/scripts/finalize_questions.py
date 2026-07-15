"""C1: 从 questions_v2.json 生成最终 questions.json。

规则：
- 每 KP 题数 ≤ 12
- 按 question 长度排序后均匀抽样
- 保留 explanation 字段
"""
from __future__ import annotations

import json
from pathlib import Path


DATA_DIR = Path(__file__).parent.parent / "data"
V2 = DATA_DIR / "questions_v2.json"
PRESET = DATA_DIR / "preset.json"
OUT = DATA_DIR / "questions.json"

MAX_PER_KP = 12


def main():
    v2: dict[str, dict] = json.loads(V2.read_text(encoding="utf-8"))
    preset = json.loads(PRESET.read_text(encoding="utf-8"))

    all_kps: list[str] = []
    for m in preset["modules"]:
        for kp in m["knowledge_points"]:
            all_kps.append(kp["name"])

    final: dict[str, dict] = {}
    total = 0
    for kp in all_kps:
        entry = v2.get(kp, {})
        qs = list(entry.get("questions", []))
        explanation = entry.get("explanation", "")

        seen = set()
        unique = []
        for q in qs:
            key = q.get("question", "")[:50]
            if key in seen:
                continue
            seen.add(key)
            unique.append(q)

        if len(unique) <= MAX_PER_KP:
            selected = unique
        else:
            unique.sort(key=lambda q: len(q.get("question", "")))
            n = MAX_PER_KP
            step = len(unique) / n
            selected = [unique[int(i * step)] for i in range(n)]

        final[kp] = {"explanation": explanation, "questions": selected}
        total += len(selected)

    OUT.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    covered = sum(1 for v in final.values() if len(v["questions"]) > 0)
    print(f"最终题库: {OUT}")
    print(f"总题数: {total}")
    print(f"KP 覆盖: {covered}/{len(all_kps)} ({covered/len(all_kps):.0%})")
    print(f"  0 题: {sum(1 for v in final.values() if len(v['questions']) == 0)} 个 KP")
    print(f"  1-3 题: {sum(1 for v in final.values() if 1 <= len(v['questions']) <= 3)} 个 KP")
    print(f"  4-8 题: {sum(1 for v in final.values() if 4 <= len(v['questions']) <= 8)} 个 KP")
    print(f"  9-12 题: {sum(1 for v in final.values() if 9 <= len(v['questions']) <= 12)} 个 KP")


if __name__ == "__main__":
    main()
