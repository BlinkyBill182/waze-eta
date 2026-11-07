# Build context: the waze-eta folder
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY waze_eta/ waze_eta/
ENV PYTHONUNBUFFERED=1 PORT=8080
RUN useradd --uid 1000 --no-create-home eta
USER eta
# Traefik routes only to a healthy container, so the first check runs within seconds of a deploy.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --start-interval=2s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["python", "-m", "waze_eta"]
