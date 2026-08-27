FROM python:3.12-slim
# Sem .pyc: o /app e bind-mount em dev e read-only-ish para o usuario nao-root,
# entao evitar __pycache__ tira um ruido de permissao.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# O .dockerignore ao lado deste arquivo e o que impede o certificado ICP-Brasil
# (certs/*.pfx) e os tokens da Ailos (scripts/*.json) de entrarem na imagem.
# O .gitignore NAO tem efeito sobre o Docker.
COPY . .
# Nao roda como root: o .pfx e montado em /certs:ro e um escape do processo
# nao deve dar leitura direta da chave privada da empresa.
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser
# O IP real do cliente vem de app/core/client_ip.py (ultima entrada do
# X-Forwarded-For), nao do --proxy-headers da uvicorn. Ver nota no
# docker-compose.prod.yml.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
