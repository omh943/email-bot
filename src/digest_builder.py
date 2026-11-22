"""
Constructor de resúmenes (digest) de correos.
Genera versiones HTML y texto plano para envío por correo o Telegram.
"""
import logging
from datetime import datetime
from typing import Dict, Any, List
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)


class DigestBuilder:
    """Construye resúmenes formateados de la actividad del bot"""

    def __init__(self, rules: Dict[str, Any]):
        self.rules = rules
        self.digest_config = rules.get("digest", {})
        self.max_unreplied = self.digest_config.get("max_unreplied_shown", 20)
        self.format = self.digest_config.get("format", "html")

    @staticmethod
    def _gmail_link(email_id: str, thread_id: str = None) -> str:
        """Genera link directo al correo en Gmail Web usando búsqueda por Message-ID"""
        # Formato correcto que SIEMPRE funciona: búsqueda por rfc822msgid
        # https://mail.google.com/mail/u/0/#search/rfc822msgid:{message_id}
        # El message_id suele venir con < >, hay que limpiarlo
        clean_id = email_id.strip("<>")
        return f"https://mail.google.com/mail/u/0/#search/rfc822msgid:{clean_id}"

    def _email_row_html(self, i: int, email: Dict, show_gmail_link: bool = True) -> str:
        """Genera fila HTML para un correo con link a Gmail"""
        subject = email.get("subject", "(sin asunto)")
        from_addr = email.get("from", "(desconocido)")
        date_str = email.get("date", "")
        email_id = email.get("id", "")
        thread_id = email.get("thread_id", "")

        try:
            if date_str:
                dt = parsedate_to_datetime(date_str)
                date_fmt = dt.strftime("%d/%m/%Y %H:%M")
            else:
                date_fmt = "Fecha desconocida"
        except Exception:
            date_fmt = "Fecha inválida"

        gmail_url = self._gmail_link(email_id, thread_id) if show_gmail_link else "#"
        link_attr = f'href="{gmail_url}" target="_blank" style="color: #0066cc; text-decoration: none;"' if show_gmail_link else 'style="color: #333;"'

        return f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #f0f0f0; width: 40px; text-align: center; color: #888;">{i}</td>
            <td style="padding: 12px; border-bottom: 1px solid #f0f0f0;">
                <div style="font-weight: 600; color: #333; margin-bottom: 4px;">
                    <a {link_attr}>{subject}</a>
                </div>
                <div style="font-size: 13px; color: #666;">De: {from_addr}</div>
                <div style="font-size: 12px; color: #999;">{date_fmt}</div>
            </td>
        </tr>"""

    def build_digest(
        self,
        unreplied_emails: List[Dict],
        stats: Dict[str, int],
        threshold_days: int,
        pending_review_emails: List[Dict] = None,
        ual_emails: List[Dict] = None
    ) -> Dict[str, Any]:
        """
        Construye el resumen completo.
        Retorna dict con subject, text_body, html_body.
        """
        if pending_review_emails is None:
            pending_review_emails = []
        if ual_emails is None:
            ual_emails = []
            
        date_str = datetime.now().strftime("%d/%m/%Y")
        subject = f"📧 Resumen diario de correo - {date_str}"

        # Construir partes
        text_parts = []
        html_parts = []

        # Encabezado
        text_parts.append(self._build_text_header(date_str, stats, threshold_days))
        html_parts.append(self._build_html_header(date_str, stats, threshold_days))

        # Estadísticas de limpieza
        text_parts.append(self._build_text_stats(stats))
        html_parts.append(self._build_html_stats(stats))

        # Correos sin responder
        if unreplied_emails:
            text_parts.append(self._build_text_unreplied(unreplied_emails, threshold_days))
            html_parts.append(self._build_html_unreplied(unreplied_emails, threshold_days))
        else:
            text_parts.append("\n✅ No hay correos sin responder pendientes.")
            html_parts.append("<p style='color: green;'>✅ No hay correos sin responder pendientes.</p>")

        # Correos pendientes de revisión
        max_pending = self.digest_config.get("max_pending_review_shown", 30)
        if pending_review_emails:
            text_parts.append(self._build_text_pending_review(pending_review_emails, max_pending))
            html_parts.append(self._build_html_pending_review(pending_review_emails, max_pending))
        elif self.digest_config.get("include_pending_review_list", True):
            text_parts.append("\n✅ No hay correos pendientes de revisión.")
            html_parts.append("<p style='color: green;'>✅ No hay correos pendientes de revisión.</p>")

        # Correos UAL/ESI (con secciones colapsables)
        max_ual = self.digest_config.get("max_ual_shown", 50)
        if ual_emails:
            text_parts.append(self._build_text_ual(ual_emails, max_ual))
            html_parts.append(self._build_html_ual(ual_emails, max_ual))
        elif self.digest_config.get("include_ual_list", True):
            text_parts.append("\n✅ No hay correos UAL/ESI.")
            html_parts.append("<p style='color: green;'>✅ No hay correos UAL/ESI.</p>")

        # Pie
        text_parts.append(self._build_text_footer())
        html_parts.append(self._build_html_footer())

        text_body = "\n".join(text_parts)
        html_body = "\n".join(html_parts)

        return {
            "subject": subject,
            "text_body": text_body,
            "html_body": html_body,
            "generated_at": datetime.now().isoformat(),
            "stats": stats,
            "unreplied_count": len(unreplied_emails),
            "pending_review_count": len(pending_review_emails),
            "ual_count": len(ual_emails),
            "ual_emails": ual_emails,
            "pending_review_emails": pending_review_emails
        }

    def _build_text_header(self, date_str: str, stats: Dict, threshold_days: int) -> str:
        return f"""
