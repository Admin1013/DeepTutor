"""Preset curriculum loader — bootstrap a mastery path without an LLM call.

Reads a JSON preset file (shipped under ``deeptutor/learning/presets/``) and
populates a :class:`LearningProgress` via :class:`LearningService`, so a learner
can start a mastery path immediately without waiting for the model to design
one from materials.

Preset JSON structure matches what ``mastery_build`` expects::

    {
      "id": "unique_preset_id",
      "name": "人类可读名称",
      "modules": [
        {
          "name": "模块名",
          "knowledge_points": [
            {"name": "知识点名", "type": "memory|concept|procedure|design"}
          ]
        }
      ]
    }
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from deeptutor.learning.models import KnowledgePoint, KnowledgeType, LearningModule


def list_presets() -> list[dict[str, str]]:
    """Return ``[{id, name, subject, grade, semester}]`` for every preset file."""
    out: list[dict[str, str]] = []
    for entry in resources.files(__package__).joinpath("presets").iterdir():
        if not str(entry.name).endswith(".json"):
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            out.append(
                {
                    "id": data.get("id", entry.name),
                    "name": data.get("name", entry.name),
                    "subject": data.get("subject", ""),
                    "grade": data.get("grade", ""),
                    "semester": data.get("semester", ""),
                }
            )
        except Exception:
            continue
    return out


def load_preset(preset_id: str) -> dict[str, Any]:
    """Load a preset by its ``id`` field. Raises ``FileNotFoundError`` if absent."""
    for entry in resources.files(__package__).joinpath("presets").iterdir():
        if not str(entry.name).endswith(".json"):
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
            if data.get("id") == preset_id:
                return data
        except Exception:
            continue
    raise FileNotFoundError(f"No preset with id={preset_id!r}")


def preset_to_modules(
    data: dict[str, Any], path_id: str, *, offset: int = 0
) -> list[LearningModule]:
    """Convert preset ``data`` into :class:`LearningModule` objects.

    Mirrors the id-generation logic in
    :func:`deeptutor.capabilities.mastery.tools._parse_modules` so presets are
    interchangeable with model-designed paths.
    """
    _ALLOWED = {t.value for t in KnowledgeType}
    modules: list[LearningModule] = []
    for i, raw in enumerate(data.get("modules", [])):
        index = offset + i
        module_id = f"{path_id}_m{index}"
        kps: list[KnowledgePoint] = []
        for j, raw_kp in enumerate(raw.get("knowledge_points", [])):
            kp_type = str(raw_kp.get("type", "concept")).strip().lower()
            if kp_type not in _ALLOWED:
                kp_type = "concept"
            kps.append(
                KnowledgePoint(
                    id=f"{module_id}_kp{j}",
                    name=str(raw_kp["name"]),
                    type=KnowledgeType(kp_type),
                    module_id=module_id,
                )
            )
        modules.append(
            LearningModule(
                id=module_id,
                name=str(raw["name"]),
                order=index,
                knowledge_points=kps,
            )
        )
    return modules


def apply_preset(
    preset_id: str,
    path_id: str,
    *,
    mode: str = "replace",
) -> dict[str, Any]:
    """Load a preset and write it into a learning path. No LLM needed.

    Returns a summary dict with module/kp counts and the map snapshot.
    """
    from deeptutor.learning.policy import map_summary
    from deeptutor.learning.service import LearningService
    from deeptutor.learning.storage import LearningStore

    data = load_preset(preset_id)
    service = LearningService(LearningStore())
    progress = service.get_or_create(path_id)

    offset = len(progress.modules) if mode == "append" else 0
    new_modules = preset_to_modules(data, path_id, offset=offset)
    combined = (
        list(progress.modules) + new_modules if mode == "append" else new_modules
    )
    service.replace_modules(progress, combined)
    progress.pending_question = None
    if combined:
        progress.current_module_id = combined[0].id
        progress.current_kp_index = 0
    service.save(progress)

    return {
        "preset_id": preset_id,
        "preset_name": data.get("name", ""),
        "path_id": path_id,
        "mode": mode,
        "modules_count": len(combined),
        "knowledge_points_count": sum(len(m.knowledge_points) for m in combined),
        "map": map_summary(progress),
    }


__all__ = [
    "list_presets",
    "load_preset",
    "preset_to_modules",
    "apply_preset",
]
