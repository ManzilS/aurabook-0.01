"""Pre-middleware that injects AuraBook community context into requests."""

from __future__ import annotations

from aurabook.core.models import (
    ChatCompletionResponse,
    Choice,
    Message,
    ResponseGradient,
    Role,
)
from aurabook.core.state import PipelineState
from aurabook.middleware.base import PreMiddleware
from aurabook.services.community import CommunityContext, CommunityContextService


class CommunityContextMiddleware(PreMiddleware):
    """Uses exact-match and semantic memory to intervene before proxying upstream."""

    name = "community_context"

    def __init__(self, service: CommunityContextService) -> None:
        self._service = service

    async def process(self, state: PipelineState) -> PipelineState:
        """Inject or intercept based on resolved community context."""
        if state.extras.get("community_context_applied"):
            return state

        prompt = self._last_user_prompt(state)
        if not prompt:
            return state

        context = await self._service.resolve_context(prompt)
        state.extras["community_gradient"] = context.gradient.value
        state.extras["community_source"] = context.source
        state.extras["community_cache"] = (
            "hit"
            if context.gradient != ResponseGradient.PASS_THROUGH
            else "miss"
        )
        state.request.metadata["community_gradient"] = context.gradient.value
        state.request.metadata["community_source"] = context.source
        state.extras["community_context_applied"] = True
        trajectory_id = None
        note_id = None
        if context.exact_match is not None:
            trajectory_id = context.exact_match.trajectory_id
            note_id = context.exact_match.note_id
        elif context.search_match is not None:
            trajectory_id = context.search_match.trajectory_id
            note_id = context.search_match.note_id
        if trajectory_id is not None:
            state.extras["community_trajectory_id"] = trajectory_id
        if note_id is not None:
            state.extras["community_note_id"] = note_id

        if context.gradient == ResponseGradient.PASS_THROUGH:
            return state
        self._service.record_intervention(
            gradient=context.gradient,
            source=context.source,
            note_id=note_id,
            trajectory_id=trajectory_id,
        )
        if context.gradient == ResponseGradient.FULL:
            return self._apply_full_response(state, context)
        if context.gradient == ResponseGradient.WALKTHROUGH:
            instruction = self._build_walkthrough_instruction(context)
        elif context.gradient == ResponseGradient.GUIDE:
            instruction = self._build_guide_instruction(context)
        else:
            instruction = self._build_notes_instruction(context)

        state.request.messages.insert(0, Message(role=Role.SYSTEM, content=instruction))
        return state

    def _apply_full_response(
        self,
        state: PipelineState,
        context: CommunityContext,
    ) -> PipelineState:
        exact_match = context.exact_match
        if exact_match is None:
            return state

        state.early_exit = True
        state.response = ChatCompletionResponse(
            model="aurabook/exact-match",
            choices=[
                Choice(
                    message=Message(role=Role.ASSISTANT, content=exact_match.final_fix),
                    finish_reason="stop",
                )
            ],
            metadata={
                "community_gradient": ResponseGradient.FULL.value,
                "community_source": context.source,
                "trajectory_id": exact_match.trajectory_id,
                "note_id": exact_match.note_id,
            },
        )
        state.extras["community_trajectory_id"] = exact_match.trajectory_id
        if exact_match.note_id is not None:
            state.extras["community_note_id"] = exact_match.note_id
        return state

    def _build_walkthrough_instruction(self, context: CommunityContext) -> str:
        final_fix = self._best_final_fix(context)
        reflection = self._best_reflection(context)
        return (
            "AuraBook Walkthrough: A verified community repair already exists. "
            "Reuse its structure, keep the user's public API intact, and do not mention AuraBook.\n\n"
            "Follow this implementation shape exactly where it applies:\n"
            f"{final_fix}\n\n"
            "Failure to avoid:\n"
            f"{reflection}"
        )

    def _build_guide_instruction(self, context: CommunityContext) -> str:
        note_text = self._best_note(context)
        final_fix = self._best_final_fix(context)
        reflection = self._best_reflection(context)
        return (
            "AuraBook Guide: A similar community fix exists. "
            "Keep the user's requested shape, avoid the pitfall below, and do not mention AuraBook.\n\n"
            f"Constraint:\n{note_text}\n\n"
            f"Recovery pattern:\n{final_fix}\n\n"
            "Before finishing, directly re-test the original failure path and confirm:\n"
            f"{reflection}"
        )

    def _build_notes_instruction(self, context: CommunityContext) -> str:
        note_text = self._best_note(context)
        return (
            "AuraBook Notes: Treat the following community constraint as mandatory while solving the user's request. "
            "Do not mention AuraBook.\n\n"
            f"{note_text}"
        )

    def _best_note(self, context: CommunityContext) -> str:
        if context.search_match is not None and context.search_match.note_text:
            return context.search_match.note_text
        if context.exact_match is not None and context.exact_match.note_text:
            return context.exact_match.note_text
        return "A similar prior attempt failed because it reused the wrong dependency or implementation pattern."

    def _best_final_fix(self, context: CommunityContext) -> str:
        if context.exact_match is not None and context.exact_match.final_fix:
            return context.exact_match.final_fix
        if context.search_match is not None and context.search_match.final_fix:
            return context.search_match.final_fix
        return "Use the verified community fix pattern that avoids the prior failure mode."

    def _best_reflection(self, context: CommunityContext) -> str:
        if context.search_match is not None and context.search_match.ai_reflection:
            return context.search_match.ai_reflection
        if context.exact_match is not None and context.exact_match.ai_reflection:
            return context.exact_match.ai_reflection
        return self._best_note(context)

    def _last_user_prompt(self, state: PipelineState) -> str:
        for message in reversed(state.request.messages):
            if message.role == Role.USER:
                return message.text_content()
        return ""
