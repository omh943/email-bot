"""
Orquestador principal del bot de limpieza de correo.
Conecta, lee, aplica reglas y envía el resumen.
"""
import sys
import logging
from datetime import datetime
from pathlib import Path

# Añadir directorio raíz al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    validate_config, load_rules, load_processed_ids, save_processed_ids,
    LOG_LEVEL, LOG_FILE, DAYS_THRESHOLD
)
from src.mail_client import MailClient
from src.classifier import EmailClassifier
from src.digest_builder import DigestBuilder

# Configurar logging
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def main() -> int:
    """Función principal del bot"""
    logger.info("=" * 60)
    logger.info("Iniciando Email Cleaner Bot")
    logger.info("=" * 60)

    try:
        # Validar configuración
        validate_config()
        logger.info("Configuración validada correctamente")

        # Cargar reglas y IDs procesados
        rules = load_rules()
        processed_ids = load_processed_ids()
        logger.info(f"Reglas cargadas. IDs procesados previos: {len(processed_ids)}")

        # Inicializar componentes
        mail_client = MailClient(rules)
        classifier = EmailClassifier(rules)
        digest_builder = DigestBuilder(rules)

        # Conectar al servidor de correo
        logger.info("Conectando al servidor de correo...")
        mail_client.connect()

        # Buscar correos recientes (últimos 30 días por defecto)
        logger.info("Buscando correos para procesar...")
        emails = mail_client.fetch_recent_emails(days_back=30)
        logger.info(f"Correos encontrados: {len(emails)}")

        # Clasificar correos
        logger.info("Clasificando correos...")
        classification_results = classifier.classify_batch(emails)

        # Aplicar acciones (etiquetar, archivar)
        logger.info("Aplicando acciones...")
        action_results = mail_client.apply_actions(classification_results)

        # Filtrar correos sin responder para el resumen
        unreplied_emails = [
            email for email in emails
            if email.get("id") not in processed_ids
            and classifier.is_unreplied(email, DAYS_THRESHOLD)
        ]

        # Filtrar correos pendientes de revisión (etiquetados como newsletter/receipt pero no archivados)
        pending_review_emails = [
            email for email in emails
            if email.get("id") not in processed_ids
            and any(r["email"]["id"] == email["id"] and "label_pending_review" in r.get("actions", [])
                    for r in classification_results)
        ]

        # Construir y enviar resumen (siempre enviar si hay algo que reportar)
        if unreplied_emails or pending_review_emails:
            logger.info(f"Enviando resumen: {len(unreplied_emails)} sin responder, {len(pending_review_emails)} pendientes revisión...")
            digest = digest_builder.build_digest(
                unreplied_emails=unreplied_emails,
                pending_review_emails=pending_review_emails,
                stats=action_results,
                threshold_days=DAYS_THRESHOLD
            )
            mail_client.send_digest(digest)

            # Actualizar IDs procesados
            new_processed_ids = {email["id"] for email in unreplied_emails}
            new_processed_ids.update(email["id"] for email in pending_review_emails)
            processed_ids.update(new_processed_ids)
            save_processed_ids(processed_ids)
            logger.info(f"IDs procesados actualizados: {len(processed_ids)} total")
        else:
            logger.info("No hay correos sin responder ni pendientes de revisión para notificar")

        # Cerrar conexión
        mail_client.disconnect()

        logger.info("=" * 60)
        logger.info("Email Cleaner Bot finalizado correctamente")
        logger.info("=" * 60)
        return 0

    except Exception as e:
        logger.exception(f"Error crítico en la ejecución: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())