from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Literal
import os
import httpx
import json

try:
    from .core.rag import retrieve_top_chunks, format_retrieved_context
except Exception:
    retrieve_top_chunks = None
    format_retrieved_context = None

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


async def _stream_openai_like(url: str, headers: Dict[str, str], payload: Dict) -> str:
    timeout = httpx.Timeout(120.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as r:
            if r.status_code >= 400:
                body = await r.aread()
                raise HTTPException(status_code=500, detail=f"LLM stream error {r.status_code}: {body[:400].decode('utf-8','ignore')}")

            async for line in r.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue

                data = line[len("data:") :].strip()
                if not data or data == "[DONE]":
                    break

                try:
                    obj = json.loads(data)
                except Exception:
                    continue

                choice = (obj.get("choices") or [{}])[0] or {}
                delta = (choice.get("delta") or {}).get("content")
                if delta is None:
                    delta = (choice.get("message") or {}).get("content")
                if not delta:
                    continue

                yield delta


async def _call_foundry_stream(messages: List[Dict[str, str]], temperature: float = 0.7):
    url = _foundry_chat_url()
    key = _required("FOUNDRY_API_KEY")
    payload = {"messages": messages, "temperature": temperature, "stream": True}
    headers = {"api-key": key, "Content-Type": "application/json"}
    async for delta in _stream_openai_like(url, headers, payload):
        yield delta


async def _call_nvidia_stream(messages: List[Dict[str, str]], temperature: float = 0.7):
    url = _nvidia_chat_url()
    key = _required("NVIDIA_API_KEY")
    model = _required("NVIDIA_MODEL")
    payload = {"model": model, "messages": messages, "temperature": temperature, "stream": True}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async for delta in _stream_openai_like(url, headers, payload):
        yield delta


def _rag_context(query: str, req: "AgentRunRequest") -> str:
    if not retrieve_top_chunks or not format_retrieved_context:
        return ""

    sources = []
    if req.use_context in ("classroom", "both"):
        sources.append(("TURMA", req.classroom_context))
    if req.use_context in ("student", "both"):
        sources.append(("ALUNO", req.student_context))

    if not sources:
        return ""

    chunks = retrieve_top_chunks(query=query, sources=sources, top_k=7, chunk_chars=1400, overlap=200, min_score=0.03)
    return format_retrieved_context(chunks)

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

    user_content = req.prompt.strip()
    rag_context = _rag_context(user_content, req)
    if rag_context:
        user_content = (
            user_content
            + "\n\nBASE RELEVANTE (trechos selecionados automaticamente):\n"
            + rag_context
            + "\n\nUse apenas o que for relevante. Se usar algum trecho, cite o TRECHO correspondente."
        )
    else:
        context_block = ""
        if req.use_context in ("classroom", "both") and req.classroom_context:
            context_block += f"\n\n[BASE DA TURMA]\n{req.classroom_context.strip()}\n"
        if req.use_context in ("student", "both") and req.student_context:
            context_block += f"\n\n[BASE DO ALUNO]\n{req.student_context.strip()}\n"
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


@app.post("/api/v1/agents/run/stream")
async def run_agent_stream_v1(req: AgentRunRequest):
    agent_id = req.agent
    system = _agent_system_prompt(agent_id)

    user_content = req.prompt.strip()
    rag_context = _rag_context(user_content, req)
    if rag_context:
        user_content = (
            user_content
            + "\n\nBASE RELEVANTE (trechos selecionados automaticamente):\n"
            + rag_context
            + "\n\nUse apenas o que for relevante. Se usar algum trecho, cite o TRECHO correspondente."
        )
    else:
        context_block = ""
        if req.use_context in ("classroom", "both") and req.classroom_context:
            context_block += f"\n\n[BASE DA TURMA]\n{req.classroom_context.strip()}\n"
        if req.use_context in ("student", "both") and req.student_context:
            context_block += f"\n\n[BASE DO ALUNO]\n{req.student_context.strip()}\n"
        if context_block:
            user_content = user_content + "\n\nUse as bases acima quando fizer sentido. Se não ajudar, ignore." + context_block

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

    engine = req.engine or "FOUNDRY"

    async def gen():
        yield f"data: {json.dumps({'type': 'meta', 'engineUsed': engine}, ensure_ascii=False)}\n\n"
        try:
            if engine == "NVIDIA":
                async for delta in _call_nvidia_stream(messages, temperature=req.temperature):
                    yield f"data: {json.dumps({'type': 'delta', 'delta': delta}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                return

            async for delta in _call_foundry_stream(messages, temperature=req.temperature):
                yield f"data: {json.dumps({'type': 'delta', 'delta': delta}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        except Exception as e:
            msg = str(getattr(e, "detail", None) or str(e) or "stream error")
            yield f"data: {json.dumps({'type': 'error', 'error': msg[:500]}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

# ✅ Alias para facilitar o front: /agents/run
@app.post("/agents/run", response_model=AgentRunResponse)
async def run_agent_alias(req: AgentRunRequest):
    return await run_agent_v1(req)


@app.post("/agents/run/stream")
async def run_agent_stream_alias(req: AgentRunRequest):
    return await run_agent_stream_v1(req)

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
