import os, requests
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent.parent / ".env", override=True)
key = os.getenv("GEMINI_API_KEY", "")
r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
models = r.json().get("models", [])
for m in models:
    if "generateContent" in m.get("supportedGenerationMethods", []):
        print(m.get("name", ""))
