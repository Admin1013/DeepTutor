"""FastAPI 应用：初一数学学习辅助。

6 个路由：
- GET  /                    知识地图页
- GET  /learn/{kp_name}     学习卡片页
- GET  /quiz/{kp_name}      答题页
- POST /quiz/{kp_name}/submit  提交答题
- GET  /result/{kp_name}    知识点学习结果
- GET  /profile             学生能力画像
- GET  /parent              家长概览

单用户 MVP，STUDENT_ID = "default"。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from engine import (
    BKT_PRIORS,
    MASTERY_THRESHOLD,
    QuizBank,
    bkt_update,
    get_kp_stats,
    get_map_status,
    get_overview,
    load_preset,
    plan_next,
    schedule_review,
    update_ability,
)
from models import AbilityProfile, KnowledgePoint, LearnerProgress, QuizAttempt
from store import LearnerStore


BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

STUDENT_ID = "default"

app = FastAPI(title="初一数学学习辅助")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# 启动时加载知识图谱 + 题库
KPS: list[KnowledgePoint] = load_preset(DATA_DIR / "preset.json")
KP_MAP: dict[str, KnowledgePoint] = {kp.name: kp for kp in KPS}
QUIZ_BANK = QuizBank(DATA_DIR / "questions.json")
STORE = LearnerStore(DATA_DIR, STUDENT_ID)


@app.get("/", response_class=HTMLResponse)
async def map_page(request: Request):
    """知识地图页：按模块分组显示 51 个知识点状态。"""
    progress = await STORE.load()
    modules = get_map_status(progress, KPS)
    overview = get_overview(progress, KPS)
    next_step = plan_next(progress, KPS)
    return templates.TemplateResponse(request, "map.html", {
        "modules": modules,
        "overview": overview,
        "next_step": next_step,
    })


@app.get("/learn/{kp_name}", response_class=HTMLResponse)
async def learn_page(request: Request, kp_name: str):
    """学习卡片页：显示预设讲解。"""
    kp = KP_MAP.get(kp_name)
    if not kp:
        return RedirectResponse("/")
    explanation = QUIZ_BANK.get_explanation(kp_name)
    has_questions = QUIZ_BANK.has_questions(kp_name)
    return templates.TemplateResponse(request, "learn.html", {
        "kp": kp,
        "explanation": explanation,
        "has_questions": has_questions,
    })


@app.get("/quiz/{kp_name}", response_class=HTMLResponse)
async def quiz_page(request: Request, kp_name: str, q: int = 0):
    """答题页。"""
    kp = KP_MAP.get(kp_name)
    if not kp:
        return RedirectResponse("/")
    questions = QUIZ_BANK.get_questions(kp_name)
    if not questions:
        return RedirectResponse(f"/learn/{kp_name}")
    q_index = min(q, len(questions) - 1)
    question = questions[q_index]
    return templates.TemplateResponse(request, "quiz.html", {
        "kp": kp,
        "question": question,
        "q_index": q_index,
        "q_total": len(questions),
    })


@app.post("/quiz/{kp_name}/submit", response_class=HTMLResponse)
async def quiz_submit(
    request: Request,
    kp_name: str,
    question_id: str = Form(...),
    answer: int = Form(...),
    q_index: int = Form(0),
):
    """提交答题 → 批改 → 更新 BKT + 能力画像 + 间隔重复 → 返回反馈。"""
    kp = KP_MAP.get(kp_name)
    if not kp:
        return RedirectResponse("/")

    question = QUIZ_BANK.get_question(kp_name, question_id)
    if not question:
        return RedirectResponse(f"/quiz/{kp_name}")

    is_correct = answer == question["answer"]
    cognitive_level = kp.cognitive_level

    def _record(p: LearnerProgress) -> LearnerProgress:
        # 记录答题
        attempt = QuizAttempt(
            question_id=question_id,
            kp_name=kp_name,
            is_correct=is_correct,
            user_answer=answer,
            cognitive_level=cognitive_level,
            duration_sec=0.0,
        )
        p.attempts.append(attempt)

        # BKT 更新（含遗忘衰减）
        prior = p.mastery.get(kp_name, BKT_PRIORS.get(kp.type, 0.15))
        last_attempt = next(
            (a for a in reversed(p.attempts[:-1]) if a.kp_name == kp_name),
            None,
        )
        days_since = (
            (time.time() - last_attempt.timestamp) / 86400
            if last_attempt
            else 0.0
        )
        new_mastery = bkt_update(prior, is_correct, kp.type, days_since_last=days_since)
        p.mastery[kp_name] = new_mastery

        # 能力画像更新
        current_ability = p.abilities.get(kp_name, AbilityProfile())
        p.abilities[kp_name] = update_ability(current_ability, cognitive_level, is_correct)

        # 间隔重复更新
        existing = next((t for t in p.review_queue if t.kp_name == kp_name), None)
        # 统计当前 KP 的连续答对次数（跳过其他 KP 的 attempt）
        consecutive_correct = 0
        for a in reversed(p.attempts):
            if a.kp_name == kp_name:
                if a.is_correct:
                    consecutive_correct += 1
                else:
                    break

        if existing:
            new_idx, due_at = schedule_review(
                existing.interval_index, consecutive_correct, is_correct
            )
            existing.interval_index = new_idx
            existing.due_at = due_at
        else:
            new_idx, due_at = schedule_review(0, consecutive_correct, is_correct)
            from models import ReviewTask
            p.review_queue.append(ReviewTask(
                kp_name=kp_name,
                due_at=due_at,
                interval_index=new_idx,
            ))

        # 错误模式（简化版：根据错误类型归类）
        if not is_correct:
            error_type = "概念混淆"
            if kp.type == "procedure":
                error_type = "计算错误"
            elif kp.type == "design":
                error_type = "应用错误"
            p.error_patterns[error_type] = p.error_patterns.get(error_type, 0) + 1

        # 学习连续天数
        today = datetime.now().strftime("%Y-%m-%d")
        if p.last_study_date != today:
            if p.last_study_date:
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                if p.last_study_date == yesterday:
                    p.study_streak += 1
                else:
                    p.study_streak = 1
            else:
                p.study_streak = 1
            p.last_study_date = today

        return p

    progress = await STORE.update(_record)
    stats = get_kp_stats(progress, kp_name)

    return templates.TemplateResponse(request, "quiz_result.html", {
        "kp": kp,
        "question": question,
        "user_answer": answer,
        "is_correct": is_correct,
        "mastery": stats["mastery"],
        "is_mastered": stats["is_mastered"],
        "q_index": q_index,
        "q_total": len(QUIZ_BANK.get_questions(kp_name)),
    })


@app.get("/result/{kp_name}", response_class=HTMLResponse)
async def result_page(request: Request, kp_name: str):
    """知识点学习结果。"""
    kp = KP_MAP.get(kp_name)
    if not kp:
        return RedirectResponse("/")
    progress = await STORE.load()
    stats = get_kp_stats(progress, kp_name)
    return templates.TemplateResponse(request, "result.html", {
        "kp": kp,
        "stats": stats,
    })


@app.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request):
    """学生能力画像（四维雷达图）。"""
    progress = await STORE.load()
    overview = get_overview(progress, KPS)

    # 汇总四维能力（所有已学 KP 的平均值）
    dims = {"memory": 0.0, "understanding": 0.0, "application": 0.0, "analysis": 0.0}
    n = 0
    for ab in progress.abilities.values():
        dims["memory"] += ab.memory
        dims["understanding"] += ab.understanding
        dims["application"] += ab.application
        dims["analysis"] += ab.analysis
        n += 1
    if n > 0:
        for k in dims:
            dims[k] /= n

    # 薄弱知识点（掌握度 < 0.5 且已开始学）
    weak_kps = []
    for kp in KPS:
        m = progress.mastery.get(kp.name, 0.0)
        has_attempt = any(a.kp_name == kp.name for a in progress.attempts)
        if has_attempt and m < 0.5:
            weak_kps.append({"name": kp.name, "mastery": round(m, 2), "type": kp.type})
    weak_kps.sort(key=lambda x: x["mastery"])

    return templates.TemplateResponse(request, "profile.html", {
        "overview": overview,
        "dims": dims,
        "weak_kps": weak_kps[:5],
        "error_patterns": progress.error_patterns,
    })


@app.get("/parent", response_class=HTMLResponse)
async def parent_page(request: Request):
    """家长概览：进度环 + 薄弱分析 + 学习时长。"""
    progress = await STORE.load()
    overview = get_overview(progress, KPS)

    # 最近 7 天答题情况
    now = time.time()
    week_ago = now - 7 * 86400
    recent_attempts = [a for a in progress.attempts if a.timestamp >= week_ago]
    recent_correct = sum(1 for a in recent_attempts if a.is_correct)

    # 薄弱章节（模块级）
    modules = get_map_status(progress, KPS)
    weak_modules = []
    for m in modules:
        if m["mastered"] < m["total"]:
            weak_modules.append({
                "name": m["name"],
                "mastered": m["mastered"],
                "total": m["total"],
                "rate": m["mastered"] / m["total"] if m["total"] > 0 else 0,
            })
    weak_modules.sort(key=lambda x: x["rate"])

    return templates.TemplateResponse(request, "parent.html", {
        "overview": overview,
        "recent_attempts": len(recent_attempts),
        "recent_correct": recent_correct,
        "recent_accuracy": recent_correct / len(recent_attempts) if recent_attempts else 0,
        "weak_modules": weak_modules[:3],
        "error_patterns": progress.error_patterns,
        "study_streak": progress.study_streak,
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
