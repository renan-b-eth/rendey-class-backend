from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Literal
import os
import httpx
import json
import asyncio
import hashlib
import time

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

_RAG_CACHE: Dict[str, Dict[str, Any]] = {}
_RATE_BUCKET: Dict[str, List[float]] = {}

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
def list_agents_v1(request: Request):
    _require_internal_key(request)
    return _agents_list()

# ✅ Alias para facilitar o front: /agents
@app.get("/agents")
def list_agents_alias(request: Request):
    _require_internal_key(request)
    return _agents_list()

# -----------------------------
# LLM Engines
# -----------------------------
Engine = Literal["FOUNDRY", "NVIDIA"]

def _optional(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _require_internal_key(request: Request):
    expected = _optional("AGENTS_INTERNAL_API_KEY")
    if not expected:
        return
    got = (request.headers.get("x-agents-key") or "").strip()
    if got != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _rate_limit(request: Request, *, scope: str, limit: int = 30, window_seconds: int = 60):
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (request.client.host if request.client else "")
    key = f"{scope}:{ip}"
    now = time.time()
    w = float(window_seconds)
    max_hits = int(limit)

    hits = _RATE_BUCKET.get(key) or []
    hits = [t for t in hits if (now - t) <= w]
    if len(hits) >= max_hits:
        raise HTTPException(status_code=429, detail="Rate limit")
    hits.append(now)
    _RATE_BUCKET[key] = hits

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


async def _rag_context(query: str, req: "AgentRunRequest") -> str:
    if not retrieve_top_chunks or not format_retrieved_context:
        return ""

    sources = []
    if req.use_context in ("classroom", "both"):
        if (req.classroom_context or "").strip():
            sources.append(("TURMA", req.classroom_context, 1.0))
    if req.use_context in ("student", "both"):
        if (req.student_context or "").strip():
            sources.append(("ALUNO", req.student_context, 1.15))

    if not sources:
        return ""

    chunks = retrieve_top_chunks(
        query=query,
        sources=sources,
        top_k=30,
        chunk_chars=1400,
        overlap=220,
        min_score=0.03,
        dedupe_threshold=0.92,
    )

    base_url = _optional("EMBEDDINGS_API_BASE_URL")
    model = _optional("EMBEDDINGS_MODEL") or "text-embedding-3-small"
    api_key = _optional("EMBEDDINGS_API_KEY")

    if base_url and chunks:
        try:
            texts = [query] + [c.text[:1800] for c in chunks]
            vectors = await _embed_texts(base_url, api_key, model, texts)
            if vectors and len(vectors) == len(texts):
                qv = vectors[0]
                dv = vectors[1:]

                max_lex = max([c.score for c in chunks] + [1e-9])
                reranked = []
                for ch, vec in zip(chunks, dv):
                    emb = _cos_sim(qv, vec)
                    lex = float(ch.score) / float(max_lex)
                    score = (0.62 * emb) + (0.38 * lex)
                    reranked.append((score, ch))
                reranked.sort(key=lambda x: x[0], reverse=True)
                chunks = [ch for _, ch in reranked[:12]]
        except Exception:
            pass

    chunks = chunks[:7]
    return format_retrieved_context(chunks, max_chars=9000)


def _cos_sim(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        x = float(a[i])
        y = float(b[i])
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


async def _embed_texts(base_url: str, api_key: str, model: str, texts: List[str]) -> List[List[float]]:
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        url = f"{base}/embeddings"
    else:
        url = f"{base}/v1/embeddings"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "input": texts}
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=payload)
    if r.status_code >= 400:
        raise HTTPException(status_code=500, detail=f"Embeddings error {r.status_code}: {r.text[:200]}")
    data = r.json()
    out: List[List[float]] = []
    items = data.get("data") or []
    for item in items:
        out.append(item.get("embedding") or [])
    if len(out) != len(texts):
        raise HTTPException(status_code=500, detail="Embeddings returned unexpected length")
    return out


def _structured_from_markdown(md: str) -> Dict[str, Any]:
    text = (md or "").strip()
    lines = [l.rstrip() for l in text.splitlines()]
    title = None
    for l in lines[:12]:
        s = l.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            break
    if not title:
        for l in lines[:12]:
            s = l.strip()
            if len(s) >= 4:
                title = s
                break

    sections: List[Dict[str, Any]] = []
    cur = None
    for l in lines:
        s = l.strip()
        if s.startswith("## "):
            if cur:
                cur["content"] = "\n".join(cur["content"]).strip()
                sections.append(cur)
            cur = {"title": s[3:].strip(), "content": []}
            continue
        if cur is not None:
            cur["content"].append(l)
    if cur:
        cur["content"] = "\n".join(cur["content"]).strip()
        sections.append(cur)

    checklist: List[str] = []
    for sec in sections:
        if "checklist" in (sec.get("title") or "").lower():
            for l in (sec.get("content") or "").splitlines():
                ls = l.strip()
                if ls.startswith("-"):
                    checklist.append(ls.lstrip("-").strip())

    return {
        "title": title,
        "sections": sections,
        "checklist": checklist,
    }

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
    structured: Optional[Dict[str, Any]] = None

