"""Replace the body of run_agent() with a call to your existing LangGraph agent.

Keep this file as the ONLY place that knows about your agent internals.
main.py stays a thin API layer.

The model is created through get_llm(), so switching providers later
(Gemini -> Claude -> others) is a one-function change.
"""
import os

from langchain_google_genai import ChatGoogleGenerativeAI


def get_llm():
    # Reads GOOGLE_API_KEY from the environment automatically
    return ChatGoogleGenerativeAI(
        model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        temperature=0,
    )


def run_agent(message_type: str, requirements: str) -> dict:
    # TODO: replace with your real LangGraph graph, built with get_llm().
    # Quick smoke test so you can verify the key and container work:
    llm = get_llm()
    prompt = (
        f"Generate 3 concise test case titles for an ISO 20022 {message_type} "
        f"message. Requirements: {requirements}. Return one per line."
    )
    reply = llm.invoke(prompt)
    return {"test_cases": [line for line in reply.content.splitlines() if line.strip()]}
