FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Koyeb inyecta $PORT; el server de salud lo escucha (default 8000).
EXPOSE 8000
CMD ["python", "app.py"]
