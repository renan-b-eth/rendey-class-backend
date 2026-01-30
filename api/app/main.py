from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
import os
import random

try:
    from .core.prompts import AGENTS
except Exception:
    AGENTS = {}

app = FastAPI(title="Rendey Class API", version="0.1.0")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/v1/agents")
def list_agents():
    return [{"id": k, **v} for k, v in AGENTS.items()]

class QuizRequest(BaseModel):
    title: str = "Exam"
    subject: str = "Matemática"
    grade: str = "7º ano"
    topic: str = "Frações"
    count: int = Field(default=10, ge=5, le=30)

@app.post("/api/v1/generate/quiz")
def generate_quiz(req: QuizRequest):
    # Free default (mock). You can wire your own LLM here later.
    n = int(req.count)
    questions = []
    for i in range(n):
        questions.append({
            "id": str(i+1),
            "type": "mcq",
            "prompt": f"[{req.subject} • {req.grade}] {req.topic} — Questão {i+1}",
            "options": [f"A) Alternativa A", f"B) Alternativa B", f"C) Alternativa C", f"D) Alternativa D"],
            "answerIndex": i % 4,
            "explanation": "Mock mode: configure your LLM in this API to generate real questions.",
            "points": 1
        })
    return {"title": req.title, "subject": req.subject, "grade": req.grade, "topic": req.topic, "questions": questions}