╔══════════════════════════════════════════════════════════╗
║           📧 RESUMEN DIARIO DE LIMPIEZA DE CORREO        ║
║                         {date_str}                          ║
╚══════════════════════════════════════════════════════════╝
"""

    def _build_html_header(self, date_str: str, stats: Dict, threshold_days: int) -> str:
        return f"""
<div style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
    <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 24px; text-align: center;">
        <h1 style="margin: 0; font-size: 24px;">📧 Resumen Diario de Limpieza</h1>
        <p style="margin: 8px 0 0; opacity: 0.9;">{date_str}</p>
    </div>
    <div style="padding: 24px;">
"""

    def _build_text_stats(self, stats: Dict) -> str:
        lines = ["\n📊 ESTADÍSTICAS DE LIMPIEZA", "─" * 40]
        lines.append(f"  📰 Newsletters etiquetadas:    {stats.get('newsletters_labeled', 0)}")
        lines.append(f"  📦 Newsletters archivadas:     {stats.get('newsletters_archived', 0)}")
        lines.append(f"  🧾 Recibos etiquetados:        {stats.get('receipts_labeled', 0)}")
        lines.append(f"  📁 Recibos archivados:         {stats.get('receipts_archived', 0)}")
        lines.append(f"  ⏳ Pendientes de revisión:       {stats.get('pending_review', 0)}")
        lines.append(f"  🛡️ Protegidos (no tocados):      {stats.get('protected_skipped', 0)}")
        if stats.get('errors', 0):
            lines.append(f"  ⚠️ Errores:                    {stats.get('errors', 0)}")
        return "\n".join(lines)

    def _build_html_stats(self, stats: Dict) -> str:
        items = [
            ("📰 Newsletters etiquetadas", stats.get('newsletters_labeled', 0)),
            ("📦 Newsletters archivadas", stats.get('newsletters_archived', 0)),
            ("🧾 Recibos etiquetados", stats.get('receipts_labeled', 0)),
            ("📁 Recibos archivados", stats.get('receipts_archived', 0)),
            ("⏳ Pendientes de revisión", stats.get('pending_review', 0)),
            ("🛡️ Protegidos (no tocados)", stats.get('protected_skipped', 0)),
        ]
        if stats.get('errors', 0):
            items.append(("⚠️ Errores", stats.get('errors', 0)))

        rows = ""
        for label, value in items:
            rows += f"""
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #f0f0f0;">{label}</td>
                <td style="padding: 10px; border-bottom: 1px solid #f0f0f0; text-align: right; font-weight: bold;">{value}</td>
            </tr>"""

        return f"""
        <h2 style="color: #333; border-bottom: 2px solid #667eea; padding-bottom: 8px;">📊 Estadísticas de Limpieza</h2>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
            {rows}
        </table>
