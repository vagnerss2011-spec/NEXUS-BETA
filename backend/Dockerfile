FROM python:3.12-slim
WORKDIR /app
# tzdata é necessário pra a env var TZ resolver fusos como America/Sao_Paulo
# (caso contrário o Python cai pra UTC silenciosamente).
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
