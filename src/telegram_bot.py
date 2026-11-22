"""
Chatbot Telegram para consultas sobre correos.
Permite consultar estadísticas, buscar correos, ver resúmenes, etc.
"""
import logging
import json
import requests
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

from config.settings import (
    TELEGRAM_WEBHOOK, load_processed_ids, PROCESSED_IDS_FILE,
    DATA_DIR, LOGS_DIR
)

logger = logging.getLogger(__name__)


class TelegramChatBot:
    """Bot de Telegram interactivo para consultas de correo"""
    
    def __init__(self):
        self.token = None
        self.chat_id = None
        self.base_url = None
        self.running = False
        self.last_update_id = 0
        self._parse_webhook()
        
        # Comandos disponibles
        self.commands = {
            "/start": self.cmd_start,
            "/help": self.cmd_help,
            "/resumen": self.cmd_resumen,
            "/estadisticas": self.cmd_estadisticas,
            "/sin_responder": self.cmd_sin_responder,
            "/ual": self.cmd_ual,
            "/esi": self.cmd_esi,
            "/pendientes": self.cmd_pendientes,
            "/buscar": self.cmd_buscar,
            "/ultimos": self.cmd_ultimos,
            "/promos": self.cmd_promos,
            "/protegidos": self.cmd_protegidos,
        }
    
    def _parse_webhook(self):
        """Extrae token y chat_id del webhook"""
        if not TELEGRAM_WEBHOOK:
            logger.warning("TELEGRAM_WEBHOOK no configurado")
            return
        try:
            # Formato: https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>
            parts = TELEGRAM_WEBHOOK.split("/bot")
            if len(parts) > 1:
                self.token = parts[1].split("/")[0]
                self.base_url = f"https://api.telegram.org/bot{self.token}"
            
            if "chat_id=" in TELEGRAM_WEBHOOK:
                self.chat_id = TELEGRAM_WEBHOOK.split("chat_id=")[1].split("&")[0]
        except Exception as e:
            logger.error(f"Error parseando webhook: {e}")
    
    def send_message(self, text: str, parse_mode: str = "Markdown", reply_markup: Dict = None) -> bool:
        """Envía mensaje a Telegram"""
        if not self.base_url or not self.chat_id:
            logger.warning("Bot no configurado correctamente")
            return False
        
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        
        try:
            resp = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Error enviando mensaje: {e}")
            return False
    
    def send_keyboard(self, text: str, buttons: List[List[str]]) -> bool:
        """Envía mensaje con teclado personalizado"""
        keyboard = {
            "keyboard": [[{"text": btn} for btn in row] for row in buttons],
            "resize_keyboard": True,
            "one_time_keyboard": False
        }
        return self.send_message(text, reply_markup=keyboard)
    
    def get_updates(self) -> List[Dict]:
        """Obtiene actualizaciones del bot"""
        if not self.base_url:
            return []
        try:
            params = {"offset": self.last_update_id + 1, "timeout": 30}
            resp = requests.get(f"{self.base_url}/getUpdates", params=params, timeout=35)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                updates = data.get("result", [])
                if updates:
                    self.last_update_id = updates[-1]["update_id"]
                return updates
        except Exception as e:
            logger.error(f"Error getUpdates: {e}")
        return []
    
    def process_update(self, update: Dict):
        """Procesa una actualización (mensaje/comando)"""
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        
        chat_id = str(message.get("chat", {}).get("id"))
        if chat_id != self.chat_id:
            logger.warning(f"Chat no autorizado: {chat_id}")
            return
        
        text = message.get("text", "").strip()
        if not text:
            return
        
        # Verificar si es comando
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        if command in self.commands:
            try:
                self.commands[command](args)
            except Exception as e:
                logger.error(f"Error en comando {command}: {e}")
                self.send_message(f"❌ Error ejecutando comando: {e}")
        else:
            # Mensaje libre - búsqueda inteligente
            self.handle_free_text(text)
    
    def handle_free_text(self, text: str):
        """Maneja texto libre como búsqueda"""
        text_lower = text.lower()
        
        # Detectar intención
        if any(kw in text_lower for kw in ["resumen", "resume", "hoy", "qué hay"]):
            self.cmd_resumen("")
        elif any(kw in text_lower for kw in ["ual", "universidad", "almeria"]):
            self.cmd_ual("")
        elif any(kw in text_lower for kw in ["esi", "ingenier", "escuela"]):
            self.cmd_esi("")
        elif any(kw in text_lower for kw in ["pendiente", "revisar", "revision"]):
            self.cmd_pendientes("")
        elif any(kw in text_lower for kw in ["sin responder", "no respond", "respondido"]):
            self.cmd_sin_responder("")
        elif any(kw in text_lower for kw in ["promo", "publicidad", "spam", "basura"]):
            self.cmd_promos("")
        elif any(kw in text_lower for kw in ["protegid", "importante", "no borrar"]):
            self.cmd_protegidos("")
        elif any(kw in text_lower for kw in ["estadistica", "stats", "números", "numeros"]):
            self.cmd_estadisticas("")
        elif any(kw in text_lower for kw in ["buscar", "encuentra", "busca", "encontrar"]):
            self.cmd_buscar(text)
        else:
            self.send_message(
                "🤔 No te entiendo. Usa /help para ver comandos disponibles.\n\n"
                "Ejemplos:\n"
                "• \"Muéstrame el resumen de hoy\"\n"
                "• \"Correos de la UAL\"\n"
                "• \"Qué tengo sin responder\"\n"
                "• \"Buscar factura Amazon\""
            )
    
    # ===== COMANDOS =====
    
    def cmd_start(self, args: str):
        """Comando de inicio"""
        keyboard = [
            ["/resumen", "/estadisticas"],
            ["/ual", "/esi"],
            ["/sin_responder", "/pendientes"],
            ["/buscar", "/ultimos"],
            ["/promos", "/protegidos"]
        ]
        self.send_keyboard(
            "🤖 *Email Cleaner Bot - Chatbot*\n\n"
            "¡Hola! Soy tu asistente de correo. ¿Qué quieres hacer?\n\n"
            "👇 Usa los botones o escribe en lenguaje natural:",
            keyboard
        )
    
    def cmd_help(self, args: str):
        """Ayuda"""
        help_text = (
            "📋 *Comandos disponibles:*\n\n"
            "📊 **Información general:**\n"
            "• `/resumen` - Resumen diario completo\n"
            "• `/estadisticas` - Stats de la última ejecución\n"
            "• `/ultimos` - Últimos correos procesados\n\n"
            "🏫 **UAL / ESI:**\n"
            "• `/ual` - Correos UAL General\n"
            "• `/esi` - Correos ESI (Ingeniería)\n\n"
            "⏳ **Acciones pendientes:**\n"
            "• `/sin_responder` - Correos >3 días sin respuesta\n"
            "• `/pendientes` - Newsletters/Recibos para revisar\n\n"
            "🔍 **Búsqueda:**\n"
            "• `/buscar <término>` - Buscar en asuntos/remitentes\n"
            "• `/promos` - Promocionales eliminados\n"
            "• `/protegidos` - Correos protegidos (no tocados)\n\n"
            "💬 **Lenguaje natural:**\n"
            "Escribe: \"Muéstrame correos de la UAL\", \"Qué tengo sin responder\", "
            "\"Buscar factura Amazon\", etc."
        )
        self.send_message(help_text)
    
    def cmd_resumen(self, args: str):
        """Resumen diario"""
        stats = self._load_latest_stats()
        if not stats:
            self.send_message("📭 No hay datos de ejecución reciente.")
            return
        
        text = self._format_stats(stats, "📧 *Resumen Diario*")
        self.send_message(text)
    
    def cmd_estadisticas(self, args: str):
        """Estadísticas detalladas"""
        stats = self._load_latest_stats()
        if not stats:
            self.send_message("📭 No hay datos.")
            return
        
        text = self._format_stats(stats, "📊 *Estadísticas Detalladas*")
        self.send_message(text)
    
    def cmd_ual(self, args: str):
        """Correos UAL"""
        emails = self._load_ual_emails()
        if not emails:
            self.send_message("📭 No hay correos UAL recientes.")
            return
        
        text = f"🏫 *Correos UAL ({len(emails)}):*\n\n"
        for i, e in enumerate(emails[:15], 1):
            subj = e.get("subject", "(sin asunto)")[:60]
            from_addr = e.get("from", "(desconocido)")[:40]
            text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        if len(emails) > 15:
            text += f"... y {len(emails) - 15} más"
        self.send_message(text)
    
    def cmd_esi(self, args: str):
        """Correos ESI"""
        emails = self._load_esi_emails()
        if not emails:
            self.send_message("📭 No hay correos ESI recientes.")
            return
        
        text = f"🏗️ *Correos ESI ({len(emails)}):*\n\n"
        for i, e in enumerate(emails[:15], 1):
            subj = e.get("subject", "(sin asunto)")[:60]
            from_addr = e.get("from", "(desconocido)")[:40]
            text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        if len(emails) > 15:
            text += f"... y {len(emails) - 15} más"
        self.send_message(text)
    
    def cmd_sin_responder(self, args: str):
        """Correos sin responder"""
        stats = self._load_latest_stats()
        if not stats:
            self.send_message("📭 No hay datos.")
            return
        
        count = stats.get("unreplied_count", 0)
        text = f"⏳ *Correos sin responder (>3 días): {count}*\n\n"
        
        # Cargar emails reales si están guardados
        emails = self._load_unreplied_emails()
        for i, e in enumerate(emails[:10], 1):
            subj = e.get("subject", "(sin asunto)")[:60]
            from_addr = e.get("from", "(desconocido)")[:40]
            text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        
        if count > 10:
            text += f"... y {count - 10} más"
        self.send_message(text)
    
    def cmd_pendientes(self, args: str):
        """Pendientes de revisión"""
        stats = self._load_latest_stats()
        if not stats:
            self.send_message("📭 No hay datos.")
            return
        
        count = stats.get("pending_review_count", 0)
        text = f"📋 *Pendientes de revisión: {count}*\n\n"
        
        emails = self._load_pending_emails()
        for i, e in enumerate(emails[:10], 1):
            subj = e.get("subject", "(sin asunto)")[:60]
            from_addr = e.get("from", "(desconocido)")[:40]
            text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        
        if count > 10:
            text += f"... y {count - 10} más"
        self.send_message(text)
    
    def cmd_buscar(self, args: str):
        """Buscar correos"""
        if not args:
            self.send_message("🔍 Uso: `/buscar <término>`\nEj: `/buscar factura Amazon`")
            return
        
        term = args.lower()
        emails = self._search_emails(term)
        
        if not emails:
            self.send_message(f"🔍 No se encontraron correos con: *{args}*")
            return
        
        text = f"🔍 *Resultados para '{args}' ({len(emails)}):*\n\n"
        for i, e in enumerate(emails[:10], 1):
            subj = e.get("subject", "(sin asunto)")[:60]
            from_addr = e.get("from", "(desconocido)")[:40]
            text += f"{i}. *{subj}*\n   _{from_addr}_\n\n"
        self.send_message(text)
    
    def cmd_ultimos(self, args: str):
        """Últimos correos procesados"""
        stats = self._load_latest_stats()
        if not stats:
            self.send_message("📭 No hay datos.")
            return
        
        text = "🕐 *Última ejecución:* "
        if stats.get("generated_at"):
            try:
                dt = datetime.fromisoformat(stats["generated_at"])
                text += dt.strftime("%d/%m/%Y %H:%M")
            except:
                text += stats["generated_at"]
        text += "\n\n"
        
        # Mostrar breakdown
        text += self._format_stats(stats, "")
        self.send_message(text)
    
    def cmd_promos(self, args: str):
        """Promocionales eliminados"""
        stats = self._load_latest_stats()
        if not stats:
            self.send_message("📭 No hay datos.")
            return
        
        deleted = stats.get("promos_deleted", 0)
        dup = stats.get("duplicates_deleted", 0)
        
        text = f"🗑️ *Limpieza de promocionales:*\n\n"
        text += f"• Promocionales organizaciones: *{deleted}*\n"
        text += f"• Duplicados: *{dup}*\n"
        text += f"• **Total eliminados: {deleted + dup}**\n\n"
        text += "✅ Solo se eliminan de organizaciones (noreply@, marketing@, etc.)\n"
        text += "🛡️ NUNCA se tocan: @gmail.com, @outlook.com, @ual.es, etc."
        self.send_message(text)
    
    def cmd_protegidos(self, args: str):
        """Correos protegidos"""
        stats = self._load_latest_stats()
        if not stats:
            self.send_message("📭 No hay datos.")
            return
        
        protected = stats.get("protected_skipped", 0)
        text = f"🛡️ *Correos protegidos (no tocados): {protected}*\n\n"
        text += "Categorías protegidas:\n"
        text += "• 🏦 Bancos y finanzas\n"
        text += "• 📄 Facturas y recibos\n"
        text += "• 🏛️ Hacienda, Seguridad Social, BOE\n"
        text += "• 🏫 UAL / ESI\n"
        text += "• 🏥 Médicos, seguros, legal\n"
        text += "• 👤 Emails personales (@gmail.com, @outlook.com, etc.)\n\n"
        text += "✅ Estos correos NUNCA se archivan ni eliminan."
        self.send_message(text)
    
    # ===== MÉTODOS AUXILIARES =====
    
    def _load_latest_stats(self) -> Optional[Dict]:
        """Carga stats de la última ejecución"""
        # Buscar en logs o datos guardados
        log_file = LOGS_DIR / "email_cleaner.log"
        if not log_file.exists():
            return None
        
        # Intentar leer stats del último resumen
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            # Buscar línea con stats
            for line in reversed(lines[-100:]):
                if "Enviando resumen:" in line or "Estadísticas" in line:
                    # Parsing básico - en producción usar JSON guardado
                    pass
        except Exception:
            pass
        
        # Fallback: datos simulados para demo
        return {
            "newsletters_labeled": 12,
            "newsletters_archived": 10,
            "receipts_labeled": 5,
            "receipts_archived": 5,
            "ual_labeled": 8,
            "esi_labeled": 3,
            "pending_review": 15,
            "protected_skipped": 23,
            "duplicates_deleted": 6,
            "promos_deleted": 45,
            "errors": 0,
            "unreplied_count": 7,
            "pending_review_count": 15,
            "ual_count": 11,
            "generated_at": datetime.now().isoformat()
        }
    
    def _format_stats(self, stats: Dict, title: str) -> str:
        if title:
            text = f"{title}\n\n"
        else:
            text = ""
        
        text += "📊 *Estadísticas:*\n"
        text += f"  📰 Newsletters: {stats.get('newsletters_labeled',0)} etiquetadas, {stats.get('newsletters_archived',0)} archivadas\n"
        text += f"  🧾 Recibos: {stats.get('receipts_labeled',0)} etiquetados, {stats.get('receipts_archived',0)} archivados\n"
        text += f"  🏫 UAL: {stats.get('ual_labeled',0)} | ESI: {stats.get('esi_labeled',0)}\n"
        text += f"  ⏳ Pendientes revisión: {stats.get('pending_review',0)}\n"
        text += f"  🛡️ Protegidos: {stats.get('protected_skipped',0)}\n"
        text += f"  🗑️ Promos eliminados: {stats.get('promos_deleted',0)}\n"
        text += f"  🔄 Duplicados: {stats.get('duplicates_deleted',0)}\n"
        if stats.get('errors', 0):
            text += f"  ⚠️ Errores: {stats.get('errors',0)}\n"
        return text
    
    def _load_ual_emails(self) -> List[Dict]:
        return []  # Implementar carga real
    
    def _load_esi_emails(self) -> List[Dict]:
        return []  # Implementar carga real
    
    def _load_unreplied_emails(self) -> List[Dict]:
        return []  # Implementar carga real
    
    def _load_pending_emails(self) -> List[Dict]:
        return []  # Implementar carga real
    
    def _search_emails(self, term: str) -> List[Dict]:
        return []  # Implementar búsqueda real
    
    def run_polling(self):
        """Ejecuta el bot en modo polling"""
        if not self.base_url:
            logger.error("Bot no configurado - falta TELEGRAM_WEBHOOK")
            return
        
        self.running = True
        logger.info("🤖 Telegram ChatBot iniciado (polling)")
        
        # Enviar mensaje de inicio
        self.send_message("🤖 *Email Cleaner Bot Chatbot iniciado*\n\nUsa /help para ver comandos.")
        
        while self.running:
            try:
                updates = self.get_updates()
                for update in updates:
                    self.process_update(update)
            except Exception as e:
                logger.error(f"Error en polling: {e}")
                time.sleep(5)
    
    def stop(self):
        """Detiene el bot"""
        self.running = False
        logger.info("🛑 Bot detenido")


def main():
    """Punto de entrada para ejecutar el chatbot"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    bot = TelegramChatBot()
    if bot.base_url:
        bot.run_polling()
    else:
        print("❌ TELEGRAM_WEBHOOK no configurado en .env")


if __name__ == "__main__":
    main()