# syntax=docker/dockerfile:1.7
# db-bootstrap (OTA-AZ-001 Section 8): build from the repo root:
#   docker build -f infra/db-bootstrap/Dockerfile -t db-bootstrap .
FROM python:3.13-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY infra/db-bootstrap/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app infra/db-bootstrap/bootstrap.py ./
ENV PYTHONUNBUFFERED=1
USER app
CMD ["python", "bootstrap.py"]
