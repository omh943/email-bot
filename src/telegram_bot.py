"""
Telegram Chatbot 24/7 para Email Cleaner Bot.
- Persistencia en SQLite (memoria conversacional)
- Polling robusto con reintentos
- Comandos interactivos + lenguaje natural
- Deployable como systemd service / Docker
"""
import logging
import json
import re
import sqlite3
import zipfile
import io
from urllib.parse import quote
import requests
import threading
import time
import os
import signal
import sys
import subprocess
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from src.digest_builder import DigestBuilder

from config.settings import (
    TELEGRAM_WEBHOOK, EMAIL_USER, DATA_DIR, LOGS_DIR
)

logger = logging.getLogger(__name__)


@dataclass
class BotConfig:
    """Configuración del bot"""
    token: str
    chat_id: str
    admin_chat_id: str = ""
    db_path: str = ""
    poll_interval: int = 30
    max_retries: int = 5


class DatabaseManager:
    """Gestor de base de datos SQLite para persistencia"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    @contextmanager
    def get_conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        with self.get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    user_message TEXT NOT NULL,
                    bot_response TEXT NOT NULL,
                    command TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_conversations_chat_time 
                ON conversations(chat_id, timestamp);
                
                CREATE TABLE IF NOT EXISTS user_context (
                    chat_id TEXT PRIMARY KEY,
                    context_json TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE TABLE IF NOT EXISTS email_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email_id TEXT UNIQUE NOT NULL,
                    thread_id TEXT,
                    message_id TEXT,
                    subject TEXT,
                    from_addr TEXT,
                    to_addr TEXT,
                    date TEXT,
                    body TEXT,
                    labels TEXT,
                    category TEXT,
                    actions TEXT,
                    fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                
                CREATE INDEX IF NOT EXISTS idx_email_cache_category 
                ON email_cache(category);
                
                CREATE INDEX IF NOT EXISTS idx_email_cache_date 
                ON email_cache(date);
                
                CREATE TABLE IF NOT EXISTS bot_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stat_date DATE UNIQUE NOT NULL,
                    newsletters_labeled INTEGER DEFAULT 0,
                    newsletters_archived INTEGER DEFAULT 0,
                    receipts_labeled INTEGER DEFAULT 0,
                    receipts_archived INTEGER DEFAULT 0,
                    ual_labeled INTEGER DEFAULT 0,
                    esi_labeled INTEGER DEFAULT 0,
                    protected_skipped INTEGER DEFAULT 0,
                    promos_deleted INTEGER DEFAULT 0,
                    duplicates_deleted INTEGER DEFAULT 0,
                    unreplied_count INTEGER DEFAULT 0,
                    generated_at DATETIME
                );
                
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_name TEXT UNIQUE NOT NULL,
                    last_run DATETIME,
                    next_run DATETIME,
                    status TEXT DEFAULT 'pending'
                );
            """)

            # Migración: añadir message_id a email_cache si la BD es antigua.
            try:
                conn.execute("ALTER TABLE email_cache ADD COLUMN message_id TEXT")
            except sqlite3.OperationalError:
                pass  # La columna ya existe
    
    def save_conversation(self, chat_id: str, user_msg: str, bot_resp: str, command: str = None):
        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO conversations (chat_id, user_message, bot_response, command) VALUES (?, ?, ?, ?)",
                (chat_id, user_msg, bot_resp, command)
            )
    
    def get_history(self, chat_id: str, limit: int = 20) -> List[Dict]:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT user_message, bot_response, command, timestamp FROM conversations "
                "WHERE chat_id = ? ORDER BY timestamp DESC LIMIT ?",
                (chat_id, limit)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def save_context(self, chat_id: str, context: Dict):
        with self.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_context (chat_id, context_json, updated_at) VALUES (?, ?, ?)",
                (chat_id, json.dumps(context), datetime.now().isoformat())
            )
    
    def get_context(self, chat_id: str) -> Dict:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT context_json FROM user_context WHERE chat_id = ?",
                (chat_id,)
            )
            row = cursor.fetchone()
            return json.loads(row[0]) if row else {}
    
    def cache_emails(self, emails: List[Dict]):
        with self.get_conn() as conn:
            for e in emails:
                conn.execute("""
                    INSERT OR REPLACE INTO email_cache 
                    (email_id, thread_id, message_id, subject, from_addr, to_addr, date, body, labels, category, actions)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    e.get("id"), e.get("thread_id"), DigestBuilder._message_id(e),
                    e.get("subject"),
                    e.get("from"), e.get("to"), e.get("date"),
                    e.get("body", "")[:5000],  # Limit body size
                    json.dumps(e.get("labels", [])),
                    e.get("category", ""),
                    json.dumps(e.get("actions", []))
                ))
    
    def get_emails_by_category(self, category: str, limit: int = 50) -> List[Dict]:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM email_cache WHERE category = ? ORDER BY date DESC LIMIT ?",
                (category, limit)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def search_emails(self, term: str, limit: int = 20) -> List[Dict]:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM email_cache WHERE subject LIKE ? OR from_addr LIKE ? OR body LIKE ? ORDER BY date DESC LIMIT ?",
                (f"%{term}%", f"%{term}%", f"%{term}%", limit)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_email_by_id(self, email_id: str) -> Optional[Dict]:
        """Recupera un correo completo de la caché por su ID"""
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM email_cache WHERE email_id = ?", (email_id,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def save_daily_stats(self, stats: Dict):
        with self.get_conn() as conn:
            date_str = datetime.now().date().isoformat()
            conn.execute("""
                INSERT OR REPLACE INTO bot_stats 
                (stat_date, newsletters_labeled, newsletters_archived, receipts_labeled,
                 receipts_archived, ual_labeled, esi_labeled,
                 protected_skipped, promos_deleted, duplicates_deleted, unreplied_count, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                date_str,
                stats.get("newsletters_labeled", 0),
                stats.get("newsletters_archived", 0),
                stats.get("receipts_labeled", 0),
                stats.get("receipts_archived", 0),
                stats.get("ual_labeled", 0),
                stats.get("esi_labeled", 0),
                stats.get("protected_skipped", 0),
                stats.get("promos_deleted", 0),
                stats.get("duplicates_deleted", 0),
                stats.get("unreplied_count", 0),
                stats.get("generated_at", datetime.now().isoformat())
            ))
    
    def get_latest_stats(self) -> Optional[Dict]:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM bot_stats ORDER BY stat_date DESC LIMIT 1"
            )
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_stats_history(self, days: int = 30) -> List[Dict]:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM bot_stats WHERE stat_date >= date('now', ?) ORDER BY stat_date DESC",
                (f"-{days} days",)
            )
            return [dict(row) for row in cursor.fetchall()]


