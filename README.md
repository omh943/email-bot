# 📧 Email Cleaner Bot

Bot automatizado para limpiar y organizar tu bandeja de entrada de Gmail:
- 🏷️ **Etiqueta automáticamente** newsletters y boletines
- 📦 **Archiva recibos y facturas** en carpetas dedicadas
- 📋 **Envía resumen diario** de correos sin responder (>3 días)
- ⚙️ **Configurable** via JSON y variables de entorno
- 🔒 **Seguro**: usa OAuth o App Passwords, nunca guarda contraseñas en código

---

## 🏗️ Arquitectura

```
email-cleaner-bot/
├── .github/workflows/daily_cleanup.yml   # GitHub Actions: ejecución diaria 8:00 AM
├── config/
│   ├── rules.json                        # Reglas de clasificación (keywords, remitentes, etiquetas)
│   └── settings.py                       # Carga de config, validación, helpers
├── data/
│   └── processed_ids.json                # (gitignored) IDs notificados para no repetir
├── logs/                                 # (gitignored) Logs de ejecución
├── src/
│   ├── main.py                           # Orquestador principal
│   ├── mail_client.py                    # Cliente unificado Gmail API / IMAP
│   ├── classifier.py                     # Motor de reglas de clasificación
│   └── digest_builder.py                 # Generador de resúmenes (HTML/texto/Telegram)
├── tests/
│   └── test_classifier.py                # Tests unitarios con emails simulados
├── .env.example                          # Plantilla de configuración
├── .gitignore                            # Protege secretos y datos runtime
├── requirements.txt                      # Dependencias Python
└── README.md                             # Este archivo
```

---

## 🚀 Inicio Rápido

### 1. Clona y configura

```bash
git clone <tu-repo>
cd email-cleaner-bot

# Crear entorno virtual
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
```

### 2. Configura credenciales (elige una)

#### Opción A: IMAP + App Password (recomendado para empezar)

1. Activa **2FA** en tu cuenta Google
2. Genera un **App Password**: https://myaccount.google.com/apppasswords
3. Copia `.env.example` a `.env` y edita:

```bash
cp .env.example .env
# Edita .env con tu editor favorito
```

```env
USE_IMAP=true
EMAIL_USER=tu_correo@gmail.com
EMAIL_APP_PASSWORD=abcd efgh ijkl mnop  # 16 chars sin espacios
```

#### Opción B: Gmail API OAuth (para producción/CI)

