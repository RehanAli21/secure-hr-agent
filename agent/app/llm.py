# The AI model: a local model running in Ollama, used through LlamaIndex.
#
# The whole rest of the code only calls ask_llm(). That keeps the model in one place,
# and lets the tests swap in a fake model that answers instantly.

import httpx
import ollama
from fastapi import HTTPException
from llama_index.core.base.llms.types import ChatMessage
from llama_index.core.tools import FunctionTool
# This package has no type information (no "py.typed" file), so strict Pylance would complain.
# We switch off ONLY that one warning, ONLY on this line.
from llama_index.llms.ollama import Ollama  # pyright: ignore[reportMissingTypeStubs]

from app.config import OLLAMA_CONTEXT, OLLAMA_MODEL, OLLAMA_URL

llm = Ollama(
    model=OLLAMA_MODEL,
    base_url=OLLAMA_URL,
    temperature=0,          # same question -> (almost) the same answer; no creative guessing
    # How much text the model reads at once, from .env (8192 = about 6,000 words). Bigger needs
    # more GPU memory: with 8192 the model doesn't fit in a 4 GB GPU, half runs on the CPU, ~3x
    # slower. We chose room (correct answers) over speed: 4096 left HR almost no room for the chat.
    context_window=OLLAMA_CONTEXT,
    request_timeout=120,    # small local models can be slow, give them 2 minutes
)
# Use a model that answers right away (like qwen3:4b-instruct), not a "thinking" model.
# Thinking models write a long explanation to themselves before every answer: much slower.


def ask_llm(messages: list[ChatMessage], tools: list[FunctionTool]) -> ChatMessage:
    """Send the conversation and the list of tools to the model. Returns its reply.

    The reply is either text for the user, or a request to call one of the tools.
    """
    try:
        response = llm.chat_with_tools(tools, chat_history=messages)
    except (ConnectionError, httpx.HTTPError):  # not running, wrong address, timeout...
        raise HTTPException(status_code=503, detail="The AI model is not reachable. Is Ollama running?")
    except ollama.ResponseError as error:  # Ollama runs, but refused: e.g. too much text, model missing
        raise HTTPException(status_code=503, detail=f"The AI model could not answer: {error.error}")
    return response.message