@app.post("/api/v1/agents/run", response_model=AgentRunResponse)
async def run_agent_v1(req: AgentRunRequest, request: Request):
    _require_internal_key(request)
    _rate_limit(request, scope="agents_run", limit=int(_optional("AGENTS_RUN_RPM") or "30"), window_seconds=60)
    agent_id = req.agent
    system = _agent_system_prompt(agent_id)

    user_content = req.prompt.strip()

    ctx_key = hashlib.sha1(
        ("|".join([
            user_content,
            req.use_context,
            (req.classroom_context or ""),
            (req.student_context or ""),
        ])).encode("utf-8", "ignore")
    ).hexdigest()
    cached = _RAG_CACHE.get(ctx_key)
    if cached and (time.time() - float(cached.get("ts") or 0)) < float(_optional("RAG_CACHE_TTL_SECONDS") or "600"):
        rag_context = str(cached.get("ctx") or "")
    else:
        rag_context = await _rag_context(user_content, req)
        _RAG_CACHE[ctx_key] = {"ts": time.time(), "ctx": rag_context}
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
        structured = _structured_from_markdown(output or "")
        return AgentRunResponse(engineUsed="NVIDIA", output=output or "", structured=structured)

    output = await _call_foundry(messages, temperature=req.temperature)
    structured = _structured_from_markdown(output or "")
    return AgentRunResponse(engineUsed="FOUNDRY", output=output or "", structured=structured)


@app.post("/api/v1/agents/run/stream")
async def run_agent_stream_v1(req: AgentRunRequest, request: Request):
    _require_internal_key(request)
    _rate_limit(request, scope="agents_stream", limit=int(_optional("AGENTS_STREAM_RPM") or "20"), window_seconds=60)
    agent_id = req.agent
    system = _agent_system_prompt(agent_id)

    user_content = req.prompt.strip()
    ctx_key = hashlib.sha1(
        ("|".join([
            "stream",
            user_content,
            req.use_context,
            (req.classroom_context or ""),
            (req.student_context or ""),
        ])).encode("utf-8", "ignore")
    ).hexdigest()
    cached = _RAG_CACHE.get(ctx_key)
    if cached and (time.time() - float(cached.get("ts") or 0)) < float(_optional("RAG_CACHE_TTL_SECONDS") or "600"):
        rag_context = str(cached.get("ctx") or "")
    else:
        rag_context = await _rag_context(user_content, req)
        _RAG_CACHE[ctx_key] = {"ts": time.time(), "ctx": rag_context}
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

    ping_interval = float((os.getenv("SSE_PING_INTERVAL_SECONDS") or "15").strip() or "15")
    stream_wait_timeout = float((os.getenv("SSE_STREAM_WAIT_TIMEOUT_SECONDS") or "120").strip() or "120")

    async def gen():
        yield f"data: {json.dumps({'type': 'meta', 'engineUsed': engine}, ensure_ascii=False)}\n\n"
        last_activity = asyncio.get_event_loop().time()

        async def emit_ping():
            yield ": ping\n\n"

        try:
            if engine == "NVIDIA":
                it = _call_nvidia_stream(messages, temperature=req.temperature).__aiter__()
            else:
                it = _call_foundry_stream(messages, temperature=req.temperature).__aiter__()

            while True:
                if await request.is_disconnected():
                    return

                idle = asyncio.get_event_loop().time() - last_activity
                if idle > stream_wait_timeout:
                    yield f"data: {json.dumps({'type': 'error', 'error': 'timeout aguardando resposta do modelo'}, ensure_ascii=False)}\n\n"
                    return

                try:
                    delta = await asyncio.wait_for(it.__anext__(), timeout=ping_interval)
                except asyncio.TimeoutError:
                    async for p in emit_ping():
                        yield p
                    continue
                except StopAsyncIteration:
                    break

                if delta:
                    last_activity = asyncio.get_event_loop().time()
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
async def run_agent_alias(req: AgentRunRequest, request: Request):
    return await run_agent_v1(req, request)


@app.post("/agents/run/stream")
async def run_agent_stream_alias(req: AgentRunRequest, request: Request):
    return await run_agent_stream_v1(req, request)

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
