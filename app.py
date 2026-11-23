"""
Punto de entrada para Hugging Face Spaces / Render.
Ejecuta el chatbot 24/7 de Email Cleaner Bot.
"""
import sys
from pathlib import Path

# Añadir directorio raíz al path
sys.path.insert(0, str(Path(__file__).parent))

from src.chatbot_cloud import main

if __name__ == "__main__":
    main()