"""

    def _build_text_unreplied(self, emails: List[Dict], threshold_days: int) -> str:
        lines = [f"\n⏳ CORREOS SIN RESPONDER (> {threshold_days} días)", "─" * 40]
        shown = emails[:self.max_unreplied]

        for i, email in enumerate(shown, 1):
            subject = email.get("subject", "(sin asunto)")
            from_addr = email.get("from", "(desconocido)")
            date_str = email.get("date", "")

            # Formatear fecha
            try:
                if date_str:
                    dt = parsedate_to_datetime(date_str)
                    date_fmt = dt.strftime("%d/%m/%Y %H:%M")
                else:
                    date_fmt = "Fecha desconocida"
            except Exception:
                date_fmt = "Fecha inválida"

            # Truncar remitente
            if len(from_addr) > 50:
                from_addr = from_addr[:47] + "..."

            lines.append(f"\n{i}. {subject}")
            lines.append(f"   De: {from_addr}")
            lines.append(f"   Fecha: {date_fmt}")

        if len(emails) > self.max_unreplied:
            lines.append(f"\n... y {len(emails) - self.max_unreplied} correos más.")

        return "\n".join(lines)

    def _build_html_unreplied(self, emails: List[Dict], threshold_days: int) -> str:
        shown = emails[:self.max_unreplied]
        rows = "".join(self._email_row_html(i, email) for i, email in enumerate(shown, 1))

        more = ""
        if len(emails) > self.max_unreplied:
            more = f"<p style='color: #888; font-size: 13px; margin-top: 12px;'>... y {len(emails) - self.max_unreplied} correos más.</p>"

        return f"""
        <h2 style="color: #333; border-bottom: 2px solid #f59e0b; padding-bottom: 8px;">⏳ Correos Sin Responder (> {threshold_days} días)</h2>
        <p style="font-size: 12px; color: #888; margin-bottom: 8px;">💡 Click en el asunto para abrir en Gmail</p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 12px;">
            {rows}
        </table>
        {more}
"""

    def _build_text_pending_review(self, emails: List[Dict], max_shown: int) -> str:
        lines = [f"\n⏳ CORREOS PENDIENTES DE REVISIÓN (Newsletters/Recibos)", "─" * 40]
        shown = emails[:max_shown]

        for i, email in enumerate(shown, 1):
            subject = email.get("subject", "(sin asunto)")
            from_addr = email.get("from", "(desconocido)")
            date_str = email.get("date", "")

            try:
                if date_str:
                    dt = parsedate_to_datetime(date_str)
                    date_fmt = dt.strftime("%d/%m/%Y %H:%M")
                else:
                    date_fmt = "Fecha desconocida"
            except Exception:
                date_fmt = "Fecha inválida"

            if len(from_addr) > 50:
                from_addr = from_addr[:47] + "..."

            lines.append(f"\n{i}. {subject}")
            lines.append(f"   De: {from_addr}")
            lines.append(f"   Fecha: {date_fmt}")

        if len(emails) > max_shown:
            lines.append(f"\n... y {len(emails) - max_shown} correos más.")

        return "\n".join(lines)

    def _build_html_pending_review(self, emails: List[Dict], max_shown: int) -> str:
        shown = emails[:max_shown]
        rows = "".join(self._email_row_html(i, email) for i, email in enumerate(shown, 1))

        more = ""
        if len(emails) > max_shown:
            more = f"<p style='color: #888; font-size: 13px; margin-top: 12px;'>... y {len(emails) - max_shown} correos más.</p>"

        return f"""
        <h2 style="color: #333; border-bottom: 2px solid #8b5cf6; padding-bottom: 8px;">⏳ Pendientes de Revisión (Newsletters/Recibos)</h2>
        <p style="font-size: 12px; color: #888; margin-bottom: 8px;">💡 Click en el asunto para abrir en Gmail</p>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 12px;">
            {rows}
        </table>
        {more}
