"""
Configuración centralizada del bot de limpieza de correo.
Carga variables de entorno y parámetros generales.
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# Rutas base
BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
SRC_DIR = BASE_DIR / "src"

# Archivos de datos
PROCESSED_IDS_FILE = DATA_DIR / "processed_ids.json"
RULES_FILE = CONFIG_DIR / "rules.json"

# Credenciales Gmail API
GMAIL_CREDENTIALS_FILE = BASE_DIR / "credentials.json"
GMAIL_TOKEN_FILE = BASE_DIR / "token.json"

# Configuración de correo
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

# Configuración Gmail API (OAuth)
GMAIL_CREDENTIALS_JSON = os.getenv("GMAIL_CREDENTIALS_JSON")
GMAIL_TOKEN_JSON = os.getenv("GMAIL_TOKEN_JSON")

# Configuración Telegram (opcional)
TELEGRAM_WEBHOOK = os.getenv("TELEGRAM_WEBHOOK")

# Parámetros de limpieza
DAYS_THRESHOLD = int(os.getenv("DAYS_THRESHOLD", "3"))
DIGEST_TIME = os.getenv("DIGEST_TIME", "08:00")
TIMEZONE = os.getenv("TIMEZONE", "UTC")

# Configuración de logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = LOGS_DIR / "email_cleaner.log"

# Scopes de Gmail API
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.send"
]

# Configuración IMAP (alternativa a Gmail API)
IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
USE_IMAP = os.getenv("USE_IMAP", "false").lower() == "true"


def load_rules() -> dict:
    """Carga las reglas de clasificación desde rules.json"""
    with open(RULES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# Esquema de los IDs guardados. Antes, con IMAP se guardaban números de secuencia
# (cambian cada vez que se borra un correo); ahora se guardan UIDs, que son estables.
# Si el fichero tiene otro esquema se descarta para no mezclar IDs incompatibles.
PROCESSED_IDS_SCHEME = "imap_uid" if USE_IMAP else "gmail_api_id"


def load_processed_ids() -> set:
    """Carga los IDs de correos ya procesados"""
    if not PROCESSED_IDS_FILE.exists():
        return set()
    try:
        with open(PROCESSED_IDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return set()
    if isinstance(data, dict) and data.get("scheme") == PROCESSED_IDS_SCHEME:
        return set(str(i) for i in data.get("ids", []))
    # Formato antiguo (lista de números de secuencia IMAP) u otro esquema: no es fiable
    return set()


def save_processed_ids(processed_ids: set) -> None:
    """Guarda los IDs de correos procesados"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {"scheme": PROCESSED_IDS_SCHEME, "ids": sorted(processed_ids)}
    with open(PROCESSED_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def validate_config() -> bool:
    """Valida que la configuración mínima esté presente"""
    errors = []

    if not EMAIL_USER:
        errors.append("EMAIL_USER no está configurado")

    if USE_IMAP and not EMAIL_APP_PASSWORD:
        errors.append("EMAIL_APP_PASSWORD requerido para IMAP")

    if not USE_IMAP:
        if not GMAIL_CREDENTIALS_JSON and not GMAIL_CREDENTIALS_FILE.exists():
            errors.append("GMAIL_CREDENTIALS_JSON o credentials.json requerido para Gmail API")
        if not GMAIL_TOKEN_JSON and not GMAIL_TOKEN_FILE.exists():
            errors.append("GMAIL_TOKEN_JSON o token.json requerido para Gmail API")

    if errors:
        raise ValueError("Errores de configuración:\n" + "\n".join(f"  - {e}" for e in errors))

    return True