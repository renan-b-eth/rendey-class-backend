from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Literal
import os
import httpx

try:
    from .core.prompts import AGENTS
except Exception:
    AGENTS = {}

app = FastAPI(title="Rendey Class API", version="0.2.1")

# -----------------------------
# CORS (Front-end on Vercel)
# -----------------------------
_default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    # ✅ adicione sua Vercel aqui (ou via env)
    "https://rendey-class-front.vercel.app",
]
_env_origins = os.getenv("CORS_ORIGINS", "").strip()
if _env_origins:
    _default_origins = [o.strip() for o in _env_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_default_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# ROOT + HEALTH (evita 404 no /)
# -----------------------------
@app.get("/")
def root():
    return {"ok": True, "service": "rendey-class-api", "version": "0.2.1"}

@app.get("/health")
def health():
    return {"status": "ok"}

# -----------------------------
# Agents listing
# -----------------------------
def _agents_list():
    # sempre retorna uma lista, mesmo se AGENTS estiver vazio
    if isinstance(AGENTS, dict) and AGENTS:
        return [{"id": k, **(v or {})} for k, v in AGENTS.items()]
    return []

@app.get("/api/v1/agents")
def list_agents_v1():
    return _agents_list()

# ✅ Alias para facilitar o front: /agents
@app.get("/agents")
def list_agents_alias():
    return _agents_list()

# -----------------------------
# LLM Engines
# -----------------------------
Engine = Literal["FOUNDRY", "NVIDIA"]

def _required(name: str) -> str:
    v = (os.getenv(name) or "").strip()
    if not v or "COLE_AQUI" in v:
        raise HTTPException(status_code=500, detail=f"Missing env: {name}")
    return v

def _required_url(name: str) -> str:
    v = _required(name)
    if not (v.startswith("http://") or v.startswith("https://")):
        raise HTTPException(status_code=500, detail=f"Invalid URL env: {name}")
    return v.rstrip("/")

def _foundry_chat_url() -> str:
    base = _required_url("FOUNDRY_API_BASE_URL")
    deployment = _required("FOUNDRY_MODEL")
    api_version = (os.getenv("FOUNDRY_API_VERSION") or "2024-02-15-preview").strip()
    return f"{base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

def _nvidia_chat_url() -> str:
    base = _required_url("NVIDIA_API_BASE_URL")
    return f"{base}/v1/chat/completions"

def _agent_system_prompt(agent_id: str) -> str:
    agent = AGENTS.get(agent_id) if isinstance(AGENTS, dict) else None
    if not agent:
        return (
            "Você é um copiloto pedagógico para professores da rede pública. "
            "Seja prático, claro e entregue material pronto para imprimir e aplicar."
        )
    return (
        "Você é um copiloto pedagógico para professores da rede pública. "
        "Responda em português do Brasil, com linguagem simples e aplicável.\n\n"
        f"PERSONA/AGENTE: {agent.get('name','')}\n"
        f"INSTRUÇÃO: {agent.get('role','')}"
    )

async def _call_foundry(messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
    url = _foundry_chat_url()
    key = _required("FOUNDRY_API_KEY")
    payload = {"messages": messages, "temperature": temperature}
    timeout = httpx.Timeout(60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            url,
            headers={"api-key": key, "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=500, detail=f"Foundry error {r.status_code}: {r.text[:400]}")
    data = r.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

async def _call_nvidia(messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
    url = _nvidia_chat_url()
    key = _required("NVIDIA_API_KEY")
    model = _required("NVIDIA_MODEL")
    payload = {"model": model, "messages": messages, "temperature": temperature}
    timeout = httpx.Timeout(60.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=500, detail=f"NVIDIA error {r.status_code}: {r.text[:400]}")
    data = r.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

# -----------------------------
# Agent Run
# -----------------------------
class AgentRunRequest(BaseModel):
    agent: str = Field(default="quiz", description="agent id")
    engine: Engine = Field(default="FOUNDRY", description="FOUNDRY (default) or NVIDIA")
    prompt: str = Field(min_length=1)

    classroom_context: Optional[str] = None
    student_context: Optional[str] = None
    use_context: Literal["none", "classroom", "student", "both"] = "none"

    temperature: float = Field(default=0.7, ge=0.0, le=1.5)

class AgentRunResponse(BaseModel):
    ok: bool = True
    engineUsed: Engine
    output: str

@app.post("/api/v1/agents/run", response_model=AgentRunResponse)
async def run_agent_v1(req: AgentRunRequest):
    agent_id = req.agent
    system = _agent_system_prompt(agent_id)

    context_block = ""
    if req.use_context in ("classroom", "both") and req.classroom_context:
        context_block += f"\n\n[BASE DA TURMA]\n{req.classroom_context.strip()}\n"
    if req.use_context in ("student", "both") and req.student_context:
        context_block += f"\n\n[BASE DO ALUNO]\n{req.student_context.strip()}\n"

    user_content = req.prompt.strip()
    if context_block:
        user_content = user_content + "\n\nUse as bases acima quando fizer sentido. Se não ajudar, ignore." + context_block

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    engine = req.engine or "FOUNDRY"
    if engine == "NVIDIA":
        output = await _call_nvidia(messages, temperature=req.temperature)
        return AgentRunResponse(engineUsed="NVIDIA", output=output or "")

    output = await _call_foundry(messages, temperature=req.temperature)
    return AgentRunResponse(engineUsed="FOUNDRY", output=output or "")

# ✅ Alias para facilitar o front: /agents/run
@app.post("/agents/run", response_model=AgentRunResponse)
async def run_agent_alias(req: AgentRunRequest):
    return await run_agent_v1(req)

# -----------------------------
# Legacy demo endpoint (mock)
# -----------------------------
class QuizRequest(BaseModel):
    title: str = "Exam"
    subject: str = "Matemática"
    grade: str = "7º ano"
    topic: str = "Frações"
    count: int = Field(default=10, ge=5, le=30)

@app.post("/api/v1/generate/quiz")
def generate_quiz(req: QuizRequest):
    n = int(req.count)
    questions = []
    for i in range(n):
        questions.append(
            {
                "id": str(i + 1),
                "type": "mcq",
                "prompt": f"[{req.subject} • {req.grade}] {req.topic} — Questão {i + 1}",
                "options": ["A) Alternativa A", "B) Alternativa B", "C) Alternativa C", "D) Alternativa D"],
                "answerIndex": i % 4,
                "explanation": "Mock mode: configure your LLM in this API to generate real questions.",
                "points": 1,
            }
        )
    return {"title": req.title, "subject": req.subject, "grade": req.grade, "topic": req.topic, "questions": questions}
