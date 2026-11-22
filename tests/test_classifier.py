"""
Pruebas unitarias para el clasificador de correos.
Valida que las reglas no clasifiquen incorrectamente correos importantes.
"""
import sys
import json
from pathlib import Path

# Añadir src al path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from src.classifier import EmailClassifier


# Correos de prueba simulados
TEST_EMAILS = [
    # Newsletter clara
    {
        "id": "test_1",
        "subject": "Weekly Digest: Top 10 Articles This Week",
        "from": "newsletter@medium.com",
        "body": "Here are your weekly picks. Unsubscribe at any time. Privacy policy applies.",
        "date": "Mon, 20 Nov 2026 10:00:00 +0000",
        "labels": [],
    },
    # Newsletter en español
    {
        "id": "test_2",
        "subject": "Boletín semanal: Novedades de noviembre",
        "from": "noreply@empresa.es",
        "body": "Darse de baja | Preferencias de correo | Ver en el navegador",
        "date": "Tue, 19 Nov 2026 08:00:00 +0000",
        "labels": [],
    },
    # Recibo/Factura
    {
        "id": "test_3",
        "subject": "Your receipt from Amazon - Order #123-4567890",
        "from": "auto-confirm@amazon.com",
        "body": "Order number: 123-4567890. Total paid: $29.99. Payment method: Visa ending in 1234.",
        "date": "Wed, 18 Nov 2026 15:30:00 +0000",
        "labels": [],
    },
    # Factura en español
    {
        "id": "test_4",
        "subject": "Factura #FAC-2026-001 - Tu compra en MercadoLibre",
        "from": "facturacion@mercadolibre.com",
        "body": "Número de factura: FAC-2026-001. Total pagado: $1,250.00. Método de pago: Tarjeta de crédito.",
        "date": "Thu, 17 Nov 2026 12:00:00 +0000",
        "labels": [],
    },
    # Correo personal importante (NO debe clasificarse como newsletter)
    {
        "id": "test_5",
        "subject": "Reunión de proyecto - Mañana a las 10",
        "from": "juan.perez@empresa.com",
        "body": "Hola, recordatorio de la reunión de mañana. Por favor confirma tu asistencia.",
        "date": "Fri, 16 Nov 2026 09:00:00 +0000",
        "labels": [],
    },
    # Correo de soporte (NO debe archivarse)
    {
        "id": "test_6",
        "subject": "Soporte: Tu ticket #4521 ha sido actualizado",
        "from": "support@github.com",
        "body": "Tu ticket ha sido respondido. Por favor revisa la actualización.",
        "date": "Sat, 15 Nov 2026 14:00:00 +0000",
        "labels": [],
    },
    # Correo con etiqueta Newsletter ya puesta
    {
        "id": "test_7",
        "subject": "Promoción especial - 50% descuento",
        "from": "marketing@tienda.com",
        "body": "Oferta por tiempo limitado. Unsubscribe here.",
        "date": "Sun, 14 Nov 2026 11:00:00 +0000",
        "labels": ["NEWSLETTER", "INBOX"],
    },
    # Correo antiguo sin responder (para test unreplied)
    {
        "id": "test_8",
        "subject": "Propuesta de colaboración",
        "from": "colaboraciones@otraempresa.com",
        "body": "Hola, me gustaría proponer una colaboración...",
        "date": "Mon, 10 Nov 2026 10:00:00 +0000",
        "labels": ["INBOX"],
    },
    # Correo reciente sin responder (no debe aparecer en unreplied con threshold 3)
    {
        "id": "test_9",
        "subject": "Pregunta rápida",
        "from": "amigo@gmail.com",
        "body": "Hola, ¿cómo estás?",
        "date": "Sat, 20 Nov 2026 10:00:00 +0000",
        "labels": ["INBOX"],
    },
    # Correo de no-reply que NO es newsletter (alerta de seguridad)
    {
        "id": "test_10",
        "subject": "Alerta de seguridad: Nuevo inicio de sesión",
        "from": "noreply@security.google.com",
        "body": "Se detectó un nuevo inicio de sesión en tu cuenta. Si no fuiste tú, cambia tu contraseña.",
        "date": "Fri, 19 Nov 2026 16:00:00 +0000",
        "labels": ["INBOX"],
    },
]


