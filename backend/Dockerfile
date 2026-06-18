FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# --proxy-headers + --forwarded-allow-ips: confia no X-Forwarded-For do nginx
# (único container que fala com o backend) para que rate limiting (slowapi)
# e auditoria enxerguem o IP real do cliente, não o IP interno do nginx.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--proxy-headers", "--forwarded-allow-ips=*"]