"""

    def _build_text_ual(self, emails: List[Dict], max_shown: int) -> str:
        # Separar UAL general y ESI
        esi_emails = []
        ual_general = []
        for email in emails:
            subject = email.get("subject", "").lower()
            body = email.get("body", "").lower()
            from_addr = email.get("from", "").lower()
            is_esi = any(kw in subject or kw in body or kw in from_addr 
                        for kw in ["esi", "escuela superior de ingeniería", "escuela de ingeniería"])
            if is_esi:
                esi_emails.append(email)
            else:
                ual_general.append(email)
        
        lines = [f"\n🏫 CORREOS UAL / ESI (ordenados por recientes)", "─" * 40]
        
        if ual_general:
            lines.append(f"\n📍 UAL General ({len(ual_general)}):")
            shown = ual_general[:max_shown]
            for i, email in enumerate(shown, 1):
                subject = email.get("subject", "(sin asunto)")
                from_addr = email.get("from", "(desconocido)")
                date_str = email.get("date", "")
                try:
                    if date_str:
                        dt = parsedate_to_datetime(date_str)
                        date_fmt = dt.strftime("%d/%m/%Y %H:%M")
                    else:
                        date_fmt = "Fecha desconocida"
                except Exception:
                    date_fmt = "Fecha inválida"
                if len(from_addr) > 50:
                    from_addr = from_addr[:47] + "..."
                lines.append(f"  {i}. {subject}")
                lines.append(f"     De: {from_addr} | {date_fmt}")
            if len(ual_general) > max_shown:
                lines.append(f"  ... y {len(ual_general) - max_shown} más")
        
        if esi_emails:
            lines.append(f"\n🏗️ ESI - Escuela Superior de Ingeniería ({len(esi_emails)}):")
            shown = esi_emails[:max_shown]
            for i, email in enumerate(shown, 1):
                subject = email.get("subject", "(sin asunto)")
                from_addr = email.get("from", "(desconocido)")
                date_str = email.get("date", "")
                try:
                    if date_str:
                        dt = parsedate_to_datetime(date_str)
                        date_fmt = dt.strftime("%d/%m/%Y %H:%M")
                    else:
                        date_fmt = "Fecha desconocida"
                except Exception:
                    date_fmt = "Fecha inválida"
                if len(from_addr) > 50:
                    from_addr = from_addr[:47] + "..."
                lines.append(f"  {i}. {subject}")
                lines.append(f"     De: {from_addr} | {date_fmt}")
            if len(esi_emails) > max_shown:
                lines.append(f"  ... y {len(esi_emails) - max_shown} más")
        
        return "\n".join(lines)

    def _build_html_ual(self, emails: List[Dict], max_shown: int) -> str:
        # Separar UAL general y ESI
        esi_emails = []
        ual_general = []
        for email in emails:
            subject = email.get("subject", "").lower()
            body = email.get("body", "").lower()
            from_addr = email.get("from", "").lower()
            is_esi = any(kw in subject or kw in body or kw in from_addr 
                        for kw in ["esi", "escuela superior de ingeniería", "escuela de ingeniería"])
            if is_esi:
                esi_emails.append(email)
            else:
                ual_general.append(email)
        
        html_parts = []
        
        # UAL General (colapsable)
        if ual_general:
            shown = ual_general[:max_shown]
            rows = "".join(self._email_row_html(i, email) for i, email in enumerate(shown, 1))

            more = ""
            if len(ual_general) > max_shown:
                more = f"<p style='color: #888; font-size: 12px; margin-top: 8px;'>... y {len(ual_general) - max_shown} más.</p>"
            
            html_parts.append(f"""
            <details style="margin-bottom: 16px; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
                <summary style="background: linear-gradient(135deg, #0066cc 0%, #004499 100%); color: white; padding: 14px 16px; cursor: pointer; font-weight: 600; font-size: 15px;">
                    📍 UAL General ({len(ual_general)} correos) ▼
                </summary>
                <div style="padding: 8px;">
                    <p style="font-size: 12px; color: #888; margin: 0 0 8px 0;">💡 Click en el asunto para abrir en Gmail</p>
                    <table style="width: 100%; border-collapse: collapse; margin: 0;">
                        {rows}
                    </table>
                    {more}
                </div>
            </details>""")
        
        # ESI (colapsable)
        if esi_emails:
            shown = esi_emails[:max_shown]
            rows = "".join(self._email_row_html(i, email) for i, email in enumerate(shown, 1))

            more = ""
            if len(esi_emails) > max_shown:
                more = f"<p style='color: #888; font-size: 12px; margin-top: 8px;'>... y {len(esi_emails) - max_shown} más.</p>"
            
            html_parts.append(f"""
            <details style="margin-bottom: 16px; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
                <summary style="background: linear-gradient(135deg, #009933 0%, #006622 100%); color: white; padding: 14px 16px; cursor: pointer; font-weight: 600; font-size: 15px;">
                    🏗️ ESI - Escuela Superior de Ingeniería ({len(esi_emails)} correos) ▼
                </summary>
                <div style="padding: 8px;">
                    <p style="font-size: 12px; color: #888; margin: 0 0 8px 0;">💡 Click en el asunto para abrir en Gmail</p>
                    <table style="width: 100%; border-collapse: collapse; margin: 0;">
                        {rows}
                    </table>
                    {more}
                </div>
            </details>""")
        
        if not html_parts:
            return "<p style='color: green;'>✅ No hay correos UAL/ESI.</p>"
        
        return f"""
        <h2 style="color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 8px;">🏫 UAL / ESI (click para expandir, click en asunto para abrir en Gmail)</h2>
        {''.join(html_parts)}
