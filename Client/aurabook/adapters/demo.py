"""Offline upstream adapter used for AuraBook demo mode."""

from __future__ import annotations

import re
from typing import Any

from aurabook.adapters.base import AdapterBase
from aurabook.core.models import ChatCompletionRequest, ChatCompletionResponse, Choice, Message, Role

_CODE_BLOCK_PATTERN = re.compile(r"```[\w.+-]*\n(?P<code>[\s\S]*?)```")


class DemoAdapter(AdapterBase):
    """Deterministic adapter that simulates an upstream coding model."""

    name = "openai_proxy"
    supports_streaming = True

    def translate_to_ai(self, request: ChatCompletionRequest) -> dict[str, Any]:
        """Return a debug-friendly payload."""
        return request.model_dump(mode="json")

    def translate_to_universal(self, raw: dict[str, Any]) -> ChatCompletionResponse:
        """Validate a raw payload into the universal response shape."""
        return ChatCompletionResponse.model_validate(raw)

    async def send(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        """Return a deterministic coding response for offline demos."""
        response_text = self._build_response(request)
        return ChatCompletionResponse(
            model="aurabook-demo-adapter",
            choices=[
                Choice(
                    message=Message(role=Role.ASSISTANT, content=response_text),
                    finish_reason="stop",
                )
            ],
        )

    def _build_response(self, request: ChatCompletionRequest) -> str:
        prompt = self._last_message(request, Role.USER)
        system_text = self._last_message(request, Role.SYSTEM)
        lowered_prompt = prompt.lower()
        lowered_system = system_text.lower()

        if any(marker in lowered_prompt for marker in ("that fixed it", "works now", "thanks")):
            return "Glad it works. Capture the successful fix and keep the safer pattern."

        walkthrough_code = self._extract_code_block(system_text)
        if walkthrough_code and "aurabook walkthrough:" in lowered_system:
            return f"```python\n{walkthrough_code.strip()}\n```"

        if "httpx.asyncclient" in lowered_system or "async request" in lowered_prompt:
            return (
                "```python\n"
                "import httpx\n\n"
                "async def fetch_data(url: str) -> dict:\n"
                "    async with httpx.AsyncClient() as client:\n"
                "        response = await client.get(url)\n"
                "        response.raise_for_status()\n"
                "        return response.json()\n"
                "```"
            )
        if "time.sleep" in lowered_prompt or "blocking sleep" in lowered_prompt:
            return (
                "```python\n"
                "import asyncio\n\n"
                "async def poll() -> None:\n"
                "    await asyncio.sleep(2)\n"
                "    await fetch_updates()\n"
                "```"
            )
        if "corsmiddleware" in lowered_system or "cors" in lowered_prompt:
            return (
                "```python\n"
                "from fastapi import FastAPI\n"
                "from fastapi.middleware.cors import CORSMiddleware\n\n"
                "app = FastAPI()\n"
                "app.add_middleware(\n"
                "    CORSMiddleware,\n"
                "    allow_origins=['http://localhost:3000'],\n"
                "    allow_methods=['*'],\n"
                "    allow_headers=['*'],\n"
                ")\n"
                "```"
            )
        if "settingwithcopywarning" in lowered_prompt or ".loc" in lowered_system:
            return (
                "```python\n"
                "mask = df['active']\n"
                "df.loc[mask, 'price'] = df.loc[mask, 'price'].fillna(0).astype(float)\n"
                "```"
            )
        if "react" in lowered_prompt or "maximum update depth" in lowered_prompt:
            return (
                "```tsx\n"
                "useEffect(() => {\n"
                "  let cancelled = false;\n"
                "  fetch(`/api/notes?project=${projectId}`)\n"
                "    .then((r) => r.json())\n"
                "    .then((payload) => {\n"
                "      if (!cancelled) {\n"
                "        setNotes(payload);\n"
                "      }\n"
                "    });\n"
                "  return () => {\n"
                "    cancelled = true;\n"
                "  };\n"
                "}, [projectId]);\n"
                "```"
            )

        if "aurabook notes:" in lowered_system:
            return f"Applied community constraint: {self._guidance_preview(system_text)}"
        if "aurabook guide:" in lowered_system:
            return f"Applied recovery guide: {self._guidance_preview(system_text)}"
        return "Offline demo response: no community memory matched, so the request passed through unchanged."

    def _last_message(self, request: ChatCompletionRequest, role: Role) -> str:
        for message in reversed(request.messages):
            if message.role == role:
                return message.text_content()
        return ""

    def _extract_code_block(self, text: str) -> str:
        match = _CODE_BLOCK_PATTERN.search(text)
        if not match:
            return ""
        return match.group("code")

    def _guidance_preview(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if line.lower().startswith("aurabook "):
                continue
            if line.endswith(":"):
                continue
            return line
        return "avoid the prior failure mode"
