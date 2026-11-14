FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/futuranext
COPY backend/ ./
RUN python -m pip install --no-cache-dir . \
    && groupadd --system futuranext \
    && useradd --system --gid futuranext --no-create-home futuranext

USER futuranext
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