1. Ve a [Google Cloud Console](https://console.cloud.google.com/)
2. Crea proyecto → Habilita **Gmail API**
3. Crea credenciales **OAuth 2.0 Client ID** (tipo "Aplicación de escritorio")
4. Descarga `credentials.json` a la raíz del proyecto
5. Ejecuta una vez localmente para generar `token.json`:

```bash
python -c "
from src.mail_client import MailClient
client = MailClient()
client.connect()  # Abrirá navegador para autorizar
print('Token guardado en token.json')
"
```

6. Para GitHub Actions, añade estos **Secrets** en el repo:
   - `GMAIL_CREDENTIALS_JSON` = contenido completo de `credentials.json`
   - `GMAIL_TOKEN_JSON` = contenido completo de `token.json`
   - `EMAIL_USER` = tu correo

### 3. Ejecuta localmente

```bash
python -m src.main
```

Deberías ver logs de conexión, clasificación y envío del resumen.

---

## ⚙️ Configuración Avanzada

### Reglas de clasificación (`config/rules.json`)

El archivo `rules.json` controla toda la lógica de detección:

```json
{
  "newsletter_senders": ["noreply@", "newsletter@", "marketing@"],
  "newsletter_keywords_subject": ["newsletter", "weekly digest", "promoción"],
  "newsletter_keywords_body": ["unsubscribe", "darse de baja", "view in browser"],
  "receipt_senders": ["billing@", "facturacion@", "noreply@paypal", "noreply@amazon"],
  "receipt_keywords_subject": ["receipt", "factura", "order confirmation"],
  "receipt_keywords_body": ["order number", "número de factura", "amount paid"],
  "unreplied_threshold_days": 3,
  "unreplied_exclude_senders": ["noreply@", "security@", "donotreply@"],
  "actions": {
    "newsletter": "label_and_archive",
    "receipt": "label_and_archive"
  }
}
```

**Puedes personalizar:**
- Añadir/quitar remitentes y palabras clave
- Cambiar umbral de días para "sin responder"
- Modificar acciones: `label_only` vs `label_and_archive`
- Ajustar formato del resumen (HTML/texto)

### Variables de entorno (`.env`)

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `USE_IMAP` | No | `true` para IMAP, `false` para Gmail API (default: false) |
| `EMAIL_USER` | Sí | Tu dirección de Gmail |
| `EMAIL_APP_PASSWORD` | Si IMAP | App Password de 16 caracteres |
| `GMAIL_CREDENTIALS_JSON` | Si API | JSON completo de credentials.json |
| `GMAIL_TOKEN_JSON` | Si API | JSON completo de token.json |
| `DAYS_THRESHOLD` | No | Días para considerar "sin responder" (default: 3) |
| `DIGEST_TIME` | No | Hora envío resumen UTC (default: 08:00) |
| `TELEGRAM_WEBHOOK` | No | Webhook Telegram para notificaciones |
| `LOG_LEVEL` | No | Nivel de log: DEBUG, INFO, WARNING, ERROR |

---

## 🤖 Automatización con GitHub Actions

El workflow `.github/workflows/daily_cleanup.yml` ejecuta el bot **diariamente a las 8:00 AM UTC**.

### Configuración en GitHub:

1. Ve a **Settings → Secrets and variables → Actions**
2. Añade los secrets necesarios (ver Opción B arriba)
3. El workflow se ejecutará automáticamente cada día
4. Puedes lanzarlo manualmente en la pestaña **Actions**

### Personalizar horario:

Edita el `cron` en `daily_cleanup.yml`:
```yaml
# Ejemplo: 8:00 AM hora de Madrid (UTC+1 en invierno, UTC+2 en verano)
# Usa UTC fijo o añade lógica de zona horaria en settings.py
- cron: '0 7 * * *'  # 8:00 CET (invierno)
```

---

## 🧪 Testing

```bash
# Ejecutar tests del clasificador
python -m pytest tests/ -v

# Con coverage
python -m pytest tests/ --cov=src --cov-report=html
```

Los tests usan emails simulados para verificar:
- ✅ Newsletters detectadas correctamente
- ✅ Recibos/facturas detectados correctamente
- ✅ Correos importantes **NO** clasificados como spam
- ✅ Alertas de seguridad excluidas
- ✅ Umbral de días para "sin responder" funciona

---

## 📬 Formato del Resumen

El bot envía un email con:
- **Estadísticas**: newsletters/recibos procesados
- **Lista de correos sin responder** (>3 días) con asunto, remitente y fecha
- **Formato dual**: HTML (bonito) + Texto plano (accesible)
- **Opcional**: También por Telegram si configuras webhook

---

## 🔒 Seguridad

- **Nunca** subas `.env`, `credentials.json`, `token.json` a Git
- El `.gitignore` ya protege estos archivos
- Usa **App Passwords** (no tu contraseña real) para IMAP
- En GitHub Actions, usa **Secrets** encriptados
- Los tokens OAuth se renuevan automáticamente

---

## 🐛 Troubleshooting

| Problema | Solución |
|----------|----------|
| `ModuleNotFoundError` | `pip install -r requirements.txt` |
| `InvalidCredentials` (IMAP) | Verifica App Password y 2FA activado |
| `Token expired` (API) | Borra `token.json` y re-ejecutar localmente |
| `Quota exceeded` | Gmail API tiene límites; usa IMAP para volúmenes altos |
| Emails importantes archivados | Ajusta `rules.json`: añade remitentes a `unreplied_exclude_senders` |
| No llega el resumen | Revisa carpeta Spam; verifica logs en `logs/` |

---

## 📄 Licencia

MIT License - Úsalo libremente para uso personal o comercial.

---

## 🤝 Contribuir

1. Fork del repo
2. Crea rama: `git checkout -b feature/nueva-funcionalidad`
3. Commit: `git commit -m 'Add: nueva funcionalidad'`
4. Push: `git push origin feature/nueva-funcionalidad`
5. Abre Pull Request

---

## 📞 Soporte

- **Issues**: [GitHub Issues](https://github.com/tu-usuario/email-cleaner-bot/issues)
- **Email**: tu-email@ejemplo.com

---

*Generado con ❤️ para mantener tu bandeja de entrada limpia y organizada*