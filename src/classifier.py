"""
Motor de clasificación de correos.
Aplica reglas para identificar newsletters, recibos y correos sin responder.
"""
import re
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Set
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)


class EmailClassifier:
    """Clasificador de correos basado en reglas configurables"""

    def __init__(self, rules: Dict[str, Any]):
        self.rules = rules
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compila patrones regex para mejor rendimiento"""
        # Patrones de remitentes (dominios/prefijos)
        self.newsletter_sender_patterns = [
            re.compile(re.escape(s).replace(r"\*", ".*") + r".*", re.IGNORECASE)
            for s in self.rules.get("newsletter_senders", [])
        ]
        self.receipt_sender_patterns = [
            re.compile(re.escape(s).replace(r"\*", ".*") + r".*", re.IGNORECASE)
            for s in self.rules.get("receipt_senders", [])
        ]
        self.unreplied_exclude_sender_patterns = [
            re.compile(re.escape(s).replace(r"\*", ".*") + r".*", re.IGNORECASE)
            for s in self.rules.get("unreplied_exclude_senders", [])
        ]
        self.protected_sender_patterns = [
            re.compile(re.escape(s).replace(r"\*", ".*") + r".*", re.IGNORECASE)
            for s in self.rules.get("protected_senders", [])
        ]

        # Palabras clave en asunto
        self.newsletter_subject_keywords = [
            k.lower() for k in self.rules.get("newsletter_keywords_subject", [])
        ]
        self.receipt_subject_keywords = [
            k.lower() for k in self.rules.get("receipt_keywords_subject", [])
        ]
        self.protected_subject_keywords = [
            k.lower() for k in self.rules.get("protected_keywords_subject", [])
        ]

        # Palabras clave en cuerpo
        self.newsletter_body_keywords = [
            k.lower() for k in self.rules.get("newsletter_keywords_body", [])
        ]
        self.receipt_body_keywords = [
            k.lower() for k in self.rules.get("receipt_keywords_body", [])
        ]
        self.protected_body_keywords = [
            k.lower() for k in self.rules.get("protected_keywords_body", [])
        ]

        # Etiquetas conocidas
        self.newsletter_labels = set(l.lower() for l in self.rules.get("newsletter_labels", []))
        self.receipt_labels = set(l.lower() for l in self.rules.get("receipt_labels", []))
        self.unreplied_exclude_labels = set(
            l.lower() for l in self.rules.get("unreplied_exclude_labels", [])
        )
        
        # Configuración de confirmación
        self.confirmation_mode = self.rules.get("confirmation_mode", False)

    def classify_batch(self, emails: List[Dict]) -> List[Dict]:
        """Clasifica un lote de correos"""
        results = []
        for email in emails:
            category, actions, confidence = self.classify(email)
            results.append({
                "email": email,
                "category": category,
                "actions": actions,
                "confidence": confidence,
                "matched_rules": self._get_matched_rules(email, category)
            })
        return results

    def classify(self, email: Dict) -> tuple:
        """
        Clasifica un correo individual.
        Retorna: (categoría, acciones, confianza)
        Categorías: 'newsletter', 'receipt', 'unreplied', 'other'
        """
        subject = email.get("subject", "").lower()
        from_addr = email.get("from", "").lower()
        body = email.get("body", "").lower()
        labels = [l.lower() for l in email.get("labels", [])]

        # Verificar si está protegido (no archivar nunca)
        if self._is_protected(subject, from_addr, body):
            logger.debug(f"Correo protegido detectado: {email.get('subject', '')[:50]}")
            return "protected", [], 0.0

        # Verificar etiquetas existentes primero (más confiable)
        if any(l in self.newsletter_labels for l in labels):
            actions = self._get_actions_for_category("newsletter")
            return "newsletter", actions, 0.95

        if any(l in self.receipt_labels for l in labels):
            actions = self._get_actions_for_category("receipt")
            return "receipt", actions, 0.95

        # Puntuación para newsletter
        newsletter_score = self._score_newsletter(subject, from_addr, body)
        # Puntuación para recibo
        receipt_score = self._score_receipt(subject, from_addr, body)

        # Determinar categoría ganadora
        if newsletter_score > receipt_score and newsletter_score >= 0.5:
            actions = self._get_actions_for_category("newsletter")
            return "newsletter", actions, newsletter_score

        if receipt_score > newsletter_score and receipt_score >= 0.5:
            actions = self._get_actions_for_category("receipt")
            return "receipt", actions, receipt_score

        return "other", [], 0.0

    def _is_protected(self, subject: str, from_addr: str, body: str) -> bool:
        """Verifica si el correo tiene contenido protegido que no debe archivarse"""
        # Verificar remitentes protegidos
        for pattern in self.protected_sender_patterns:
            if pattern.search(from_addr):
                return True

        # Verificar palabras clave protegidas en asunto
        for kw in self.protected_subject_keywords:
            if kw in subject:
                return True

        # Verificar palabras clave protegidas en cuerpo
        for kw in self.protected_body_keywords:
            if kw in body:
                return True

        return False

    def _get_actions_for_category(self, category: str) -> List[str]:
        """Obtiene acciones según la configuración y modo confirmación"""
        action_config = self.rules.get("actions", {}).get(category, "label_and_archive")
        
        if category == "newsletter":
            base_action = "label_newsletter"
        elif category == "receipt":
            base_action = "label_receipt"
        else:
            return []

        if self.confirmation_mode:
            # En modo confirmación: solo etiquetar para revisión manual
            return [base_action, "label_pending_review"]
        elif action_config == "label_and_archive":
            return [base_action, "archive"]
        else:
            return [base_action]

    def _score_newsletter(self, subject: str, from_addr: str, body: str) -> float:
        """Calcula puntuación de probabilidad de ser newsletter"""
        score = 0.0
        max_score = 0.0

        # Remitente (peso alto)
        max_score += 0.4
        for pattern in self.newsletter_sender_patterns:
            if pattern.search(from_addr):
                score += 0.4
                break

        # Asunto (peso medio)
        max_score += 0.3
        for kw in self.newsletter_subject_keywords:
            if kw in subject:
                score += 0.3
                break

        # Cuerpo (peso medio-bajo)
        max_score += 0.2
        for kw in self.newsletter_body_keywords:
            if kw in body:
                score += 0.2
                break

        # Etiquetas Gmail existentes
        max_score += 0.1
        # Ya verificado arriba, pero por completitud

        return score / max_score if max_score > 0 else 0.0

    def _score_receipt(self, subject: str, from_addr: str, body: str) -> float:
        """Calcula puntuación de probabilidad de ser recibo/factura"""
        score = 0.0
        max_score = 0.0

        # Remitente (peso alto)
        max_score += 0.4
        for pattern in self.receipt_sender_patterns:
            if pattern.search(from_addr):
                score += 0.4
                break

        # Asunto (peso alto)
        max_score += 0.35
        for kw in self.receipt_subject_keywords:
            if kw in subject:
                score += 0.35
                break

        # Cuerpo (peso medio)
        max_score += 0.25
        matches = sum(1 for kw in self.receipt_body_keywords if kw in body)
        if matches > 0:
            score += min(0.25, matches * 0.08)

        return score / max_score if max_score > 0 else 0.0

    def _get_matched_rules(self, email: Dict, category: str) -> List[str]:
        """Obtiene lista de reglas que coincidieron para debugging"""
        matched = []
        subject = email.get("subject", "").lower()
        from_addr = email.get("from", "").lower()
        body = email.get("body", "").lower()

        if category == "newsletter":
            for pattern in self.newsletter_sender_patterns:
                if pattern.search(from_addr):
                    matched.append(f"sender:{pattern.pattern}")
            for kw in self.newsletter_subject_keywords:
                if kw in subject:
                    matched.append(f"subject:{kw}")
            for kw in self.newsletter_body_keywords:
                if kw in body:
                    matched.append(f"body:{kw}")

        elif category == "receipt":
            for pattern in self.receipt_sender_patterns:
                if pattern.search(from_addr):
                    matched.append(f"sender:{pattern.pattern}")
            for kw in self.receipt_subject_keywords:
                if kw in subject:
                    matched.append(f"subject:{kw}")
            for kw in self.receipt_body_keywords:
                if kw in body:
                    matched.append(f"body:{kw}")

        return matched

    def is_unreplied(self, email: Dict, threshold_days: int) -> bool:
        """
        Determina si un correo requiere respuesta y lleva sin responder
        más de threshold_days.
        """
        # Excluir por remitente
        from_addr = email.get("from", "").lower()
        for pattern in self.unreplied_exclude_sender_patterns:
            if pattern.search(from_addr):
                return False

        # Excluir por etiquetas
        labels = [l.lower() for l in email.get("labels", [])]
        if any(l in self.unreplied_exclude_labels for l in labels):
            return False

        # Verificar edad del correo
        try:
            date_str = email.get("date", "")
            if date_str:
                email_date = parsedate_to_datetime(date_str)
                # Hacer timezone-naive para comparación
                if email_date.tzinfo:
                    email_date = email_date.replace(tzinfo=None)
                now = datetime.now()
                days_old = (now - email_date).days
                if days_old < threshold_days:
                    return False
        except Exception as e:
            logger.warning(f"Error parseando fecha de correo {email.get('id')}: {e}")
            return False

        # Verificar si es un correo saliente (enviado por nosotros)
        # En Gmail API, los correos enviados tienen label "SENT"
        if "SENT" in email.get("labels", []):
            return False

        # Heurística: si el correo no tiene "Re:" o "Fwd:" en asunto
        # y no somos el remitente, podría requerir respuesta
        subject = email.get("subject", "").lower()
        is_reply = subject.startswith("re:") or subject.startswith("fwd:") or subject.startswith("fw:")

        # Si es una respuesta nuestra, no cuenta
        # Nota: esto es una heurística simple; en producción se podría
        # verificar el thread completo
        return not is_reply

    def get_unreplied_emails(self, emails: List[Dict], threshold_days: int) -> List[Dict]:
        """Filtra correos sin responder de una lista"""
        return [e for e in emails if self.is_unreplied(e, threshold_days)]