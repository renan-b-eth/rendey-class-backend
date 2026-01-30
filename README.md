# Rendey Class Backend (Hugging Face Space)

Backend **FastAPI** para rodar os **agentes pedagógicos** (copiloto do professor) usando:

- **Foundry (Azure OpenAI)** como motor padrão
- **NVIDIA (OpenAI-compatible / HV100 / NIM)** como opção

## Endpoints

- `GET /health`
- `GET /api/v1/agents` → lista os agentes disponíveis
- `POST /api/v1/agents/run` → executa um agente em Foundry (default) ou NVIDIA

### POST `/api/v1/agents/run`

Body JSON:

```json
{
  "agent": "quiz",
  "engine": "FOUNDRY",
  "prompt": "Crie um quiz de frações para 6º ano...",
  "use_context": "none",
  "classroom_context": "... opcional ...",
  "student_context": "... opcional ...",
  "temperature": 0.7
}
```

Resposta:

```json
{
  "ok": true,
  "engineUsed": "FOUNDRY",
  "output": "..."
}
```

## Variáveis de ambiente

### Foundry (Azure OpenAI) — **padrão**

> `FOUNDRY_MODEL` aqui é o **deployment name** do Azure.

- `FOUNDRY_API_BASE_URL` = `https://SEU-RESOURCE.openai.azure.com`
- `FOUNDRY_API_KEY` = chave do Azure
- `FOUNDRY_MODEL` = deployment name (ex: `meu-gpt`)
- `FOUNDRY_API_VERSION` = (opcional) `2024-02-15-preview`

### NVIDIA (OpenAI compatible)

- `NVIDIA_API_BASE_URL` = `https://integrate.api.nvidia.com` (ou sua base)
- `NVIDIA_API_KEY` = token `nvapi-...`
- `NVIDIA_MODEL` = modelo (ex: `meta/llama-3.1-70b-instruct` ou o que você tiver)

### CORS

- `CORS_ORIGINS` = lista separada por vírgula de origens do seu front (Vercel)
  - exemplo: `https://seu-front.vercel.app,http://localhost:3000`

## Como rodar local

```bash
cd api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 7860
```

Abra: `http://localhost:7860/docs`
