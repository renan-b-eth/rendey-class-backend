---
title: rendey-class
sdk: docker
app_port: 7860
pinned: false
---

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