"""

    def _build_text_footer(self) -> str:
        return """
═══════════════════════════════════════════════════════════
Generado automáticamente por Email Cleaner Bot
https://github.com/tu-usuario/email-cleaner-bot
"""

    def _build_html_footer(self) -> str:
        return """
    <div style="margin-top: 32px; padding-top: 16px; border-top: 1px solid #e0e0e0; text-align: center; color: #888; font-size: 12px;">
        <p>Generado automáticamente por <strong>Email Cleaner Bot</strong></p>
    </div>
</div>
</div>
"""

    def build_telegram_message(self, digest: Dict) -> str:
        """Construye mensaje optimizado para Telegram (Markdown)"""
        stats = digest["stats"]
        unreplied_count = digest.get("unreplied_count", 0)
        pending_count = digest.get("pending_review_count", 0)
        ual_count = digest.get("ual_count", 0)

        lines = [
            f"📧 *Resumen Diario - {datetime.now().strftime('%d/%m/%Y')}*",
            "",
            "📊 *Estadísticas:*",
            f"  📰 Newsletters: {stats.get('newsletters_labeled', 0)} etiquetadas, {stats.get('newsletters_archived', 0)} archivadas",
            f"  🧾 Recibos: {stats.get('receipts_labeled', 0)} etiquetados, {stats.get('receipts_archived', 0)} archivados",
            f"  🏫 UAL/ESI: {stats.get('ual_labeled', 0)} UAL, {stats.get('esi_labeled', 0)} ESI",
            f"  ⏳ Pendientes revisión: {stats.get('pending_review', 0)}",
            f"  🛡️ Protegidos: {stats.get('protected_skipped', 0)}",
            f"  🗑️ Duplicados eliminados: {stats.get('duplicates_deleted', 0)}",
        ]

        if stats.get('errors', 0):
            lines.append(f"  ⚠️ Errores: {stats.get('errors', 0)}")

        lines.append("")
        if unreplied_count > 0:
            lines.append(f"⏳ *{unreplied_count} correos sin responder (>3 días):*")
            for i, email in enumerate(digest.get("unreplied_emails", [])[:5], 1):
                subject = email.get("subject", "(sin asunto)")[:60]
                lines.append(f"  {i}. {subject}")
            if unreplied_count > 5:
                lines.append(f"  ... y {unreplied_count - 5} más")
        else:
            lines.append("✅ *No hay correos sin responder pendientes*")

        if pending_count > 0:
            lines.append("")
            lines.append(f"📋 *{pending_count} pendientes de revisión:*")
            for i, email in enumerate(digest.get("pending_review_emails", [])[:5], 1):
                subject = email.get("subject", "(sin asunto)")[:60]
                lines.append(f"  {i}. {subject}")
            if pending_count > 5:
                lines.append(f"  ... y {pending_count - 5} más")

        if ual_count > 0:
            lines.append("")
            lines.append(f"🏫 *{ual_count} correos UAL/ESI:*")
            # Separar UAL y ESI
            ual_emails = digest.get("ual_emails", [])
            esi_count = sum(1 for e in ual_emails 
                           if any(kw in e.get("subject", "").lower() or kw in e.get("body", "").lower() 
                                 for kw in ["esi", "escuela superior de ingeniería", "escuela de ingeniería"]))
            ual_general_count = ual_count - esi_count
            lines.append(f"  📍 UAL General: {ual_general_count}")
            lines.append(f"  🏗️ ESI: {esi_count}")
            for i, email in enumerate(ual_emails[:5], 1):
                subject = email.get("subject", "(sin asunto)")[:60]
                lines.append(f"  {i}. {subject}")
            if ual_count > 5:
                lines.append(f"  ... y {ual_count - 5} más")

        return "\n".join(lines)