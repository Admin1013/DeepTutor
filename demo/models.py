"""数据模型 + 四维能力画像。

四维能力对应布鲁姆认知分类法：
- memory（记忆）：识记事实、定义、公式
- understanding（理解）：解释概念、转换表述
- application（应用）：在熟悉情境中运用
- analysis（分析）：在新情境中拆解、推理

知识点类型 → 能力维度映射：
- memory    → memory
- concept   → understanding
- procedure → application
- design    → analysis
"""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# 知识点类型 → 认知层次映射
COGNITIVE_LEVEL_BY_TYPE: dict[str, str] = {
    "memory": "memory",
    "concept": "understanding",
    "procedure": "application",
    "design": "analysis",
}


class KnowledgePoint(BaseModel):
    """知识点（来自 preset.json）。"""
    model_config = ConfigDict(extra="ignore")

    name: str
    type: Literal["memory", "concept", "procedure", "design"]
    module_name: str = ""
    prerequisites: list[str] = Field(default_factory=list)

    @property
    def cognitive_level(self) -> str:
        """该知识点对应的能力维度。"""
        return COGNITIVE_LEVEL_BY_TYPE.get(self.type, "application")

    @property
    def difficulty(self) -> float:
        """知识点难度先验（1-5）。

        memory=1.5, concept=2.5, procedure=3.0, design=4.5
        用于 ZPD 推荐时的难度匹配。
        """
        return {"memory": 1.5, "concept": 2.5, "procedure": 3.0, "design": 4.5}.get(self.type, 3.0)


class AbilityProfile(BaseModel):
    """四维能力画像（单个知识点）。"""
    model_config = ConfigDict(extra="ignore")

    memory: float = 0.0
    understanding: float = 0.0
    application: float = 0.0
    analysis: float = 0.0

    @property
    def overall(self) -> float:
        """综合能力值（四维平均）。"""
        return (self.memory + self.understanding + self.application + self.analysis) / 4

    @property
    def weakest(self) -> str:
        """最薄弱的维度名。"""
        dims = {
            "memory": self.memory,
            "understanding": self.understanding,
            "application": self.application,
            "analysis": self.analysis,
        }
        return min(dims, key=dims.get)  # type: ignore[arg-type]


class QuizAttempt(BaseModel):
    """单次答题记录。"""
    model_config = ConfigDict(extra="ignore")

    question_id: str
    kp_name: str
    is_correct: bool
    user_answer: Any = None
    cognitive_level: str = "application"
    duration_sec: float = 0.0
    timestamp: float = Field(default_factory=time.time)


class ReviewTask(BaseModel):
    """间隔重复任务。"""
    model_config = ConfigDict(extra="ignore")

    kp_name: str
    due_at: float
    interval_index: int = 0


class StudySession(BaseModel):
    """学习会话记录。"""
    model_config = ConfigDict(extra="ignore")

    started_at: float
    duration_sec: float = 0.0
    kps_touched: list[str] = Field(default_factory=list)


class LearnerProgress(BaseModel):
    """学习者进度聚合根（单学生单科目）。"""
    model_config = ConfigDict(extra="ignore")

    student_id: str = "default"
    # kp_name → mastery (0-1)
    mastery: dict[str, float] = Field(default_factory=dict)
    # kp_name → AbilityProfile
    abilities: dict[str, AbilityProfile] = Field(default_factory=dict)
    attempts: list[QuizAttempt] = Field(default_factory=list)
    review_queue: list[ReviewTask] = Field(default_factory=list)
    study_sessions: list[StudySession] = Field(default_factory=list)
    # 错误模式统计：{"概念混淆": 3, "计算错误": 5, ...}
    error_patterns: dict[str, int] = Field(default_factory=dict)
    study_streak: int = 0
    last_study_date: str = ""  # YYYY-MM-DD
    total_study_sec: float = 0.0


__all__ = [
    "COGNITIVE_LEVEL_BY_TYPE",
    "KnowledgePoint",
    "AbilityProfile",
    "QuizAttempt",
    "ReviewTask",
    "StudySession",
    "LearnerProgress",
]
