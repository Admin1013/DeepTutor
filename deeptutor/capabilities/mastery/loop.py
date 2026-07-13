"""Mastery path loop-capability hooks."""

from __future__ import annotations

from importlib import resources
from typing import Any

from deeptutor.capabilities.mastery.tools import MASTERY_TOOL_NAMES
from deeptutor.capabilities.protocol import PromptBlock
from deeptutor.core.context import UnifiedContext


class MasteryLoopCapability:
    """Turn-scoped integration for mastery-path tutoring.

    Reuses the full chat tool surface (rag / read_source / ask_user / … under
    the same user toggles as chat) and adds the mastery engine tools on top.
    """

    name = "mastery"
    owned_tools = MASTERY_TOOL_NAMES

    def is_active(self, context: UnifiedContext) -> bool:
        return bool(context.metadata.get("mastery_mode"))

    def system_block(
        self,
        context: UnifiedContext,
        *,
        language: str,
        prompts: dict[str, Any],
    ) -> PromptBlock | None:
        if not self.is_active(context):
            return None
        override = _prompt_text(prompts, ("mastery", "system"))
        content = override or _load_system_prompt(language)
        # Append subject-specific tutor hint from a preset, if one matches
        # the active path id.  This lets preset curricula ship pedagogy
        # guidance (e.g. "use number-line metaphors for 7th-grade math")
        # without modifying the generic system prompt.
        hint = _preset_tutor_hint(str(context.metadata.get("mastery_path_id") or ""))
        if hint:
            content = content + "\n\n" + hint
        return PromptBlock("mastery_tutor", content)

    def augment_kwargs(
        self,
        tool_name: str,
        kwargs: dict[str, Any],
        context: UnifiedContext,
    ) -> dict[str, Any]:
        if self.is_active(context) and tool_name in MASTERY_TOOL_NAMES:
            updated = dict(kwargs)
            updated["_mastery_path_id"] = str(context.metadata.get("mastery_path_id") or "").strip()
            updated["_session_id"] = str(context.session_id or "").strip()
            updated["_turn_id"] = str(context.metadata.get("turn_id") or "").strip()
            return updated
        return kwargs

    def pre_loop_seed(self, context: UnifiedContext) -> str:
        _ = context
        return ""


def _prompt_text(prompts: dict[str, Any], path: tuple[str, ...]) -> str:
    value: Any = prompts
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value if isinstance(value, str) and value else ""


def _load_system_prompt(language: str) -> str:
    lang = "zh" if language.lower().startswith("zh") else "en"
    prompt = resources.files(__package__).joinpath("prompts", lang, "system.md")
    return prompt.read_text(encoding="utf-8").strip()


def _preset_tutor_hint(path_id: str) -> str:
    """Return the ``tutor_hint`` from a preset whose id matches *path_id*.

    Returns an empty string when no preset matches, so callers can simply
    check truthiness.  Import is lazy to avoid a circular dependency at
    module load (``deeptutor.learning.presets`` imports from
    ``deeptutor.learning.models`` which is fine, but keeping it lazy is
    consistent with the rest of this module's import discipline).
    """
    if not path_id:
        return ""
    try:
        from deeptutor.learning.presets import load_preset

        data = load_preset(path_id)
        return str(data.get("tutor_hint") or "").strip()
    except Exception:
        return ""


__all__ = ["MasteryLoopCapability"]
