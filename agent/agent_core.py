"""Travel Agent core — LangChain tool-calling agent via MiniMax AI.

MiniMax exposes an OpenAI-compatible chat completions API at
https://api.minimaxi.chat/v1, authenticated with MINIMAX_API_KEY.
We point LangChain's ChatOpenAI at that endpoint so the rest of the
codebase needs no changes.

`TravelAgent.run(user_message)` returns the final assistant text, ready for
Telegram. Internally LangChain handles the ReAct loop:
- LLM emits tool calls
- We execute them (Playwright)
- The results feed back into the next LLM turn
- Stops when the LLM produces a final answer (or hits max_iterations)
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI

from agent.prompts import render_system_prompt
from agent.tools_registry import build_tools
from config.settings import settings

logger = logging.getLogger(__name__)

# Reasoning-tag patterns emitted by some models (e.g. MiniMax M2.x, DeepSeek-R1).
# Strip these so internal chain-of-thought never reaches the user or chat history.
_THINK_BLOCK_RE = re.compile(
    r"<\s*(think|thinking|reasoning|reflection)\s*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Some models leave an unclosed opening tag at the start of streaming output.
_UNCLOSED_THINK_RE = re.compile(
    r"^\s*<\s*(think|thinking|reasoning|reflection)\s*>.*?(?=\n\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> and similar reasoning blocks from model output."""
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    cleaned = _UNCLOSED_THINK_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


class TravelAgent:
    """Stateless travel agent. One instance is enough for the whole bot process."""

    def __init__(
        self,
        model_name: str | None = None,
        temperature: float = 0.2,
        max_iterations: int = 12,
    ) -> None:
        if not settings.minimax_api_key:
            raise RuntimeError(
                "MINIMAX_API_KEY is not set. "
                "Add your MiniMax API key to the .env file."
            )

        resolved_model = model_name or settings.minimax_model_name
        logger.info("Initializing TravelAgent with model=%s endpoint=%s",
                    resolved_model, settings.minimax_base_url)

        self.llm = ChatOpenAI(
            model=resolved_model,
            temperature=temperature,
            api_key=settings.minimax_api_key,
            base_url=settings.minimax_base_url,
            timeout=120,
        )
        self.tools = build_tools()

        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", render_system_prompt()),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        agent = create_tool_calling_agent(llm=self.llm, tools=self.tools, prompt=self.prompt)
        self.executor = AgentExecutor(
            agent=agent,
            tools=self.tools,
            max_iterations=max_iterations,
            handle_parsing_errors=True,
            return_intermediate_steps=False,
            verbose=False,
        )

    async def run(self, user_message: str, chat_history: Optional[list] = None) -> str:
        """Execute the agent loop against one user message. Returns final answer text."""
        try:
            result = await self.executor.ainvoke(
                {
                    "input": user_message,
                    "chat_history": chat_history or [],
                }
            )
            output = result.get("output", "")
            if isinstance(output, list):
                pieces = []
                for chunk in output:
                    if isinstance(chunk, dict) and "text" in chunk:
                        pieces.append(chunk["text"])
                    elif isinstance(chunk, str):
                        pieces.append(chunk)
                output = "\n".join(pieces)
            return _strip_thinking(str(output))
        except Exception as e:
            logger.exception("Agent run failed")
            return (
                "Maaf, ada kendala saat memproses permintaanmu. "
                f"Detail teknis: {type(e).__name__}. Silakan coba lagi sebentar lagi."
            )


_global_agent: Optional[TravelAgent] = None


def get_agent() -> TravelAgent:
    """Lazy singleton — first call builds the agent, subsequent calls reuse it."""
    global _global_agent
    if _global_agent is None:
        _global_agent = TravelAgent()
    return _global_agent
