import os
import time
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.agent import run_agent

app = FastAPI(title="PayTestAgent", version="0.1.0")


class GenerateRequest(BaseModel):
    message_type: str = Field(..., examples=["pacs.008"], max_length=20)
    requirements: str = Field(..., min_length=5, max_length=5000)


class GenerateResponse(BaseModel):
    test_cases: list
    elapsed_seconds: float


@app.get("/health")
def health():
    return {"status": "ok", "api_key_configured": bool(os.getenv("GOOGLE_API_KEY"))}


@app.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest):
    start = time.perf_counter()
    try:
        result = run_agent(req.message_type, req.requirements)
    except Exception as exc:  # keep errors clean for API callers
        raise HTTPException(status_code=500, detail=f"Agent failed: {exc}")
    return GenerateResponse(
        test_cases=result["test_cases"],
        elapsed_seconds=round(time.perf_counter() - start, 3),
    )
