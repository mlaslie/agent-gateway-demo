# Generic, config-driven runtime image. Configure with COMPONENT_SPEC (see main.py); Cloud Run sets PORT.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .

# Non-root user.
RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn --factory main:create_app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
