# PayTestAgent: AI-Powered ISO 20022 Test Generation

AI agent that generates test cases for ISO 20022 payment messages.

**Stack:** LangGraph, Gemini API, FastAPI, Docker

## Run locally

    docker build -t paytest-agent .
    docker run --rm -p 8000:8000 --env-file .env paytest-agent

Then open http://localhost:8000/docs

## Example request

    curl -X POST http://localhost:8000/generate \
      -H "Content-Type: application/json" \
      -d '{"message_type": "pacs.008", "requirements": "Cross-border credit transfer with UETR"}'

## Roadmap
- [x] Week 1: API + Docker
- [ ] Week 2: Retrieval improvements
- [ ] Week 3: Tracing + guardrails
- [ ] Week 4: Evals in CI + multi-agent
- [ ] Week 5: Human approval, audit log, Azure deploy
- [ ] Week 6: Architecture diagram + demo video
