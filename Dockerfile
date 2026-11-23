FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY src ./src
COPY config ./config

# Puerto para healthcheck (Hugging Face Spaces / Render)
ENV PORT=7860

# Variables por defecto (sobrescribirse en Secrets/Environment Variables)
ENV TELEGRAM_BOT_TOKEN=""
ENV TELEGRAM_CHAT_ID=""
ENV OPENROUTER_API_KEY=""
ENV OPENROUTER_MODEL="nvidia/nemotron-3.5-super-vl-120b-a12b"
ENV GITHUB_REPO="omh943/email-bot"
ENV GITHUB_API_TOKEN=""

CMD ["python", "-m", "src.chatbot_cloud"]
