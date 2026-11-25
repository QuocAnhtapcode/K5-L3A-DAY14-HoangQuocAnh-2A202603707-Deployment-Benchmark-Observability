FROM python:3.11-slim AS builder

WORKDIR /app
ENV VIRTUAL_ENV=/opt/venv
COPY requirements.txt .
RUN python -m venv "$VIRTUAL_ENV" \
    && "$VIRTUAL_ENV/bin/pip" install --no-cache-dir -r requirements.txt

FROM python:3.11-slim AS runtime

WORKDIR /app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN useradd --system --user-group --no-create-home agent
COPY --from=builder /opt/venv /opt/venv
COPY app/ app/
COPY utils/ utils/

USER agent
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=2)"
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8000}\" --workers 2"]