class TelegramChatBot:
    """Bot de Telegram 24/7 con memoria persistente"""
    
    def __init__(self, config: BotConfig):
        self.config = config
        self.db = DatabaseManager(config.db_path)
        self.base_url = f"https://api.telegram.org/bot{config.token}"
        self.running = False
        self.last_update_id = 0
        self._setup_signal_handlers()
        
        # Comandos disponibles
        self.commands = {
            "/start": self.cmd_start,
            "/help": self.cmd_help,
            "/resumen": self.cmd_resumen,
            "/estadisticas": self.cmd_estadisticas,
            "/sin_responder": self.cmd_sin_responder,
            "/pendientes": self.cmd_pendientes,
            "/ual": self.cmd_ual,
            "/esi": self.cmd_esi,
            "/buscar": self.cmd_buscar,
            "/ultimos": self.cmd_ultimos,
            "/promos": self.cmd_promos,
            "/protegidos": self.cmd_protegidos,
            "/historial": self.cmd_historial,
            "/contexto": self.cmd_contexto,
            "/stats_semana": self.cmd_stats_semana,
            "/limpiar": self.cmd_limpiar,
        }
        
        # Teclado principal
        self.main_keyboard = {
            "keyboard": [
                [{"text": "📊 Resumen"}, {"text": "📈 Estadísticas"}],
                [{"text": "🏫 UAL"}, {"text": "🏗️ ESI"}],
                [{"text": "⏳ Sin responder"}, {"text": "🔍 Buscar"}],
                [{"text": "🕐 Últimos"}, {"text": "🗑️ Promos"}],
                [{"text": "🛡️ Protegidos"}, {"text": "📜 Historial"}],
                [{"text": "📊 Semana"}, {"text": "🧹 Limpiar"}]
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False
        }
    
    def _setup_signal_handlers(self):
        """Manejo graceful de señales"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        logger.info(f"Señal {signum} recibida, deteniendo bot...")
        self.stop()
        sys.exit(0)
    
    def send_message(self, text: str, parse_mode: str = "Markdown", 
                     reply_markup: Dict = None, disable_preview: bool = True,
                     chat_id: str = None) -> bool:
        """Envía mensaje con reintentos"""
        target_chat_id = chat_id or self.config.chat_id
        for attempt in range(self.config.max_retries):
            try:
                payload = {
                    "chat_id": target_chat_id,
                    "text": text[:4096],  # Límite Telegram
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": disable_preview
                }
                if reply_markup:
                    payload["reply_markup"] = json.dumps(reply_markup)
                
                resp = requests.post(
                    f"{self.base_url}/sendMessage", 
                    json=payload, 
                    timeout=15
                )
                resp.raise_for_status()
                return True
            except Exception as e:
                logger.warning(f"Intento {attempt+1}/{self.config.max_retries} falló: {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(2 ** attempt)  # Backoff exponencial
        logger.error(f"Fallo envío tras {self.config.max_retries} intentos")
        return False
    
    def send_typing(self):
        """Muestra indicador 'escribiendo...'"""
        try:
            requests.post(f"{self.base_url}/sendChatAction", 
                         json={"chat_id": self.config.chat_id, "action": "typing"}, timeout=5)
        except Exception:
            pass
    
    def get_updates(self) -> List[Dict]:
        """Obtiene actualizaciones con long polling"""
        try:
            params = {
                "offset": self.last_update_id + 1,
                "timeout": self.config.poll_interval,
                "allowed_updates": ["message", "edited_message", "callback_query"]
            }
            resp = requests.get(f"{self.base_url}/getUpdates", params=params, timeout=self.config.poll_interval + 10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                updates = data.get("result", [])
                if updates:
                    self.last_update_id = updates[-1]["update_id"]
                return updates
        except requests.exceptions.ReadTimeout:
            pass  # Normal en long polling
        except Exception as e:
            logger.error(f"Error getUpdates: {e}")
        return []
    
    def process_update(self, update: Dict):
        """Procesa una actualización"""
        message = update.get("message") or update.get("edited_message")
        if not message:
            # Callback queries para botones inline
            if "callback_query" in update:
                self._process_callback(update["callback_query"])
            return
        
        chat_id = str(message.get("chat", {}).get("id"))
        if chat_id != self.config.chat_id and chat_id != self.config.admin_chat_id:
            logger.warning(f"Chat no autorizado: {chat_id}")
            self.send_message("❌ No autorizado", chat_id=chat_id)
            return
        
        text = message.get("text", "").strip()
        if not text:
            return
        
        # Guardar contexto
        context = self.db.get_context(chat_id)
        context["last_message"] = text
        context["last_time"] = datetime.now().isoformat()
        self.db.save_context(chat_id, context)
        
        # Procesar comando o texto libre
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        # Mapear botones de teclado a comandos
        button_map = {
            "📊 Resumen": "/resumen",
            "📈 Estadísticas": "/estadisticas",
            "🏫 UAL": "/ual",
            "🏗️ ESI": "/esi",
            "⏳ Sin responder": "/sin_responder",
            "🔍 Buscar": "/buscar",
            "🕐 Últimos": "/ultimos",
            "🗑️ Promos": "/promos",
            "🛡️ Protegidos": "/protegidos",
            "📜 Historial": "/historial",
            "📊 Semana": "/stats_semana",
            "🧹 Limpiar": "/limpiar",
        }
        
        if text in button_map:
            command = button_map[text]
            args = ""
        
        if command in self.commands:
            self.send_typing()
            try:
                response = self.commands[command](args, chat_id)
                if response:
                    self.db.save_conversation(chat_id, text, response, command)
            except Exception as e:
                logger.error(f"Error en comando {command}: {e}")
                err_msg = f"❌ Error: {e}"
                self.send_message(err_msg, chat_id=chat_id)
                self.db.save_conversation(chat_id, text, err_msg, command)
        else:
            # Lenguaje natural
            self.send_typing()
            response = self.handle_natural_language(text, chat_id)
            if response:
                self.db.save_conversation(chat_id, text, response, "natural")
    
    def _process_callback(self, callback: Dict):
        """Procesa callback queries de botones inline"""
        data = callback.get("data", "")
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id"))
        
        if data.startswith("open_email:"):
            email_id = data.split(":")[1]
            self._open_email(email_id, chat_id)
        elif data.startswith("category:"):
            cat = data.split(":")[1]
            self._show_category(cat, chat_id)
    
    def _open_email(self, email_id: str, chat_id: str):
        """Abre email en Gmail usando el Message-ID real."""
        email = self.db.get_email_by_id(email_id)
        if not email:
            self.send_message("📭 No se encontró el correo.", chat_id=chat_id)
            return

        # Usar DigestBuilder._gmail_link para construir el enlace de forma consistente
        url = DigestBuilder._gmail_link(email)

        self.send_message(
            f"📧 [Abrir en Gmail]({url})",
            parse_mode="Markdown",
            chat_id=chat_id
        )

    def _gmail_inline_keyboard(self, emails: List[Dict]) -> Optional[Dict]:
        """Crea teclado inline con botones 'Abrir en Gmail' para cada correo."""
        if not emails:
            return None
        rows = []
        for e in emails[:10]:  # Máximo 10 botones por mensaje
            email_id = e.get("email_id") or e.get("id")
            if not email_id:
                continue
            rows.append([{
                "text": f"📧 Abrir: {e.get('subject', '(sin asunto)')[:25]}",
                "url": DigestBuilder._gmail_link(e)
            }])
        return {"inline_keyboard": rows} if rows else None

    def _truncate_message(self, text: str, max_len: int = 3800) -> str:
        """Trunca un mensaje para no exceder el límite de Telegram (4096 chars)."""
        if len(text) <= max_len:
            return text
        return text[:max_len] + "\n\n... (mensaje truncado)"

    def _show_category(self, category: str, chat_id: str):
        """Muestra emails de una categoría con botones para abrir en Gmail"""
        emails = self.db.get_emails_by_category(category, limit=20)
        if not emails:
            self.send_message(f"📭 No hay correos en {category}", chat_id=chat_id)
            return
        
        text = f"📂 *{category.upper()}* ({len(emails)}):\n\n"
        for i, e in enumerate(emails, 1):
            subj = e.get("subject", "(sin asunto)")[:50]
            from_addr = e.get("from_addr", "(desconocido)")[:35]
            text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        
        text = self._truncate_message(text)
        reply_markup = self._gmail_inline_keyboard(emails[:10])
        self.send_message(text, chat_id=chat_id, reply_markup=reply_markup)
    
    # ===== COMANDOS =====
    
    def cmd_start(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        welcome = (
            "🤖 *Email Cleaner Bot - Asistente 24/7*\n\n"
            "¡Hola! Soy tu asistente inteligente de correo.\n"
            "Tengo memoria persistente y estoy siempre activo.\n\n"
            "👇 *Elige una opción:*"
        )
        self.send_message(welcome, reply_markup=self.main_keyboard, chat_id=chat_id)
        return "Menú principal mostrado"
    
    def cmd_help(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        help_text = (
            "📋 *Comandos disponibles:*\n\n"
            "📊 **General:**\n"
            "• `/resumen` - Resumen diario\n"
            "• `/estadisticas` - Stats detalladas\n"
            "• `/ultimos` - Última ejecución\n"
            "• `/stats_semana` - Últimos 7 días\n"
            "• `/limpiar` - Forzar limpieza de correo ahora\n\n"
            "🏫 **UAL / ESI:**\n"
            "• `/ual` - Correos UAL General\n"
            "• `/esi` - Correos ESI (Ingeniería)\n\n"
            "⏳ **Acciones:**\n"
            "• `/sin_responder` - >3 días sin respuesta\n\n"
            "🔍 **Búsqueda:**\n"
            "• `/buscar <término>` - Buscar\n"
            "• `/promos` - Promocionales eliminados\n"
            "• `/protegidos` - Correos protegidos\n\n"
            "💾 **Memoria:**\n"
            "• `/historial` - Últimas conversaciones\n"
            "• `/contexto` - Contexto actual\n\n"
            "💬 **Lenguaje natural:**\n"
            "\"Muéstrame UAL\", \"Qué sin responder\",\n"
            "\"Buscar factura Amazon\", \"Stats semana\""
        )
        self.send_message(help_text, chat_id=chat_id)
        return "Ayuda mostrada"
    
    def cmd_resumen(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        stats = self.db.get_latest_stats()
        if not stats:
            return self.send_message("📭 No hay datos de ejecución reciente.", chat_id=chat_id)
        
        text = self._format_stats(stats, "📧 *Resumen Diario*")
        self.send_message(text, chat_id=chat_id)
        return "Resumen enviado"
    
    def cmd_estadisticas(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        stats = self.db.get_latest_stats()
        if not stats:
            return self.send_message("📭 No hay datos.", chat_id=chat_id)
        
        text = self._format_stats(stats, "📊 *Estadísticas Detalladas*")
        self.send_message(text, chat_id=chat_id)
        return "Estadísticas enviadas"
    
    def cmd_ual(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        emails = self.db.get_emails_by_category("ual", limit=20)
        if not emails:
            return self.send_message("📭 No hay correos UAL recientes.", chat_id=chat_id)
        
        text = f"🏫 *Correos UAL ({len(emails)}):*\n\n"
        for i, e in enumerate(emails, 1):
            subj = e.get("subject", "(sin asunto)")[:55]
            from_addr = e.get("from_addr", "(desconocido)")[:40]
            text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        
        self.send_message(text, chat_id=chat_id)
        return "UAL enviado"
    
    def cmd_esi(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        emails = self.db.get_emails_by_category("esi", limit=20)
        if not emails:
            return self.send_message("📭 No hay correos ESI recientes.", chat_id=chat_id)
        
        text = f"🏗️ *Correos ESI ({len(emails)}):*\n\n"
        for i, e in enumerate(emails, 1):
            subj = e.get("subject", "(sin asunto)")[:55]
            from_addr = e.get("from_addr", "(desconocido)")[:40]
            text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        
        self.send_message(text, chat_id=chat_id)
        return "ESI enviado"
    
    def cmd_sin_responder(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        stats = self.db.get_latest_stats()
        if not stats:
            return self.send_message("📭 No hay datos.", chat_id=chat_id)
        
        count = stats.get("unreplied_count", 0)
        text = f"⏳ *Sin responder (>3 días): {count}*\n\n"
        
        # Buscar correos reales no UAL/ESI de la caché
        emails = self.db.get_emails_by_category("other", limit=10)
        if emails:
            text += "*Ejemplos recientes:*\n\n"
            for i, e in enumerate(emails, 1):
                subj = e.get("subject", "(sin asunto)")[:45]
                from_addr = e.get("from_addr", "(desconocido)")[:30]
                text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        else:
            text += "_Usa /buscar para encontrar específicos_\n"
        
        text = self._truncate_message(text)
        reply_markup = self._gmail_inline_keyboard(emails[:10]) if emails else None
        self.send_message(text, chat_id=chat_id, reply_markup=reply_markup)
        return "Sin responder enviado"
    
    def cmd_pendientes(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        stats = self.db.get_latest_stats()
        if not stats:
            return self.send_message("📭 No hay datos.", chat_id=chat_id)
        
        count = stats.get("unreplied_count", 0)
        text = (
            f"⏳ *Correos sin contestar*\n\n"
            f"• Total sin responder (>3 días): *{count}*\n"
            f"• Usa `/buscar` para encontrar específicos\n"
            f"• ¡El bot responde 24/7!"
        )
        self.send_message(text, chat_id=chat_id)
        return "Pendientes enviado"
    
    def cmd_buscar(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        if not args:
            return self.send_message("🔍 Uso: `/buscar <término>`\nEj: `/buscar factura Amazon`", chat_id=chat_id)
        
        emails = self.db.search_emails(args.lower(), limit=15)
        if not emails:
            return self.send_message(f"🔍 Sin resultados para: *{args}*", chat_id=chat_id)
        
        text = f"🔍 *Resultados: '{args}' ({len(emails)}):*\n\n"
        for i, e in enumerate(emails, 1):
            subj = e.get("subject", "(sin asunto)")[:55]
            from_addr = e.get("from_addr", "(?)")[:35]
            cat = e.get("category", "?")
            text += f"{i}. [{cat}] *{subj}*\n   _{from_addr}_\n\n"
        
        text = self._truncate_message(text)
        reply_markup = self._gmail_inline_keyboard(emails[:10])
        self.send_message(text, chat_id=chat_id, reply_markup=reply_markup)
        return f"Búsqueda: {args}"
    
    def cmd_ultimos(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        stats = self.db.get_latest_stats()
        if not stats:
            return self.send_message("📭 No hay datos.", chat_id=chat_id)
        
        text = "🕐 *Última ejecución:* "
        if stats.get("generated_at"):
            try:
                dt = datetime.fromisoformat(stats["generated_at"])
                text += dt.strftime("%d/%m/%Y %H:%M")
            except:
                text += stats["generated_at"]
        text += "\n\n" + self._format_stats(stats, "")
        self.send_message(text, chat_id=chat_id)
        return "Última ejecución enviada"
    
    def cmd_promos(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        stats = self.db.get_latest_stats()
        if not stats:
            return self.send_message("📭 No hay datos.", chat_id=chat_id)
        
        deleted = stats.get("promos_deleted", 0)
        dup = stats.get("duplicates_deleted", 0)
        
        text = (
            f"🗑️ *Limpieza promocionales:*\n\n"
            f"• Promos organizaciones: *{deleted}*\n"
            f"• Duplicados: *{dup}*\n"
            f"• **Total: {deleted + dup}**\n\n"
            f"✅ Solo orgs (noreply@, marketing@, promo@...)\n"
            f"🛡️ NUNCA: @gmail.com, @outlook.com, @ual.es, @inlumine.ual.es"
        )
        self.send_message(text, chat_id=chat_id)
        return "Promos enviado"
    
    def cmd_protegidos(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        stats = self.db.get_latest_stats()
        if not stats:
            return self.send_message("📭 No hay datos.", chat_id=chat_id)
        
        protected = stats.get("protected_skipped", 0)
        text = (
            f"🛡️ *Protegidos (no tocados): {protected}*\n\n"
            f"• 🏦 Bancos, facturas, pagos\n"
            f"• 🏛️ Hacienda, SS, BOE\n"
            f"• 🏫 UAL / ESI (@esi@ual.es, @ual.es, @inlumine.ual.es)\n"
            f"• 🏥 Médicos, seguros, legal\n"
            f"• 👤 Personales (@gmail.com, @outlook.com...)\n\n"
            f"✅ NUNCA se archivan ni eliminan."
        )
        self.send_message(text, chat_id=chat_id)
        return "Protegidos enviado"
    
    def cmd_historial(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        history = self.db.get_history(chat_id, limit=10)
        if not history:
            return self.send_message("📭 Sin historial.", chat_id=chat_id)
        
        text = "📜 *Últimas conversaciones:*\n\n"
        for h in reversed(history):
            cmd = f" [{h['command']}]" if h['command'] else ""
            text += f"• *Tú:* {h['user_message'][:50]}\n"
            text += f"  *Bot:* {h['bot_response'][:50]}{cmd}\n"
            text += f"  _{h['timestamp']}_\n\n"
        
        self.send_message(text, chat_id=chat_id)
        return "Historial enviado"
    
    def cmd_contexto(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        context = self.db.get_context(chat_id)
        if not context:
            return self.send_message("📭 Sin contexto guardado.", chat_id=chat_id)
        
        text = "🧠 *Contexto actual:*\n\n"
        for k, v in context.items():
            text += f"• {k}: {v}\n"
        self.send_message(text, chat_id=chat_id)
        return "Contexto enviado"
    
    def cmd_stats_semana(self, args: str, chat_id: str = None) -> str:
        chat_id = chat_id or self.config.chat_id
        history = self.db.get_stats_history(7)
        if not history:
            return self.send_message("📭 Sin datos de la semana.", chat_id=chat_id)
        
        text = "📊 *Últimos 7 días:*\n\n"
        for day in history:
            date = day.get("stat_date", "?")
            text += (
                f"📅 {date}: "
                f"📰{day.get('newsletters_labeled',0)} "
                f"🧾{day.get('receipts_labeled',0)} "
                f"🏫{day.get('ual_labeled',0)} "
                f"🏗️{day.get('esi_labeled',0)} "
                f"🗑️{day.get('promos_deleted',0)} "
                f"⏳{day.get('unreplied_count',0)}\n"
            )
        self.send_message(text, chat_id=chat_id)
        return "Stats semana enviadas"
    
    def cmd_limpiar(self, args: str, chat_id: str = None) -> str:
        """Fuerza la ejecución del bot de limpieza de correo bajo demanda."""
        chat_id = chat_id or self.config.chat_id
        self.send_message("🧹 Ejecutando limpieza de correo... esto puede tardar un par de minutos.", chat_id=chat_id)
        
        try:
            result = subprocess.run(
                [sys.executable, "-m", "src.main"],
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutos máximo
                cwd=str(Path(__file__).parent.parent)
            )
            
            if result.returncode == 0:
                # Extraer líneas clave del log
                output = result.stdout
                lines = output.split("\n")
                key_lines = [l for l in lines if any(k in l for k in [
                    "Correos encontrados", "UAL/ESI de los últimos", "Enviando resumen",
                    "finalizado correctamente", "No hay correos"
                ])]
                summary = "\n".join(key_lines[:6]) if key_lines else "Limpieza completada sin salida destacada."
                self.send_message(f"✅ *Limpieza completada*\n\n{summary}", chat_id=chat_id)
            else:
                error_tail = result.stderr[-500:] if result.stderr else "Sin detalles de error"
                self.send_message(f"❌ *Limpieza falló*\n\n`{error_tail}`", chat_id=chat_id)
                
        except subprocess.TimeoutExpired:
            self.send_message("⏱️ La limpieza excedió el tiempo límite (5 min).", chat_id=chat_id)
        except Exception as e:
            self.send_message(f"❌ Error ejecutando limpieza: {e}", chat_id=chat_id)
        
        return "Limpieza ejecutada"
    
    # ===== LENGUAJE NATURAL =====
    
    def handle_natural_language(self, text: str, chat_id: str) -> str:
        """Procesa lenguaje natural"""
        text_lower = text.lower()
        
        intents = {
            ("resumen", "resume", "hoy", "qué hay"): self.cmd_resumen,
            ("ual", "universidad", "almería", "almeria"): self.cmd_ual,
            ("esi", "ingenier", "escuela"): self.cmd_esi,
            ("pendiente", "revisar", "revisión", "revision"): self.cmd_pendientes,
            ("sin responder", "no respond", "respondido"): self.cmd_sin_responder,
            ("promo", "publicidad", "spam", "basura"): self.cmd_promos,
            ("protegid", "importante", "no borrar"): self.cmd_protegidos,
            ("estadistica", "stats", "números", "numeros"): self.cmd_estadisticas,
            ("buscar", "encuentra", "busca", "encontrar"): lambda a, c: self._handle_buscar_natural(text, chat_id),
            ("historial", "conversaciones", "anterior"): self.cmd_historial,
            ("semana", "7 días", "últimos días"): self.cmd_stats_semana,
            ("limpiar", "ejecutar", "corre ahora", "haz limpieza"): self.cmd_limpiar,
        }
        
        for keywords, handler in intents.items():
            if any(kw in text_lower for kw in keywords):
                return handler("", chat_id)
        
        # Fallback
        fallback = (
            "🤔 No te entiendo. Usa /help o los botones.\n\n"
            "Ejemplos:\n"
            "• \"Muéstrame resumen de hoy\"\n"
            "• \"Correos de la UAL\"\n"
            "• \"Qué tengo sin responder\"\n"
            "• \"Buscar factura Amazon\"\n"
            "• \"Estadísticas de la semana\""
        )
        self.send_message(fallback, chat_id=chat_id)
        return "Fallback enviado"
    
    def _handle_buscar_natural(self, text: str, chat_id: str) -> str:
        """Extrae el término de búsqueda de un mensaje en lenguaje natural."""
        text_lower = text.lower()
        # Quitar palabras clave de búsqueda
        term = text_lower
        for kw in ["buscar", "encuentra", "busca", "encontrar", "buscar ", "encuentra ", "busca ", "encontrar "]:
            term = term.replace(kw, " ")
        term = term.strip()
        if not term:
            return self.send_message("🔍 ¿Qué quieres buscar? Ej: `/buscar factura Amazon`", chat_id=chat_id)
        return self.cmd_buscar(term, chat_id)
    
    def _format_stats(self, stats: Dict, title: str) -> str:
        if title:
            text = f"{title}\n\n"
        else:
            text = ""
        
        text += "📊 *Estadísticas:*\n"
        text += f"  📰 Newsletters: {stats.get('newsletters_labeled',0)} etiquetadas, {stats.get('newsletters_archived',0)} archivadas\n"
        text += f"  🧾 Recibos: {stats.get('receipts_labeled',0)} etiquetados, {stats.get('receipts_archived',0)} archivados\n"
        text += f"  🏫 UAL: {stats.get('ual_labeled',0)} | ESI: {stats.get('esi_labeled',0)}\n"
        text += f"  ⏳ Sin responder: {stats.get('unreplied_count',0)}\n"
        text += f"  🛡️ Protegidos: {stats.get('protected_skipped',0)}\n"
        text += f"  🗑️ Promos: {stats.get('promos_deleted',0)}\n"
        text += f"  🔄 Duplicados: {stats.get('duplicates_deleted',0)}\n"
        if stats.get('errors', 0):
            text += f"  ⚠️ Errores: {stats.get('errors',0)}\n"
        return text
    
    # ===== LOOP PRINCIPAL =====
    
    def run_polling(self):
        """Ejecuta bot en modo polling 24/7"""
        if not self.base_url:
            logger.error("Bot no configurado - falta TELEGRAM_WEBHOOK")
            return
        
        self.running = True
        logger.info("[BOT] Telegram ChatBot 24/7 iniciado (polling)")
        
        self.send_message(
            "🤖 *Email Cleaner Bot 24/7 ACTIVO*\n\n"
            "✅ Memoria persistente (SQLite)\n"
            "✅ Polling robusto con reintentos\n"
            "✅ Comandos + Lenguaje natural\n\n"
            "Usa /help o los botones 👇",
            reply_markup=self.main_keyboard
        )
        
        while self.running:
            try:
                updates = self.get_updates()
                for update in updates:
                    if not self.running:
                        break
                    self.process_update(update)
            except Exception as e:
                logger.error(f"Error en loop principal: {e}")
                time.sleep(10)  # Pausa antes de reintentar
    
    def stop(self):
        """Detiene el bot gracefully"""
        self.running = False
        logger.info("[STOP] Bot detenido")
        self.send_message("🛑 Bot detenido. Hasta pronto! 👋")


def create_bot_from_env() -> TelegramChatBot:
    """Crea bot desde variables de entorno"""
    if not TELEGRAM_WEBHOOK:
        raise ValueError("TELEGRAM_WEBHOOK no configurado")
    
    # Parsear webhook
    token = None
    chat_id = None
    try:
        parts = TELEGRAM_WEBHOOK.split("/bot")
        if len(parts) > 1:
            token = parts[1].split("/")[0]
        if "chat_id=" in TELEGRAM_WEBHOOK:
            chat_id = TELEGRAM_WEBHOOK.split("chat_id=")[1].split("&")[0]
    except Exception as e:
        raise ValueError(f"Error parseando webhook: {e}")
    
    if not token or not chat_id:
        raise ValueError("Token o chat_id no encontrados en webhook")
    
    # DB path
    db_path = DATA_DIR / "telegram_bot.db"
    
    config = BotConfig(
        token=token,
        chat_id=chat_id,
        admin_chat_id=chat_id,
        db_path=str(db_path),
        poll_interval=30
    )
    
    return TelegramChatBot(config)


def download_latest_db_artifact() -> bool:
    """Descarga el artifact telegram-bot-db más reciente de GitHub Actions.
    
    Permite que el chatbot del VPS conozca los análisis previos del bot de correo
    (correos UAL/ESI, sin responder, stats, etc.) aunque el bot de correo corra
    en GitHub Actions (runner efímero).
    """
    repo = os.getenv("GITHUB_REPO", "")
    token = os.getenv("GITHUB_API_TOKEN", "")
    if not repo or not token:
        logger.warning("GITHUB_REPO o GITHUB_API_TOKEN no configurados, saltando descarga de BD")
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    try:
        # Listar artifacts del repositorio
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/artifacts",
            headers=headers, timeout=30
        )
        resp.raise_for_status()
        artifacts = resp.json().get("artifacts", [])

        # Filtrar telegram-bot-db y ordenar por fecha descendente
        db_artifacts = [a for a in artifacts if a.get("name") == "telegram-bot-db"]
        if not db_artifacts:
            logger.warning("No se encontró artifact 'telegram-bot-db' en GitHub Actions")
            return False

        db_artifacts.sort(key=lambda a: a.get("created_at", ""), reverse=True)
        latest = db_artifacts[0]
        artifact_id = latest["id"]
        download_url = latest.get("archive_download_url") or f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip"

        # Descargar zip del artifact
        resp = requests.get(download_url, headers=headers, timeout=60)
        resp.raise_for_status()

        # Extraer telegram_bot.db del zip
        zip_data = zipfile.ZipFile(io.BytesIO(resp.content))
        data_dir = Path(__file__).parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        for name in zip_data.namelist():
            if name.endswith("telegram_bot.db"):
                target = data_dir / "telegram_bot.db"
                with zip_data.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                logger.info(f"BD del chatbot descargada: {target} ({target.stat().st_size} bytes)")
                return True

        logger.warning("No se encontró telegram_bot.db dentro del artifact")
        return False

    except Exception as e:
        logger.error(f"Error descargando artifact de BD: {e}")
        return False


def main():
    """Punto de entrada"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(LOGS_DIR / "telegram_bot.log"),
            logging.StreamHandler()
        ]
    )
    
    try:
        # Descargar BD más reciente de GitHub Actions (si existe)
        # así el chatbot conoce análisis previos del bot de correo
        download_latest_db_artifact()

        bot = create_bot_from_env()
        bot.run_polling()
    except KeyboardInterrupt:
        logger.info("Interrumpido por usuario")
    except Exception as e:
        logger.error(f"Error fatal: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()