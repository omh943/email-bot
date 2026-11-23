"""
Chatbot 24/7 para Email Cleaner Bot - Despliegue en la nube (Hugging Face Spaces / Render).

- Lee data/dataset.json desde GitHub Actions (artifact o raw URL)
- Puerto HTTP dummy para mantener viva la app en plataformas gratuitas
- Telegram polling para comandos y lenguaje natural
- Integración con OpenRouter / Nemotron para respuestas inteligentes
"""
import logging
import json
import os
import re
import sys
import threading
import time
import zipfile
import io
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, Any, List, Optional

import requests

from src.digest_builder import DigestBuilder

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURACIÓN DESDE VARIABLES DE ENTORNO
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "nvidia/nemotron-3.5-super-vl-120b-a12b")
GITHUB_REPO = os.getenv("GITHUB_REPO", "omh943/email-bot")
GITHUB_API_TOKEN = os.getenv("GITHUB_API_TOKEN", "")
PORT = int(os.getenv("PORT", "7860"))
DATASET_PATH = Path(__file__).parent.parent / "data" / "dataset.json"
DATASET_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/data/dataset.json"

# ============================================================
# SERVIDOR HTTP DUMMY (para Render / Hugging Face Spaces)
# ============================================================

class _DummyHandler(BaseHTTPRequestHandler):
    """Handler mínimo para healthcheck de plataformas en la nube."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Email Cleaner Bot 24/7 - vivo")

    def log_message(self, format, *args):
        # Silenciar logs del servidor HTTP
        pass


def _start_http_dummy():
    """Inicia servidor HTTP en hilo paralelo para healthcheck."""
    try:
        server = HTTPServer(("0.0.0.0", PORT), _DummyHandler)
        logger.info(f"[HTTP] Servidor dummy en puerto {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"[HTTP] Error servidor dummy: {e}")


# ============================================================
# GESTIÓN DEL DATASET (desde GitHub)
# ============================================================

class DatasetManager:
    """Carga y gestiona el dataset de correos desde GitHub o archivo local."""

    def __init__(self):
        self.emails: List[Dict[str, Any]] = []
        self.stats: Dict[str, int] = {}
        self.generated_at: Optional[str] = None
        self._load()

    def _load(self):
        """Carga dataset desde archivo local o descarga desde GitHub."""
        # Primero intentar archivo local (desarrollo)
        if DATASET_PATH.exists():
            try:
                with open(DATASET_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._parse(data)
                logger.info(f"[Dataset] Cargado localmente: {len(self.emails)} correos")
                return
            except Exception as e:
                logger.warning(f"[Dataset] Error leyendo local: {e}")

        # Descargar desde GitHub (artifact más reciente o raw URL)
        self._download_from_github()

    def _download_from_github(self):
        """Descarga dataset.json desde GitHub (raw URL o API de artifacts)."""
        # Intento 1: raw URL directa
        try:
            resp = requests.get(DATASET_URL, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                self._parse(data)
                logger.info(f"[Dataset] Descargado desde raw URL: {len(self.emails)} correos")
                return
        except Exception as e:
            logger.warning(f"[Dataset] Error raw URL: {e}")

        # Intento 2: API de artifacts de GitHub Actions
        if GITHUB_API_TOKEN and GITHUB_REPO:
            try:
                headers = {
                    "Authorization": f"Bearer {GITHUB_API_TOKEN}",
                    "Accept": "application/vnd.github+json",
                }
                resp = requests.get(
                    f"https://api.github.com/repos/{GITHUB_REPO}/actions/artifacts",
                    headers=headers, timeout=30
                )
                resp.raise_for_status()
                artifacts = resp.json().get("artifacts", [])
                dataset_artifacts = [a for a in artifacts if a.get("name") == "dataset-json"]
                if dataset_artifacts:
                    dataset_artifacts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
                    latest = dataset_artifacts[0]
                    download_url = latest.get("archive_download_url") or \
                        f"https://api.github.com/repos/{GITHUB_REPO}/actions/artifacts/{latest['id']}/zip"
                    resp = requests.get(download_url, headers=headers, timeout=60)
                    resp.raise_for_status()
                    zip_data = zipfile.ZipFile(io.BytesIO(resp.content))
                    for name in zip_data.namelist():
                        if name.endswith("dataset.json"):
                            with zip_data.open(name) as src:
                                data = json.load(src)
                            self._parse(data)
                            logger.info(f"[Dataset] Descargado desde artifact: {len(self.emails)} correos")
                            return
            except Exception as e:
                logger.warning(f"[Dataset] Error artifact API: {e}")

        logger.warning("[Dataset] No se pudo cargar dataset, iniciando vacío")

    def _parse(self, data: Dict[str, Any]):
        """Parsea el dataset JSON."""
        self.emails = data.get("emails", [])
        self.stats = data.get("stats", {})
        self.generated_at = data.get("generated_at", "")

    def refresh(self):
        """Fuerza recarga del dataset desde GitHub."""
        old_count = len(self.emails)
        self._download_from_github()
        return len(self.emails) - old_count

    def get_emails_by_category(self, category: str, limit: int = 20) -> List[Dict]:
        return [e for e in self.emails if e.get("category") == category][:limit]

    def search_emails(self, term: str, limit: int = 15) -> List[Dict]:
        term_lower = term.lower()
        results = []
        for e in self.emails:
            haystack = f"{e.get('subject','')} {e.get('from','')} {e.get('snippet','')}".lower()
            if term_lower in haystack:
                results.append(e)
        return results[:limit]

    def get_unreplied(self, limit: int = 20) -> List[Dict]:
        """Correos sin responder (categoría other, no UAL/ESI)."""
        return [e for e in self.emails if e.get("category") == "other"][:limit]

    def get_stats_summary(self) -> str:
        """Resumen de estadísticas para mostrar al usuario."""
        if not self.stats:
            return "No hay estadísticas disponibles."
        lines = [
            f"📊 *Estadísticas del {self.generated_at[:10] if self.generated_at else 'hoy'}:",
            f"  📰 Newsletters: {self.stats.get('newsletters_labeled', 0)} etiquetadas, {self.stats.get('newsletters_archived', 0)} archivadas",
            f"  🧾 Recibos: {self.stats.get('receipts_labeled', 0)} etiquetados, {self.stats.get('receipts_archived', 0)} archivados",
            f"  🏫 UAL: {self.stats.get('ual_labeled', 0)}",
            f"  🏗️ ESI: {self.stats.get('esi_labeled', 0)}",
            f"  ⏳ Sin responder: {self.stats.get('unreplied_count', 0)}",
            f"  🛡️ Protegidos: {self.stats.get('protected_skipped', 0)}",
            f"  🗑️ Promos eliminadas: {self.stats.get('promos_deleted', 0)}",
            f"  🔄 Duplicados: {self.stats.get('duplicates_deleted', 0)}",
        ]
        return "\n".join(lines)


# ============================================================
# OPENROUTER / NEMOTRON
# ============================================================

class OpenRouterClient:
    """Cliente para OpenRouter API con modelo Nemotron."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1"

    def chat(self, messages: List[Dict[str, str]], max_tokens: int = 500) -> str:
        if not self.api_key:
            return "⚠️ OPENROUTER_API_KEY no configurada. Usando respuestas basadas en reglas."

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"[OpenRouter] Error: {e}")
            return f"⚠️ Error consultando OpenRouter: {e}"


