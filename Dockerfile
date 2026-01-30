FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /code

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# requirements precisa estar em api/requirements.txt
COPY api/requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir -r /code/requirements.txt

# copia o código da api (e ignora o resto via .dockerignore)
COPY api /code/api

EXPOSE 7860

# ✅ seu app está em ./api/app/main.py
CMD ["uvicorn", "api.app.main:app", "--host", "0.0.0.0", "--port", "7860"]
