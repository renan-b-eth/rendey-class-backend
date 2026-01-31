# ✅ BACK-END (HuggingFace) - FastAPI
from fastapi import APIRouter
from typing import List, Dict

router = APIRouter(prefix="/api/v1")

AGENTS: List[Dict] = [
    {"id": "planner", "name": "Planejador de Aula", "description": "Cria plano de aula completo com objetivos, competências e etapas.", "category": "Aula"},
    {"id": "quiz", "name": "Gerador de Quiz", "description": "Cria quiz com alternativas e gabarito.", "category": "Avaliação"},
    {"id": "exam", "name": "Criador de Prova", "description": "Monta prova por níveis e habilidades (fácil/médio/difícil).", "category": "Avaliação"},
    {"id": "recovery", "name": "Recuperação Paralela", "description": "Gera atividades de recuperação personalizadas.", "category": "Intervenção"},
    {"id": "rubric", "name": "Rubrica de Correção", "description": "Cria rubricas claras com critérios e níveis.", "category": "Avaliação"},
    {"id": "lesson_activities", "name": "Atividades da Aula", "description": "Sugere atividades práticas e dinâmicas.", "category": "Aula"},
    {"id": "project", "name": "Projeto Interdisciplinar", "description": "Cria projeto com etapas, produtos e avaliação.", "category": "Projeto"},
    {"id": "bncc", "name": "Alinhamento BNCC", "description": "Sugere habilidades/competências alinhadas.", "category": "Currículo"},
    {"id": "summary", "name": "Resumidor Didático", "description": "Resume conteúdo em linguagem pedagógica.", "category": "Conteúdo"},
    {"id": "study_plan", "name": "Plano de Estudos", "description": "Cria plano de estudo semanal para aluno/turma.", "category": "Aluno"},
    {"id": "adaptation", "name": "Adaptação Inclusiva", "description": "Adapta conteúdo para necessidades diversas.", "category": "Inclusão"},
    {"id": "intervention", "name": "Intervenção Pedagógica", "description": "Sugere intervenções e acompanhamento.", "category": "Intervenção"},
    {"id": "feedback", "name": "Feedback ao Aluno", "description": "Gera feedback formativo e motivador.", "category": "Aluno"},
    {"id": "skills_map", "name": "Mapa de Habilidades", "description": "Mapeia habilidades e lacunas por evidências.", "category": "Diagnóstico"},
    {"id": "diagnostic", "name": "Avaliação Diagnóstica", "description": "Cria diagnóstico inicial por nível.", "category": "Diagnóstico"},
    {"id": "worksheet", "name": "Lista de Exercícios", "description": "Gera lista com gabarito e variações.", "category": "Atividades"},
    {"id": "reading", "name": "Compreensão Leitora", "description": "Cria questões e atividades de leitura.", "category": "Língua"},
    {"id": "math_steps", "name": "Passo a Passo Matemático", "description": "Explica resolução em etapas e exemplos.", "category": "Exatas"},
    {"id": "lab", "name": "Roteiro de Experimento", "description": "Cria roteiro experimental seguro e avaliável.", "category": "Ciências"},
    {"id": "presentation", "name": "Slides da Aula", "description": "Gera estrutura de slides e tópicos por slide.", "category": "Aula"},
    {"id": "parent_msg", "name": "Mensagem aos Responsáveis", "description": "Cria comunicado claro e profissional.", "category": "Comunicação"},
]

@router.get("/agents")
def list_agents():
    return {"ok": True, "agents": AGENTS}
