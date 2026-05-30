FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir redis websockets

COPY main.py .

CMD ["python", "main.py"]
