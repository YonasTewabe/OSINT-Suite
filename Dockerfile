FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY scanner_core.py .
COPY telegram_bot.py .
COPY templates/ templates/

EXPOSE 5000

CMD ["python", "app.py"]
