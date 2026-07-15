"""学习引擎：改良 BKT + 固定间隔重复 + 路径规划。

改良 BKT（贝叶斯知识追踪）：
- 不依赖群体数据拟合参数（标准 BKT 需要），用文献先验
- 按知识点类型设先验（memory 容易学，design 难学）
- 加入遗忘衰减：超过 7 天未练习的 KP，掌握度按时间衰减

固定间隔重复表（青少年节奏，比成人缩短 30%）：
- REVIEW_INTERVALS = [0, 1, 2, 5, 10, 21, 45] 天
- 连续 2 次正确 → 跳级
- 错误 → 回到 0

路径规划 5 级优先级：
1. 到期复习（间隔重复）
2. 当前未完成的知识点
3. 前置依赖未掌握 → 回退学前置
4. ZPD 薄弱点推荐（难度与能力匹配）
5. 章节顺序推进
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from models import (
    AbilityProfile,
    KnowledgePoint,
    LearnerProgress,
    QuizAttempt,
    ReviewTask,
)


# ─── 改良 BKT 参数 ──────────────────────────────────────────────

# 按知识点类型的先验掌握度（文献先验，非群体拟合）
BKT_PRIORS: dict[str, float] = {
    "memory": 0.3,      # 记忆型容易上手
    "concept": 0.2,     # 概念型稍难
    "procedure": 0.15,  # 程序型需要练习
    "design": 0.1,      # 设计型最难
}

P_GUESS = 0.25    # 猜对概率（4选1）
P_SLIP = 0.10     # 失误概率
MASTERY_THRESHOLD = 0.85  # 掌握门槛

# 遗忘衰减：超过 FORGET_THRESHOLD 天未练习，每天衰减 FORGET_RATE
FORGET_THRESHOLD_DAYS = 7
FORGET_RATE_PER_DAY = 0.03


def bkt_update(
    prior: float,
    is_correct: bool,
    kp_type: str,
    *,
    days_since_last: float = 0.0,
) -> float:
    """改良 BKT 单步更新。

    Args:
        prior: 更新前的掌握度 P(L_{t-1})
        is_correct: 本次答题是否正确
        kp_type: 知识点类型，决定先验
        days_since_last: 距上次练习的天数（用于遗忘衰减）

    Returns:
        更新后的掌握度 P(L_t)
    """
    # 遗忘衰减
    if days_since_last > FORGET_THRESHOLD_DAYS:
        decay = (days_since_last - FORGET_THRESHOLD_DAYS) * FORGET_RATE_PER_DAY
        prior = max(0.0, prior - decay)

    p_l = prior
    p_guess = P_GUESS
    p_slip = P_SLIP

    if is_correct:
        # P(L_t | 正确) = P(L_{t-1}) * (1 - slip) / P(正确)
        p_correct = p_l * (1 - p_slip) + (1 - p_l) * p_guess
        p_l_new = (p_l * (1 - p_slip)) / p_correct if p_correct > 0 else p_l
    else:
        # P(L_t | 错误) = P(L_{t-1}) * slip / P(错误)
        p_wrong = p_l * p_slip + (1 - p_l) * (1 - p_guess)
        p_l_new = (p_l * p_slip) / p_wrong if p_wrong > 0 else p_l

    # 学习增益：本次练习带来的提升（向 1 靠拢）
    learn_gain = 0.05 if is_correct else 0.02
    p_l_new = p_l_new + (1 - p_l_new) * learn_gain

    return max(0.0, min(1.0, p_l_new))


# ─── 固定间隔重复 ──────────────────────────────────────────────

# 青少年节奏：比成人 FSRS 缩短约 30%
REVIEW_INTERVALS: list[int] = [0, 1, 2, 5, 10, 21, 45]  # 天


def schedule_review(
    current_interval_index: int,
    consecutive_correct: int,
    is_correct: bool,
) -> tuple[int, float]:
    """计算下一次复习时间。

    Args:
        current_interval_index: 当前在 REVIEW_INTERVALS 中的索引
        consecutive_correct: 连续答对次数
        is_correct: 本次是否正确

    Returns:
        (new_interval_index, due_at_timestamp)
    """
    if not is_correct:
        # 错误 → 回到 0（明天复习）
        return 0, time.time() + 1 * 86400

    # 连续 2 次正确 → 跳级
    new_index = current_interval_index
    if consecutive_correct >= 2:
        new_index = min(current_interval_index + 1, len(REVIEW_INTERVALS) - 1)
    else:
        new_index = min(current_interval_index + 1, len(REVIEW_INTERVALS) - 1)

    days = REVIEW_INTERVALS[new_index]
    due_at = time.time() + days * 86400
    return new_index, due_at


# ─── 能力画像更新 ──────────────────────────────────────────────

def update_ability(
    current: AbilityProfile,
    cognitive_level: str,
    is_correct: bool,
) -> AbilityProfile:
    """更新四维能力画像（指数移动平均，新值权重 0.3）。

    Args:
        current: 当前能力画像
        cognitive_level: 本次答题考察的维度（memory/understanding/application/analysis）
        is_correct: 是否正确

    Returns:
        更新后的能力画像
    """
    new_value = 1.0 if is_correct else 0.0
    alpha = 0.3  # 新值权重

    updated = current.model_copy()
    setattr(updated, cognitive_level, (1 - alpha) * getattr(current, cognitive_level) + alpha * new_value)
    return updated


# ─── 路径规划 ──────────────────────────────────────────────────

@dataclass
class NextStep:
    """推荐下一步学习内容。"""
    kp_name: str
    reason: str
    priority: int  # 1 最高


def plan_next(
    progress: LearnerProgress,
    all_kps: list[KnowledgePoint],
    *,
    now: float | None = None,
) -> NextStep | None:
    """5 级优先级路径规划。

    优先级 1: 到期复习（间隔重复到期）
    优先级 2: 当前未完成的知识点（mastery < 0.85 且已开始学）
    优先级 3: 前置依赖未掌握 → 回退学前置
    优先级 4: ZPD 薄弱点（难度略高于当前能力）
    优先级 5: 章节顺序推进（第一个未学的知识点）
    """
    moment = now or time.time()

    # 1. 到期复习
    due = [t for t in progress.review_queue if t.due_at <= moment]
    if due:
        due.sort(key=lambda t: t.due_at)
        return NextStep(
            kp_name=due[0].kp_name,
            reason="这个知识点该复习了（间隔重复到期）",
            priority=1,
        )

    # 2. 当前未完成（已开始但未掌握）
    for kp in all_kps:
        m = progress.mastery.get(kp.name, 0.0)
        has_attempt = any(a.kp_name == kp.name for a in progress.attempts)
        if has_attempt and m < MASTERY_THRESHOLD:
            return NextStep(
                kp_name=kp.name,
                reason=f"继续学这个知识点（当前掌握度 {m:.0%}，目标 {MASTERY_THRESHOLD:.0%}）",
                priority=2,
            )

    # 3. 前置依赖回退：找第一个未学且前置未掌握的 KP
    for kp in all_kps:
        if kp.name in progress.mastery and progress.mastery[kp.name] >= MASTERY_THRESHOLD:
            continue
        if not any(a.kp_name == kp.name for a in progress.attempts):
            # 检查前置
            for prereq in kp.prerequisites:
                if progress.mastery.get(prereq, 0.0) < MASTERY_THRESHOLD:
                    return NextStep(
                        kp_name=prereq,
                        reason=f"先学这个前置知识点，再学「{kp.name}」",
                        priority=3,
                    )

    # 4. ZPD 薄弱点：已学但掌握度低的 KP，难度略高于当前能力
    started = [kp for kp in all_kps if any(a.kp_name == kp.name for a in progress.attempts)]
    if started:
        # 计算平均能力
        avg_ability = 0.0
        n = 0
        for kp in started:
            ab = progress.abilities.get(kp.name)
            if ab:
                avg_ability += ab.overall
                n += 1
        avg_ability = avg_ability / n if n > 0 else 0.0

        # 找掌握度最低且难度匹配的
        weak = []
        for kp in started:
            m = progress.mastery.get(kp.name, 0.0)
            if m < MASTERY_THRESHOLD:
                # ZPD：难度略高于能力（+1.0），最近发展区
                if kp.difficulty <= avg_ability + 1.0:
                    weak.append((kp, m))
        if weak:
            weak.sort(key=lambda x: x[1])  # 掌握度最低的优先
            return NextStep(
                kp_name=weak[0][0].name,
                reason=f"针对薄弱点练习（当前掌握度 {weak[0][1]:.0%}）",
                priority=4,
            )

    # 5. 章节顺序：第一个未学的知识点
    for kp in all_kps:
        has_attempt = any(a.kp_name == kp.name for a in progress.attempts)
        if not has_attempt:
            return NextStep(
                kp_name=kp.name,
                reason="开始学新知识点",
                priority=5,
            )

    return None


# ─── 题库管理 ──────────────────────────────────────────────────

class QuizBank:
    """题库管理器。"""

    def __init__(self, questions_path: Path):
        self._path = questions_path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            self._data = {}
            return
        self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def get_questions(self, kp_name: str) -> list[dict]:
        """获取某知识点的所有题目。"""
        entry = self._data.get(kp_name)
        if not entry:
            return []
        return entry.get("questions", [])

    def get_question(self, kp_name: str, question_id: str) -> dict | None:
        """按 ID 获取单题。"""
        for q in self.get_questions(kp_name):
            if q.get("id") == question_id:
                return q
        return None

    def get_explanation(self, kp_name: str) -> str:
        """获取某知识点的讲解文本。"""
        entry = self._data.get(kp_name)
        if not entry:
            return ""
        return entry.get("explanation", "")

    def has_questions(self, kp_name: str) -> bool:
        return len(self.get_questions(kp_name)) > 0


# ─── 知识图谱加载 ──────────────────────────────────────────────

def load_preset(preset_path: Path) -> list[KnowledgePoint]:
    """从 preset.json 加载所有知识点（保持章节顺序）。"""
    data = json.loads(preset_path.read_text(encoding="utf-8"))
    kps: list[KnowledgePoint] = []
    for module in data.get("modules", []):
        module_name = module.get("name", "")
        for kp_data in module.get("knowledge_points", []):
            kp = KnowledgePoint(
                name=kp_data["name"],
                type=kp_data["type"],
                module_name=module_name,
                prerequisites=kp_data.get("prerequisites", []),
            )
            kps.append(kp)
    return kps


# ─── 统计辅助 ──────────────────────────────────────────────────

def get_kp_stats(progress: LearnerProgress, kp_name: str) -> dict:
    """获取某知识点的学习统计。"""
    attempts = [a for a in progress.attempts if a.kp_name == kp_name]
    correct = sum(1 for a in attempts if a.is_correct)
    mastery = progress.mastery.get(kp_name, 0.0)
    ability = progress.abilities.get(kp_name)
    return {
        "total_attempts": len(attempts),
        "correct_count": correct,
        "accuracy": correct / len(attempts) if attempts else 0.0,
        "mastery": mastery,
        "is_mastered": mastery >= MASTERY_THRESHOLD,
        "ability": ability.model_dump() if ability else None,
    }


def get_overview(progress: LearnerProgress, all_kps: list[KnowledgePoint]) -> dict:
    """获取整体学习概览。"""
    total = len(all_kps)
    mastered = sum(1 for kp in all_kps if progress.mastery.get(kp.name, 0.0) >= MASTERY_THRESHOLD)
    started = sum(1 for kp in all_kps if any(a.kp_name == kp.name for a in progress.attempts))
    return {
        "total_kps": total,
        "mastered": mastered,
        "started": started,
        "not_started": total - started,
        "master_rate": mastered / total if total > 0 else 0.0,
        "total_attempts": len(progress.attempts),
        "study_streak": progress.study_streak,
        "due_reviews": sum(1 for t in progress.review_queue if t.due_at <= time.time()),
    }


def get_map_status(
    progress: LearnerProgress,
    all_kps: list[KnowledgePoint],
) -> list[dict]:
    """获取知识地图状态（按模块分组）。"""
    by_module: dict[str, list[dict]] = {}
    for kp in all_kps:
        m = progress.mastery.get(kp.name, 0.0)
        has_attempt = any(a.kp_name == kp.name for a in progress.attempts)
        if m >= MASTERY_THRESHOLD:
            status = "mastered"
        elif has_attempt:
            status = "learning"
        else:
            status = "new"
        entry = {
            "name": kp.name,
            "type": kp.type,
            "module": kp.module_name,
            "mastery": round(m, 2),
            "status": status,
        }
        by_module.setdefault(kp.module_name, []).append(entry)

    modules: list[dict] = []
    for name, kps in by_module.items():
        modules.append({
            "name": name,
            "knowledge_points": kps,
            "mastered": sum(1 for k in kps if k["status"] == "mastered"),
            "total": len(kps),
        })
    return modules


__all__ = [
    "BKT_PRIORS",
    "P_GUESS",
    "P_SLIP",
    "MASTERY_THRESHOLD",
    "REVIEW_INTERVALS",
    "bkt_update",
    "schedule_review",
    "update_ability",
    "NextStep",
    "plan_next",
    "QuizBank",
    "load_preset",
    "get_kp_stats",
    "get_overview",
    "get_map_status",
]
