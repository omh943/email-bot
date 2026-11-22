# Dockerfile para Email Cleaner Bot + Telegram Chatbot 24/7
FROM python:3.11-slim

# Metadata
LABEL maintainer="Email Cleaner Bot"
LABEL description="Email Cleaner Bot + Telegram Chatbot 24/7"

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario no-root
RUN useradd -m -u 1000 -s /bin/bash botuser

# Directorio de trabajo
WORKDIR /app

# Copiar requirements primero (cache de capas)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY --chown=botuser:botuser . .

# Crear directorios necesarios
RUN mkdir -p /app/data /app/logs && chown -R botuser:botuser /app

# Cambiar a usuario no-root
USER botuser

# Variables de entorno
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Healthcheck
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sqlite3; sqlite3('/app/data/telegram_bot.db', 'SELECT 1')" || exit 1

# Comando por defecto (ejecutar ambos servicios con supervisord)
CMD ["python", "-m", "src.telegram_bot"]