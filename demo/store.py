"""JSON 存储：per-book asyncio.Lock + 原子写 + 3 备份。

并发安全方案：
- per-book asyncio.Lock 字典，保护整个 read-modify-write
- 文件 IO 用 asyncio.to_thread 包装，避免阻塞事件循环
- 原子写：先写 .tmp.<uuid>，再 replace 到目标路径
- 保留前 3 个版本备份（.bak1 最新，.bak3 最旧）

文件布局：
    data/default.json          当前版本
    data/default.json.bak1     上一次
    data/default.json.bak2     上上一次
    data/default.json.bak3     最旧备份
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from models import LearnerProgress


class LearnerStore:
    """学习者进度存储（单文件 JSON，per-book 锁）。"""

    def __init__(self, data_dir: Path, student_id: str = "default"):
        self._dir = data_dir
        self._student_id = student_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{student_id}.json"
        # per-book 锁
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _get_lock(self, key: str) -> asyncio.Lock:
        """获取或创建 per-book 锁（创建时用 guard 保护，避免并发创建）。"""
        if key in self._locks:
            return self._locks[key]
        async with self._locks_guard:
            if key not in self._locks:
                self._locks[key] = asyncio.Lock()
            return self._locks[key]

    async def load(self) -> LearnerProgress:
        """读取进度（无锁，读快照）。"""
        lock = await self._get_lock(self._student_id)
        async with lock:
            return await self._load_unlocked()

    async def _load_unlocked(self) -> LearnerProgress:
        if not self._path.exists():
            return LearnerProgress(student_id=self._student_id)
        data = await asyncio.to_thread(self._path.read_text, encoding="utf-8")
        return LearnerProgress.model_validate_json(data)

    async def save(self, progress: LearnerProgress) -> None:
        """保存进度（加锁 + 原子写 + 备份滚动）。"""
        lock = await self._get_lock(self._student_id)
        async with lock:
            await self._save_unlocked(progress)

    async def _save_unlocked(self, progress: LearnerProgress) -> None:
        data = progress.model_dump_json(indent=2)

        # 备份滚动：bak2→bak3, bak1→bak2, path→bak1
        bak1 = self._path.with_suffix(self._path.suffix + ".bak1")
        bak2 = self._path.with_suffix(self._path.suffix + ".bak2")
        bak3 = self._path.with_suffix(self._path.suffix + ".bak3")
        if bak2.exists():
            await asyncio.to_thread(bak2.replace, bak3)
        if bak1.exists():
            await asyncio.to_thread(bak1.replace, bak2)
        if self._path.exists():
            await asyncio.to_thread(self._path.replace, bak1)

        # 原子写：先写 .tmp.<uuid>，再 replace
        tmp = self._path.with_suffix(self._path.suffix + f".tmp.{uuid.uuid4().hex}")
        await asyncio.to_thread(tmp.write_text, data, encoding="utf-8")
        await asyncio.to_thread(tmp.replace, self._path)

    async def update(self, fn) -> LearnerProgress:
        """在锁内执行 read-modify-write。

        Args:
            fn: 接收 LearnerProgress，返回更新后的 LearnerProgress

        Returns:
            更新后的 LearnerProgress
        """
        lock = await self._get_lock(self._student_id)
        async with lock:
            progress = await self._load_unlocked()
            updated = fn(progress)
            await self._save_unlocked(updated)
            return updated


__all__ = ["LearnerStore"]
