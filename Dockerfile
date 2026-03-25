FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as non-root user — reduces blast radius if the container is ever compromised
RUN adduser --disabled-password --gecos "" appuser
COPY . .
RUN chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

EXPOSE 5000

CMD ["gunicorn", "-c", "gunicorn.conf.py", "main:app"]
