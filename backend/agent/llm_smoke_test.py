"""Quick smoke test: verify the Groq API key and model work."""
import os
import sys

os.environ.setdefault("GROQ_API_KEY", os.environ.get("GROQ_API_KEY", ""))

from backend.agent.llm import ask  # noqa: E402

response = ask("Say OK")
print(f"Groq smoke test response: {response}")
assert response.strip(), "Empty response from Groq API"
print("SMOKE TEST PASSED")
