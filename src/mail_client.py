"""
Cliente de correo: soporta Gmail API (OAuth) e IMAP.
Proporciona una interfaz unificada para leer, etiquetar, archivar y enviar correos.
"""
import os
import json
import base64
import logging
import imaplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from pathlib import Path

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GMAIL_API_AVAILABLE = True
except ImportError:
    GMAIL_API_AVAILABLE = False

import requests
from config.settings import (
    GMAIL_SCOPES, GMAIL_CREDENTIALS_FILE, GMAIL_TOKEN_FILE,
    GMAIL_CREDENTIALS_JSON, GMAIL_TOKEN_JSON,
    EMAIL_USER, EMAIL_APP_PASSWORD,
    IMAP_SERVER, IMAP_PORT, USE_IMAP,
    TELEGRAM_WEBHOOK
)

logger = logging.getLogger(__name__)


class MailClient:
    """Cliente unificado para Gmail API e IMAP"""

    def __init__(self, rules: Dict = None):
        self.service = None
        self.imap_conn = None
        self.labels_cache = {}
        self._use_gmail_api = not USE_IMAP and GMAIL_API_AVAILABLE
        self._rules = rules or {}

    def connect(self) -> None:
        """Establece conexión con el servidor de correo"""
        if self._use_gmail_api:
            self._connect_gmail_api()
        else:
            self._connect_imap()

    def _connect_gmail_api(self) -> None:
        """Conecta usando Gmail API con OAuth"""
        logger.info("Conectando via Gmail API (OAuth)...")
        creds = None

        # Cargar token guardado
        if GMAIL_TOKEN_JSON:
            creds = Credentials.from_authorized_user_info(json.loads(GMAIL_TOKEN_JSON), GMAIL_SCOPES)
        elif GMAIL_TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_FILE), GMAIL_SCOPES)

        # Renovar o obtener nuevas credenciales
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info("Renovando token expirado...")
                creds.refresh(Request())
            else:
                logger.info("Iniciando flujo OAuth...")
                if GMAIL_CREDENTIALS_JSON:
                    creds_data = json.loads(GMAIL_CREDENTIALS_JSON)
                    flow = InstalledAppFlow.from_client_config(creds_data, GMAIL_SCOPES)
                elif GMAIL_CREDENTIALS_FILE.exists():
                    flow = InstalledAppFlow.from_client_secrets_file(str(GMAIL_CREDENTIALS_FILE), GMAIL_SCOPES)
                else:
                    raise ValueError("No se encontraron credenciales de Gmail API")
                creds = flow.run_local_server(port=0)

            # Guardar token
            token_data = creds.to_json()
            if GMAIL_TOKEN_JSON:
                # En GitHub Actions, el token se pasa como secret
                logger.info("Token renovado (secreto externo)")
            else:
                with open(GMAIL_TOKEN_FILE, "w") as f:
                    f.write(token_data)

        self.service = build("gmail", "v1", credentials=creds)
        self._load_labels_cache()
        logger.info("Gmail API conectada correctamente")

    def _connect_imap(self) -> None:
        """Conecta usando IMAP"""
        logger.info(f"Conectando via IMAP a {IMAP_SERVER}:{IMAP_PORT}...")
        self.imap_conn = imaplib.IMAP4_SSL(IMAP_SERVER, IMAP_PORT)
        self.imap_conn.login(EMAIL_USER, EMAIL_APP_PASSWORD)
        self.imap_conn.select("INBOX")
        logger.info("IMAP conectado correctamente")

    def _load_labels_cache(self) -> None:
        """Carga la caché de etiquetas de Gmail"""
        if not self.service:
            return
        try:
            results = self.service.users().labels().list(userId="me").execute()
            labels = results.get("labels", [])
            self.labels_cache = {label["name"]: label["id"] for label in labels}
            logger.debug(f"Etiquetas cargadas: {len(self.labels_cache)}")
        except HttpError as e:
            logger.warning(f"No se pudieron cargar etiquetas: {e}")

    def _get_or_create_label(self, label_name: str) -> str:
        """Obtiene ID de etiqueta o la crea si no existe (Gmail API)"""
        if label_name in self.labels_cache:
            return self.labels_cache[label_name]

        try:
            label_body = {
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show"
            }
            created = self.service.users().labels().create(userId="me", body=label_body).execute()
            self.labels_cache[label_name] = created["id"]
            logger.info(f"Etiqueta creada: {label_name}")
            return created["id"]
        except HttpError as e:
            logger.error(f"Error creando etiqueta {label_name}: {e}")
            raise

    def fetch_recent_emails(self, days_back: int = 30, max_results: int = 500) -> List[Dict[str, Any]]:
        """Obtiene correos recientes"""
        if self._use_gmail_api:
            return self._fetch_gmail_api(days_back, max_results)
        else:
            return self._fetch_imap(days_back, max_results)

    def _fetch_gmail_api(self, days_back: int, max_results: int) -> List[Dict[str, Any]]:
        """Obtiene correos via Gmail API"""
        query = f"newer_than:{days_back}d"
        emails = []
        page_token = None

        while len(emails) < max_results:
            try:
                results = self.service.users().messages().list(
                    userId="me",
                    q=query,
                    maxResults=min(100, max_results - len(emails)),
                    pageToken=page_token
                ).execute()

                messages = results.get("messages", [])
                if not messages:
                    break

                # Obtener detalles de cada mensaje
                for msg in messages:
                    detail = self.service.users().messages().get(
                        userId="me", id=msg["id"], format="full"
                    ).execute()
                    emails.append(self._parse_gmail_message(detail))

                page_token = results.get("nextPageToken")
                if not page_token:
                    break

            except HttpError as e:
                logger.error(f"Error obteniendo correos: {e}")
                break

        return emails

    def _fetch_imap(self, days_back: int, max_results: int) -> List[Dict[str, Any]]:
        """Obtiene correos via IMAP"""
        since_date = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
        status, messages = self.imap_conn.search(None, f'(SINCE "{since_date}")')

        if status != "OK":
            logger.error("Error buscando correos IMAP")
            return []

        email_ids = messages[0].split()[-max_results:]  # Los más recientes
        emails = []

        for eid in reversed(email_ids):  # Procesar del más nuevo al más viejo
            try:
                status, msg_data = self.imap_conn.fetch(eid, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = msg_data[0][1]
                parsed = self._parse_imap_message(eid.decode(), raw_email)
                emails.append(parsed)
            except Exception as e:
                logger.warning(f"Error parseando correo {eid}: {e}")

        return emails

    def _parse_gmail_message(self, msg: Dict) -> Dict[str, Any]:
        """Parsea un mensaje de Gmail API"""
        payload = msg.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}

        # Extraer cuerpo
        body = self._extract_gmail_body(payload)

        return {
            "id": msg["id"],
            "thread_id": msg.get("threadId"),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "body": body,
            "snippet": msg.get("snippet", ""),
            "labels": msg.get("labelIds", []),
            "raw_headers": headers
        }

    def _extract_gmail_body(self, payload: Dict) -> str:
        """Extrae el cuerpo de texto de un payload de Gmail"""
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                elif part.get("mimeType") == "text/html":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                elif "parts" in part:
                    result = self._extract_gmail_body(part)
                    if result:
                        return result
        else:
            if payload.get("mimeType") in ("text/plain", "text/html"):
                data = payload.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
        return ""

    def _parse_imap_message(self, msg_id: str, raw_email: bytes) -> Dict[str, Any]:
        """Parsea un mensaje IMAP"""
        msg = email.message_from_bytes(raw_email)

        # Extraer cuerpo
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                    break
                elif part.get_content_type() == "text/html" and not body:
                    body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
        else:
            body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")

        return {
            "id": msg_id,
            "thread_id": msg_id,
            "subject": msg.get("Subject", ""),
            "from": msg.get("From", ""),
            "to": msg.get("To", ""),
            "date": msg.get("Date", ""),
            "body": body,
            "snippet": body[:200],
            "labels": [],
            "raw_headers": dict(msg.items())
        }

    def apply_actions(self, classification_results: List[Dict]) -> Dict[str, int]:
        """Aplica acciones de etiquetado y archivado"""
        stats = {
            "newsletters_labeled": 0,
            "newsletters_archived": 0,
            "receipts_labeled": 0,
            "receipts_archived": 0,
            "pending_review": 0,
            "protected_skipped": 0,
            "errors": 0
        }

        for result in classification_results:
            email_id = result["email"]["id"]
            category = result["category"]
            actions = result.get("actions", [])

            try:
                if category == "protected":
                    stats["protected_skipped"] += 1
                    logger.debug(f"Correo protegido omitido: {email_id}")
                    continue
                    
                if self._use_gmail_api:
                    self._apply_actions_gmail(email_id, category, actions)
                else:
                    self._apply_actions_imap(email_id, category, actions)

                # Actualizar estadísticas
                if "label_newsletter" in actions:
                    stats["newsletters_labeled"] += 1
                if "archive" in actions and category == "newsletter":
                    stats["newsletters_archived"] += 1
                if "label_receipt" in actions:
                    stats["receipts_labeled"] += 1
                if "archive" in actions and category == "receipt":
                    stats["receipts_archived"] += 1
                if "label_pending_review" in actions:
                    stats["pending_review"] += 1

            except Exception as e:
                logger.error(f"Error aplicando acciones a {email_id}: {e}")
                stats["errors"] += 1

        return stats

    def _apply_actions_gmail(self, email_id: str, category: str, actions: List[str]) -> None:
        """Aplica acciones via Gmail API"""
        label_ids = []
        remove_label_ids = []

        if category == "newsletter":
            if "label_newsletter" in actions:
                label_ids.append(self._get_or_create_label("Newsletter"))
            if "archive" in actions:
                remove_label_ids.append("INBOX")

        elif category == "receipt":
            if "label_receipt" in actions:
                label_ids.append(self._get_or_create_label("Recibos"))
            if "archive" in actions:
                remove_label_ids.append("INBOX")

        # Etiqueta de revisión pendiente (para ambos tipos)
        if "label_pending_review" in actions:
            label_ids.append(self._get_or_create_label("Pending_Review"))

        if label_ids or remove_label_ids:
            self.service.users().messages().modify(
                userId="me",
                id=email_id,
                body={"addLabelIds": label_ids, "removeLabelIds": remove_label_ids}
            ).execute()

    def _apply_actions_imap(self, email_id: str, category: str, actions: List[str]) -> None:
        """Aplica acciones via IMAP (limitado: solo mover a carpetas)"""
        # IMAP no soporta etiquetas nativas como Gmail, usamos carpetas
        folder_map = {
            "newsletter": "INBOX/Newsletter",
            "receipt": "INBOX/Recibos"
        }

        if category in folder_map and "archive" in actions:
            folder = folder_map[category]
            # Crear carpeta si no existe
            self.imap_conn.create(folder)
            # Mover correo
            self.imap_conn.copy(email_id, folder)
            self.imap_conn.store(email_id, "+FLAGS", "\\Deleted")

        # Carpeta de revisión pendiente
        if "label_pending_review" in actions:
            folder = "INBOX/Pending_Review"
            self.imap_conn.create(folder)
            self.imap_conn.copy(email_id, folder)
            # No eliminar de INBOX en modo confirmación, solo copiar

        # Expulsar eliminados
        self.imap_conn.expunge()

    def send_digest(self, digest: Dict[str, Any]) -> bool:
        """Envía el resumen por correo y Telegram"""
        email_sent = False
        telegram_sent = False
        
        # Enviar por email
        try:
            if self._use_gmail_api:
                email_sent = self._send_digest_gmail(digest)
            else:
                email_sent = self._send_digest_imap(digest)
        except Exception as e:
            logger.error(f"Error enviando resumen por email: {e}")
        
        # Enviar por Telegram si hay webhook configurado
        if TELEGRAM_WEBHOOK:
            try:
                telegram_sent = self._send_digest_telegram(digest)
            except Exception as e:
                logger.error(f"Error enviando resumen por Telegram: {e}")
        
        return email_sent or telegram_sent
    
    def _send_digest_telegram(self, digest: Dict) -> bool:
        """Envía resumen via Telegram Bot API"""
        import requests
        from src.digest_builder import DigestBuilder
        
        if not TELEGRAM_WEBHOOK:
            return False
            
        # Usar el builder para crear mensaje en formato Markdown
        builder = DigestBuilder(self._rules)
        message = builder.build_telegram_message(digest)
        
        # Extraer token y chat_id del webhook
        # Formato: https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<CHAT_ID>
        try:
            webhook_url = TELEGRAM_WEBHOOK
            # Extraer token
            token_part = webhook_url.split("/bot")[1].split("/")[0]
            # Extraer chat_id
            chat_id = webhook_url.split("chat_id=")[1].split("&")[0] if "chat_id=" in webhook_url else None
            
            if not chat_id:
                logger.warning("No se pudo extraer chat_id del webhook Telegram")
                return False
                
            base_url = f"https://api.telegram.org/bot{token_part}"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            }
            
            response = requests.post(f"{base_url}/sendMessage", json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info("Resumen enviado via Telegram")
            return True
        except Exception as e:
            logger.error(f"Error enviando a Telegram: {e}")
            return False

    def _send_digest_gmail(self, digest: Dict) -> bool:
        """Envía resumen via Gmail API"""
        message = MIMEMultipart("alternative")
        message["to"] = EMAIL_USER
        message["from"] = EMAIL_USER
        message["subject"] = digest["subject"]

        # Adjuntar versión texto y HTML
        text_part = MIMEText(digest["text_body"], "plain", "utf-8")
        html_part = MIMEText(digest["html_body"], "html", "utf-8")
        message.attach(text_part)
        message.attach(html_part)

        # Codificar
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        body = {"raw": raw}

        self.service.users().messages().send(userId="me", body=body).execute()
        logger.info("Resumen enviado via Gmail API")
        return True

    def _send_digest_imap(self, digest: Dict) -> bool:
        """Envía resumen via SMTP (usando configuración IMAP para servidor)"""
        import smtplib

        smtp_server = IMAP_SERVER.replace("imap", "smtp")
        smtp_port = 587 if IMAP_PORT == 993 else 465

        message = MIMEMultipart("alternative")
        message["to"] = EMAIL_USER
        message["from"] = EMAIL_USER
        message["subject"] = digest["subject"]
        message.attach(MIMEText(digest["text_body"], "plain", "utf-8"))
        message.attach(MIMEText(digest["html_body"], "html", "utf-8"))

        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(EMAIL_USER, EMAIL_APP_PASSWORD)
            server.send_message(message)

        logger.info("Resumen enviado via SMTP")
        return True

    def disconnect(self) -> None:
        """Cierra conexiones"""
        if self.imap_conn:
            try:
                self.imap_conn.close()
                self.imap_conn.logout()
            except Exception:
                pass
        logger.info("Conexiones cerradas")