# ============================================================
# TELEGRAM BOT
# ============================================================

class TelegramBot:
    """Bot de Telegram 24/7 con dataset de correos y OpenRouter."""

    def __init__(self):
        if not TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN no configurado")
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.dataset = DatasetManager()
        self.openrouter = OpenRouterClient(OPENROUTER_API_KEY, OPENROUTER_MODEL) if OPENROUTER_API_KEY else None
        self.last_update_id = 0
        self.running = False

    # --- Envío de mensajes ---

    def send_message(self, text: str, chat_id: Optional[str] = None,
                     parse_mode: str = "Markdown") -> bool:
        target = chat_id or self.chat_id
        if not target:
            return False
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": target,
                    "text": text[:4000],
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"[Telegram] Error enviando: {e}")
            return False

    # --- Obtener actualizaciones ---

    def get_updates(self) -> List[Dict]:
        try:
            resp = requests.get(
                f"{self.base_url}/getUpdates",
                params={
                    "offset": self.last_update_id + 1,
                    "timeout": 30,
                    "allowed_updates": ["message"],
                },
                timeout=40,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                updates = data.get("result", [])
                if updates:
                    self.last_update_id = updates[-1]["update_id"]
                return updates
        except requests.exceptions.ReadTimeout:
            pass
        except Exception as e:
            logger.error(f"[Telegram] Error getUpdates: {e}")
        return []

    # --- Procesamiento de mensajes ---

    def process_update(self, update: Dict):
        message = update.get("message")
        if not message:
            return

        chat_id = str(message.get("chat", {}).get("id"))
        text = message.get("text", "").strip()
        if not text:
            return

        # Solo responder al chat autorizado (si está configurado)
        if self.chat_id and chat_id != self.chat_id:
            self.send_message("❌ No autorizado", chat_id=chat_id)
            return

        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if command in ("/start", "/help"):
            self._cmd_help(chat_id)
        elif command == "/resumen":
            self._cmd_resumen(chat_id)
        elif command == "/estadisticas":
            self._cmd_estadisticas(chat_id)
        elif command == "/ual":
            self._cmd_ual(chat_id)
        elif command == "/esi":
            self._cmd_esi(chat_id)
        elif command == "/sin_responder":
            self._cmd_sin_responder(chat_id)
        elif command == "/buscar":
            self._cmd_buscar(args, chat_id)
        elif command == "/refresh":
            self._cmd_refresh(chat_id)
        else:
            # Lenguaje natural con OpenRouter
            self._handle_natural(text, chat_id)

    # --- Comandos ---

    def _cmd_help(self, chat_id: str):
        text = (
            "🤖 *Email Cleaner Bot 24/7*\n\n"
            "Comandos disponibles:\n"
            "• `/resumen` - Resumen del último análisis\n"
            "• `/estadisticas` - Stats detalladas de limpieza\n"
            "• `/ual` - Correos de la UAL\n"
            "• `/esi` - Correos de ESI (Ingeniería)\n"
            "• `/sin_responder` - Correos sin responder (>3 días)\n"
            "• `/buscar <término>` - Buscar en correos\n"
            "• `/refresh` - Recargar dataset desde GitHub\n\n"
            "También puedes escribir en lenguaje natural:\n"
            "• \"¿Qué correos de la UAL tengo?\"\n"
            "• \"Buscar factura Amazon\"\n"
            "• \"Cuántos sin responder hay\""
        )
        self.send_message(text, chat_id=chat_id)

    def _cmd_resumen(self, chat_id: str):
        text = self.dataset.get_stats_summary()
        text += f"\n\n📧 Total correos en dataset: {len(self.dataset.emails)}"
        self.send_message(text, chat_id=chat_id)

    def _cmd_estadisticas(self, chat_id: str):
        self._cmd_resumen(chat_id)

    def _cmd_ual(self, chat_id: str):
        emails = self.dataset.get_emails_by_category("ual", limit=20)
        if not emails:
            self.send_message("📭 No hay correos UAL en el dataset.", chat_id=chat_id)
            return
        text = f"🏫 *Correos UAL ({len(emails)}):*\n\n"
        for i, e in enumerate(emails, 1):
            subj = e.get("subject", "(sin asunto)")[:50]
            frm = e.get("from", "(desconocido)")[:35]
            text += f"{i}. *{subj}*\n   _{frm}_\n   {e.get('gmail_link', '')}\n\n"
        self.send_message(text[:4000], chat_id=chat_id)

    def _cmd_esi(self, chat_id: str):
        emails = self.dataset.get_emails_by_category("esi", limit=20)
        if not emails:
            self.send_message("📭 No hay correos ESI en el dataset.", chat_id=chat_id)
            return
        text = f"🏗️ *Correos ESI ({len(emails)}):*\n\n"
        for i, e in enumerate(emails, 1):
            subj = e.get("subject", "(sin asunto)")[:50]
            frm = e.get("from", "(desconocido)")[:35]
            text += f"{i}. *{subj}*\n   _{frm}_\n   {e.get('gmail_link', '')}\n\n"
        self.send_message(text[:4000], chat_id=chat_id)

    def _cmd_sin_responder(self, chat_id: str):
        emails = self.dataset.get_unreplied(limit=20)
        count = self.dataset.stats.get("unreplied_count", 0)
        text = f"⏳ *Sin responder (>3 días): {count}*\n\n"
        if emails:
            text += "*Ejemplos recientes:*\n\n"
            for i, e in enumerate(emails, 1):
                subj = e.get("subject", "(sin asunto)")[:45]
                frm = e.get("from", "(desconocido)")[:30]
                text += f"{i}. *{subj}*\n   _{frm}_\n   {e.get('gmail_link', '')}\n\n"
        self.send_message(text[:4000], chat_id=chat_id)

    def _cmd_buscar(self, args: str, chat_id: str):
        if not args:
            self.send_message("🔍 Uso: `/buscar <término>`\nEj: `/buscar factura Amazon`", chat_id=chat_id)
            return
        results = self.dataset.search_emails(args, limit=15)
        if not results:
            self.send_message(f"🔍 Sin resultados para: *{args}*", chat_id=chat_id)
            return
        text = f"🔍 *Resultados: '{args}' ({len(results)}):*\n\n"
        for i, e in enumerate(results, 1):
            subj = e.get("subject", "(sin asunto)")[:50]
            frm = e.get("from", "(desconocido)")[:35]
            cat = e.get("category", "?")
            text += f"{i}. [{cat}] *{subj}*\n   _{frm}_\n   {e.get('gmail_link', '')}\n\n"
        self.send_message(text[:4000], chat_id=chat_id)

    def _cmd_refresh(self, chat_id: str):
        diff = self.dataset.refresh()
        self.send_message(
            f"🔄 Dataset refrescado: {len(self.dataset.emails)} correos totales (cambio: {diff:+d})",
            chat_id=chat_id
        )

    # --- Lenguaje natural con OpenRouter ---

    def _handle_natural(self, text: str, chat_id: str):
        """Procesa lenguaje natural usando OpenRouter o reglas de respaldo."""
        # Comandos rápidos basados en reglas (sin OpenRouter)
        text_lower = text.lower()
        if any(k in text_lower for k in ["ual", "universidad", "almería"]):
            self._cmd_ual(chat_id)
            return
        if any(k in text_lower for k in ["esi", "ingenier", "escuela"]):
            self._cmd_esi(chat_id)
            return
        if any(k in text_lower for k in ["sin responder", "no respond", "pendiente"]):
            self._cmd_sin_responder(chat_id)
            return
        if any(k in text_lower for k in ["estadística", "stats", "números", "resumen"]):
            self._cmd_resumen(chat_id)
            return
        if any(k in text_lower for k in ["buscar", "encuentra", "busca"]):
            term = text_lower
            for kw in ["buscar", "encuentra", "busca"]:
                term = term.replace(kw, " ")
            term = term.strip()
            if term:
                self._cmd_buscar(term, chat_id)
            else:
                self.send_message("🔍 ¿Qué quieres buscar? Ej: `/buscar factura`", chat_id=chat_id)
            return

        # Usar OpenRouter para respuestas inteligentes
        if self.openrouter:
            context = self._build_context_for_ai(text)
            system_prompt = (
                "Eres un asistente que ayuda a gestionar correos electrónicos. "
                "Tienes acceso a un dataset de correos clasificados. Responde en español de forma clara y concisa. "
                "Si el usuario pregunta sobre correos específicos, usa la información del contexto proporcionada. "
                "No inventes datos que no estén en el contexto."
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Contexto de correos:\n{context}\n\nPregunta del usuario: {text}"},
            ]
            respuesta = self.openrouter.chat(messages)
            self.send_message(respuesta, chat_id=chat_id)
        else:
            self.send_message(
                "🤔 No te entiendo completamente. Usa /help para ver comandos disponibles.\n\n"
                "Configura OPENROUTER_API_KEY para habilitar lenguaje natural avanzado.",
                chat_id=chat_id
            )

    def _build_context_for_ai(self, user_text: str) -> str:
        """Construye un resumen del dataset relevante para la pregunta del usuario."""
        # Extraer palabras clave de la pregunta para filtrar correos relevantes
        words = set(re.findall(r'\w+', user_text.lower()))
        relevant = []
        for e in self.dataset.emails:
            haystack = f"{e.get('subject','')} {e.get('from','')} {e.get('snippet','')}".lower()
            if any(w in haystack for w in words if len(w) > 3):
                relevant.append(e)
        if not relevant:
            relevant = self.dataset.emails[:5]
        lines = []
        for e in relevant[:10]:
            lines.append(
                f"- [{e.get('category','?')}] {e.get('subject','(sin asunto)')} "
                f"de {e.get('from','(desconocido)')} ({e.get('date','')})"
            )
        return "\n".join(lines)

    # --- Loop principal ---

    def run_polling(self):
        self.running = True
        logger.info("[BOT] Telegram ChatBot 24/7 iniciado (polling)")
        self.send_message(
            "🤖 *Email Cleaner Bot 24/7 ACTIVO*\n\n"
            "✅ Dataset de correos desde GitHub\n"
            "✅ Comandos + Lenguaje natural\n"
            "✅ OpenRouter / Nemotron integrado\n\n"
            "Usa /help para comenzar.",
        )
        while self.running:
            try:
                updates = self.get_updates()
                for update in updates:
                    if not self.running:
                        break
                    self.process_update(update)
            except Exception as e:
                logger.error(f"[BOT] Error en loop: {e}")
                time.sleep(5)

    def stop(self):
        self.running = False
        logger.info("[BOT] Detenido")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Iniciar servidor HTTP dummy para healthcheck
    http_thread = threading.Thread(target=_start_http_dummy, daemon=True)
    http_thread.start()

    # Iniciar bot de Telegram
    try:
        bot = TelegramBot()
        bot.run_polling()
    except KeyboardInterrupt:
        logger.info("Interrumpido por usuario")
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