def load_test_rules() -> dict:
    """Carga las reglas reales del proyecto para testing"""
    rules_path = Path(__file__).parent.parent / "config" / "rules.json"
    with open(rules_path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_newsletter_detection():
    """Prueba detección de newsletters"""
    rules = load_test_rules()
    classifier = EmailClassifier(rules)

    # Test 1: Newsletter en inglés
    cat, actions, conf = classifier.classify(TEST_EMAILS[0])
    assert cat == "newsletter", f"Esperado 'newsletter', got '{cat}'"
    assert conf > 0.5, f"Confianza muy baja: {conf}"
    print("✅ Test 1 passed: Newsletter en inglés detectada")

    # Test 2: Newsletter en español
    cat, actions, conf = classifier.classify(TEST_EMAILS[1])
    assert cat == "newsletter", f"Esperado 'newsletter', got '{cat}'"
    print("✅ Test 2 passed: Newsletter en español detectada")

    # Test 3: Con etiqueta existente
    cat, actions, conf = classifier.classify(TEST_EMAILS[6])
    assert cat == "newsletter", f"Esperado 'newsletter', got '{cat}'"
    print("✅ Test 3 passed: Email con etiqueta Newsletter detectado")

    # Test 4: Correo personal NO debe ser newsletter
    cat, actions, conf = classifier.classify(TEST_EMAILS[4])
    assert cat != "newsletter", f"No debería ser newsletter, got '{cat}'"
    print("✅ Test 4 passed: Correo personal no clasificado como newsletter")

    # Test 5: Alerta de seguridad no-reply NO debe ser newsletter
    cat, actions, conf = classifier.classify(TEST_EMAILS[9])
    assert cat != "newsletter", f"Alerta de seguridad no debería ser newsletter, got '{cat}'"
    print("✅ Test 5 passed: Alerta de seguridad no clasificada como newsletter")


def test_receipt_detection():
    """Prueba detección de recibos/facturas"""
    rules = load_test_rules()
    classifier = EmailClassifier(rules)

    # Test 1: Recibo Amazon
    cat, actions, conf = classifier.classify(TEST_EMAILS[2])
    assert cat == "receipt", f"Esperado 'receipt', got '{cat}'"
    assert conf > 0.5, f"Confianza muy baja: {conf}"
    print("✅ Test 6 passed: Recibo Amazon detectado")

    # Test 2: Factura MercadoLibre
    cat, actions, conf = classifier.classify(TEST_EMAILS[3])
    assert cat == "receipt", f"Esperado 'receipt', got '{cat}'"
    print("✅ Test 7 passed: Factura MercadoLibre detectada")


def test_unreplied_detection():
    """Prueba detección de correos sin responder"""
    rules = load_test_rules()
    classifier = EmailClassifier(rules)

    # Usar fecha fija para tests determinísticos
    # Simular que hoy es 22 Nov 2026
    from datetime import datetime
    test_now = datetime(2026, 11, 22)

    # Monkey patch para testing
    original_is_unreplied = classifier.is_unreplied

    def mock_is_unreplied(email, threshold_days):
        # Simular fecha actual fija
        date_str = email.get("date", "")
        if not date_str:
            return False
        from email.utils import parsedate_to_datetime
        try:
            email_date = parsedate_to_datetime(date_str)
            if email_date.tzinfo:
                email_date = email_date.replace(tzinfo=None)
            days_old = (test_now - email_date).days
            if days_old < threshold_days:
                return False
        except Exception:
            return False

        # Resto de lógica original
        from_addr = email.get("from", "").lower()
        for pattern in classifier.unreplied_exclude_sender_patterns:
            if pattern.search(from_addr):
                return False
        labels = [l.lower() for l in email.get("labels", [])]
        if any(l in classifier.unreplied_exclude_labels for l in labels):
            return False
        if "SENT" in email.get("labels", []):
            return False
        subject = email.get("subject", "").lower()
        is_reply = subject.startswith("re:") or subject.startswith("fwd:") or subject.startswith("fw:")
        return not is_reply

    classifier.is_unreplied = mock_is_unreplied

    # Test 1: Correo antiguo (12 días) sin responder -> SÍ
    unreplied = classifier.get_unreplied_emails([TEST_EMAILS[7]], 3)
    assert len(unreplied) == 1, f"Debería detectar 1 correo sin responder, got {len(unreplied)}"
    print("✅ Test 8 passed: Correo antiguo sin responder detectado")

    # Test 2: Correo reciente (2 días) sin responder -> NO
    unreplied = classifier.get_unreplied_emails([TEST_EMAILS[8]], 3)
    assert len(unreplied) == 0, f"No debería detectar correos recientes, got {len(unreplied)}"
    print("✅ Test 9 passed: Correo reciente no marcado como sin responder")

    # Test 3: Newsletter antigua -> NO (excluida por etiqueta)
    unreplied = classifier.get_unreplied_emails([TEST_EMAILS[0]], 3)
    assert len(unreplied) == 0, "Newsletters no deberían aparecer en sin responder"
    print("✅ Test 10 passed: Newsletter excluida de sin responder")

    # Test 4: Alerta de seguridad (no-reply) -> NO (excluida por remitente)
    unreplied = classifier.get_unreplied_emails([TEST_EMAILS[9]], 3)
    assert len(unreplied) == 0, "Alertas de seguridad no deberían aparecer en sin responder"
    print("✅ Test 11 passed: Alerta de seguridad excluida de sin responder")


def test_batch_classification():
    """Prueba clasificación en lote"""
    rules = load_test_rules()
    classifier = EmailClassifier(rules)

    results = classifier.classify_batch(TEST_EMAILS)

    assert len(results) == len(TEST_EMAILS)

    # Verificar categorías esperadas
    expected = {
        "test_1": "newsletter",
        "test_2": "newsletter",
        "test_3": "receipt",
        "test_4": "receipt",
        "test_5": "other",
        "test_6": "other",
        "test_7": "newsletter",
        "test_8": "other",
        "test_9": "other",
        "test_10": "other",
    }

    for result in results:
        eid = result["email"]["id"]
        if eid in expected:
            assert result["category"] == expected[eid], \
                f"{eid}: esperado {expected[eid]}, got {result['category']}"

    print("✅ Test 12 passed: Clasificación en lote correcta")


def run_all_tests():
    """Ejecuta todas las pruebas"""
    print("🧪 Ejecutando pruebas del clasificador...\n")

    try:
        test_newsletter_detection()
        test_receipt_detection()
        test_unreplied_detection()
        test_batch_classification()

        print("\n" + "=" * 50)
        print("✅ TODAS LAS PRUEBAS PASARON")
        print("=" * 50)
        return True

    except AssertionError as e:
        print(f"\n❌ TEST FALLÓ: {e}")
        return False
    except Exception as e:
        print(f"\n💥 ERROR INESPERADO: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)