# Settings for the agent service, read from the .env file.

import os

from dotenv import load_dotenv

load_dotenv()

# Where the CRUD API lives, for example http://localhost:8000
# This is the ONLY way the agent service reaches HR data. It has no database password.
CRUD_API_URL = os.getenv("CRUD_API_URL", "")

# Stop right away with a clear message if the setting is missing.
if CRUD_API_URL == "":
    raise RuntimeError("CRUD_API_URL is missing. Copy .env.example to .env and fill it in.")

# The AI model, running locally in Ollama. Download it first with:  ollama pull qwen3:4b-instruct
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct")
