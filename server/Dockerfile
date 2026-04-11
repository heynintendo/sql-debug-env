FROM python:3.12-slim

WORKDIR /app

# Install server deps first so they layer-cache across code edits.
COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

# Bring in the source. models.py lives at the repo root and is imported
# from both the root package and server.* via the dual-import pattern.
COPY models.py /app/models.py
COPY server/ /app/server/
COPY openenv.yaml /app/openenv.yaml
COPY README.md /app/README.md

ENV PYTHONPATH=/app
EXPOSE 7860

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "7860"]
