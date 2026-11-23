"""
Orquestador principal del bot de limpieza de correo.
Conecta, lee, aplica reglas y envía el resumen.
"""
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Añadir directorio raíz al path para imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import (
    validate_config, load_rules, load_processed_ids, save_processed_ids,
    LOG_LEVEL, LOG_FILE, DAYS_THRESHOLD, DATA_DIR
)
from src.mail_client import MailClient
from src.classifier import EmailClassifier
from src.digest_builder import DigestBuilder

# Importar database manager para chatbot
try:
    from src.telegram_bot import DatabaseManager
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    DatabaseManager = None

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

        # Inicializar base de datos para chatbot
        db = None
        if DB_AVAILABLE:
            db_path = DATA_DIR / "telegram_bot.db"
            db = DatabaseManager(str(db_path))
            logger.info("Base de datos chatbot inicializada")

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

        # Guardar emails en BD para chatbot
        if db:
            # Enriquecer emails con categoría y acciones
            enriched_emails = []
            for result in classification_results:
                email = result["email"].copy()
                email["category"] = result["category"]
                email["actions"] = result.get("actions", [])
                enriched_emails.append(email)
            db.cache_emails(enriched_emails)
            logger.info(f"Emails cacheados en BD: {len(enriched_emails)}")

        # Inicializar stats
        action_results = {
            "newsletters_labeled": 0,
            "newsletters_archived": 0,
            "receipts_labeled": 0,
            "receipts_archived": 0,
            "ual_labeled": 0,
            "esi_labeled": 0,
            "protected_skipped": 0,
            "duplicates_deleted": 0,
            "promos_deleted": 0,
            "errors": 0
        }

        # Detectar y eliminar duplicados promocionales
        duplicates = []
        if classifier.dup_enabled:
            logger.info("Detectando duplicados promocionales...")
            duplicates = classifier.find_duplicates(emails)
            if duplicates:
                logger.info(f"Eliminando {len(duplicates)} duplicados promocionales...")
                duplicate_ids = [d["id"] for d in duplicates]
                deleted = mail_client.delete_emails(duplicate_ids)
                action_results["duplicates_deleted"] = deleted
                logger.info(f"Duplicados eliminados: {deleted}")
                emails = [e for e in emails if e["id"] not in duplicate_ids]

        # Filtrar classification_results para quitar emails ya borrados como duplicados
        duplicate_ids = {d["id"] for d in duplicates}
        if duplicate_ids:
            classification_results = [
                r for r in classification_results
                if r["email"]["id"] not in duplicate_ids
            ]

        # Aplicar acciones (etiquetar, archivar, auto-eliminar promos)
        logger.info("Aplicando acciones...")
        action_results = mail_client.apply_actions(classification_results, emails, classifier)

        # Categoría asignada a cada correo (necesaria para separar UAL y ESI en el digest)
        category_by_id = {
            r["email"]["id"]: r.get("category")
            for r in classification_results
        }

        # Filtrar correos sin responder para el resumen (UAL/ESI tienen su propia sección)
        unreplied_emails = [
            email for email in emails
            if email.get("id") not in processed_ids
            and category_by_id.get(email["id"]) not in ("ual", "esi")
            and classifier.is_unreplied(email, DAYS_THRESHOLD)
        ]

        # Filtrar correos UAL/ESI para sección especial en digest (máx. 14 días de antigüedad)
        ual_emails = []
        for email in emails:
            if email.get("id") in processed_ids:
                continue
            category = category_by_id.get(email["id"])
            if category not in ("ual", "esi"):
                continue
            if not DigestBuilder._is_recent(email, classifier.ual_max_age_days):
                continue
            enriched = dict(email)
            enriched["category"] = category
            ual_emails.append(enriched)

        ual_emails.sort(key=DigestBuilder._email_date, reverse=True)

        ual_count = sum(1 for e in ual_emails if e.get("category") == "ual")
        esi_count = sum(1 for e in ual_emails if e.get("category") == "esi")
        logger.info(
            f"UAL/ESI de los últimos {classifier.ual_max_age_days} días: "
            f"{ual_count} UAL, {esi_count} ESI"
        )

        # Construir y enviar resumen (siempre enviar si hay algo que reportar)
        if unreplied_emails or ual_emails:
            logger.info(f"Enviando resumen: {len(unreplied_emails)} sin responder, {len(ual_emails)} UAL/ESI...")
            digest = digest_builder.build_digest(
                unreplied_emails=unreplied_emails,
                ual_emails=ual_emails,
                stats=action_results,
                threshold_days=DAYS_THRESHOLD
            )
            mail_client.send_digest(digest)

            # Actualizar IDs procesados
            new_processed_ids = {email["id"] for email in unreplied_emails}
            new_processed_ids.update(email["id"] for email in ual_emails)
            processed_ids.update(new_processed_ids)
            save_processed_ids(processed_ids)
            logger.info(f"IDs procesados actualizados: {len(processed_ids)} total")
        else:
            logger.info("No hay correos sin responder ni UAL/ESI para notificar")

        # Guardar stats en BD para chatbot
        if db:
            action_results["generated_at"] = datetime.now().isoformat()
            action_results["unreplied_count"] = len(unreplied_emails)
            action_results["ual_count"] = len(ual_emails)
            db.save_daily_stats(action_results)
            logger.info("Stats diarias guardadas en BD")

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