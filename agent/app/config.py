# Settings for the agent service, read from the .env file.

import os

from dotenv import load_dotenv

load_dotenv()

# Where the CRUD API lives, for example http://127.0.0.1:8000 (see .env.example: why not "localhost")
# This is the ONLY way the agent service reaches HR data. It has no database password.
CRUD_API_URL = os.getenv("CRUD_API_URL", "")

# Stop right away with a clear message if the setting is missing.
if CRUD_API_URL == "":
    raise RuntimeError("CRUD_API_URL is missing. Copy .env.example to .env and fill it in.")

# The AI model, running locally in Ollama. Download it first with:  ollama pull qwen3:4b-instruct
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")

# How much text (in tokens) the model reads at once. Bigger = room for more, but needs more GPU memory.
# 8192 leaves HR room for a long chat. On a 4 GB GPU part of it runs on the CPU: slower, but the
# answers are the same. See .env.example for a table.
OLLAMA_CONTEXT_TEXT = os.getenv("OLLAMA_CONTEXT", "8192")
if not OLLAMA_CONTEXT_TEXT.isdigit():
    raise RuntimeError(f"OLLAMA_CONTEXT must be a whole number, like 4096 (it is {OLLAMA_CONTEXT_TEXT!r}).")
OLLAMA_CONTEXT = int(OLLAMA_CONTEXT_TEXT)
