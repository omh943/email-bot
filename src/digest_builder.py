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

    def build_digest(
        self,
        unreplied_emails: List[Dict],
        stats: Dict[str, int],
        threshold_days: int
    ) -> Dict[str, Any]:
        """
        Construye el resumen completo.
        Retorna dict con subject, text_body, html_body.
        """
        date_str = datetime.now().strftime("%d/%m/%Y")
        subject = f"📧 Resumen diario de correo - {date_str}"

        # Construir partes
        text_parts = []
        html_parts = []

        # Encabezado
        text_parts.append(self._build_text_header(date_str, stats, threshold_stats))
        html_parts.append(self._build_html_header(date_str, stats, threshold_stats))

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
            "unreplied_count": len(unreplied_emails)
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
        if stats.get('errors', 0):
            lines.append(f"  ⚠️ Errores:                    {stats.get('errors', 0)}")
        return "\n".join(lines)

    def _build_html_stats(self, stats: Dict) -> str:
        items = [
            ("📰 Newsletters etiquetadas", stats.get('newsletters_labeled', 0)),
            ("📦 Newsletters archivadas", stats.get('newsletters_archived', 0)),
            ("🧾 Recibos etiquetados", stats.get('receipts_labeled', 0)),
            ("📁 Recibos archivados", stats.get('receipts_archived', 0)),
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

        rows = ""
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

            rows += f"""
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #f0f0f0; width: 40px; text-align: center; color: #888;">{i}</td>
                <td style="padding: 12px; border-bottom: 1px solid #f0f0f0;">
                    <div style="font-weight: 600; color: #333; margin-bottom: 4px;">{subject}</div>
                    <div style="font-size: 13px; color: #666;">De: {from_addr}</div>
                    <div style="font-size: 12px; color: #999;">{date_fmt}</div>
                </td>
            </tr>"""

        more = ""
        if len(emails) > self.max_unreplied:
            more = f"<p style='color: #888; font-size: 13px; margin-top: 12px;'>... y {len(emails) - self.max_unreplied} correos más.</p>"

        return f"""
        <h2 style="color: #333; border-bottom: 2px solid #f59e0b; padding-bottom: 8px;">⏳ Correos Sin Responder (> {threshold_days} días)</h2>
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 12px;">
            {rows}
        </table>
        {more}
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
        unreplied_count = digest["unreplied_count"]

        lines = [
            f"📧 *Resumen Diario - {datetime.now().strftime('%d/%m/%Y')}*",
            "",
            "📊 *Estadísticas:*",
            f"  📰 Newsletters: {stats.get('newsletters_labeled', 0)} etiquetadas, {stats.get('newsletters_archived', 0)} archivadas",
            f"  🧾 Recibos: {stats.get('receipts_labeled', 0)} etiquetados, {stats.get('receipts_archived', 0)} archivados",
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

        return "\n".join(lines)