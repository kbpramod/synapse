import os
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

api_key = os.getenv("AICREDITS_API_KEY") or os.getenv("OPENAI_API_KEY")
base_url = os.getenv("AICREDITS_BASE_URL", "https://api.aicredits.in/v1")

ai_client = AsyncOpenAI(
    api_key=api_key,
    base_url=base_url
)

