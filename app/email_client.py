"""
Email notification via SMTP.

Sends a plain-text + HTML multipart email using smtplib (stdlib, no extra
dependencies). Supports Gmail and any standard SMTP server with STARTTLS on
port 587.

Security notes:
  - SMTP credentials are read from environment variables and are never
    included in exception messages or log output.
  - smtplib.SMTP is constructed with timeout=10 to prevent indefinite hangs
    on unresponsive SMTP servers.
  - All user-supplied alert fields are HTML-escaped in the HTML body to
    prevent content injection via crafted alert payloads.
"""

import html
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from .alert_parser import NormalizedAlert

logger = logging.getLogger(__name__)

_SEVERITY_EMOJI = {
    "critical": "🔴",
    "warning":  "🟡",
    "info":     "🟢",
    "unknown":  "⚪",
}


def _build_subject(alert: NormalizedAlert) -> str:
    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    return f"{emoji} [{alert.severity.upper()}] {alert.service_name} — {alert.status.upper()}"


def _build_plain(alert: NormalizedAlert, ai: dict[str, Any]) -> str:
    insight = ai.get("insight", "No insight available.")
    if not isinstance(insight, str):
        insight = str(insight)

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []

    lines = [
        f"[{alert.severity.upper()}] {alert.service_name} — {alert.status.upper()}",
        f"Source:   {alert.source.replace('_', ' ').title()}",
        f"Message:  {alert.message[:500]}",
        "",
        "AI Insight",
        insight[:1000],
    ]
    if actions:
        lines.append("")
        lines.append("Suggested Actions")
        for action in actions[:5]:
            lines.append(f"  • {str(action)}")

    lines += ["", "—", "Homelab AI Sentinel"]
    return "\n".join(lines)


def _build_html(alert: NormalizedAlert, ai: dict[str, Any]) -> str:
    def e(v: str) -> str:
        return html.escape(v)

    insight = ai.get("insight", "No insight available.")
    if not isinstance(insight, str):
        insight = str(insight)

    actions = ai.get("suggested_actions", [])
    if not isinstance(actions, list):
        actions = []

    severity_color = {
        "critical": "#ED4245",
        "warning":  "#FEE75C",
        "info":     "#57F287",
        "unknown":  "#99AAB5",
    }.get(alert.severity, "#99AAB5")

    emoji = _SEVERITY_EMOJI.get(alert.severity, "⚪")
    source = e(alert.source.replace("_", " ").title())

    action_items = "".join(
        f"<li>{e(str(a))}</li>" for a in actions[:5]
    ) if actions else ""
    actions_block = (
        f"<h3>⚡ Suggested Actions</h3><ul>{action_items}</ul>"
        if action_items else ""
    )

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;">
  <div style="border-left:4px solid {severity_color};padding-left:16px;">
    <h2 style="margin:0">{emoji} {e(alert.service_name)}</h2>
    <p style="margin:4px 0;color:#666;">{e(alert.severity.upper())} &mdash; {e(alert.status.upper())}</p>
  </div>
  <table style="margin-top:16px;border-collapse:collapse;">
    <tr><td style="padding:4px 12px 4px 0;color:#666;">Source</td><td>{source}</td></tr>
    <tr><td style="padding:4px 12px 4px 0;color:#666;">Message</td><td>{e(alert.message[:500])}</td></tr>
  </table>
  <h3>🤖 AI Insight</h3>
  <p>{e(insight[:1000])}</p>
  {actions_block}
  <hr style="margin-top:24px;border:none;border-top:1px solid #eee;">
  <p style="color:#999;font-size:12px;">Homelab AI Sentinel</p>
</body>
</html>"""


def post_alert(alert: NormalizedAlert, ai: dict[str, Any]) -> None:
    """
    Send the alert via SMTP with STARTTLS.

    Raises smtplib.SMTPException or OSError on SMTP failure.
    Silently skips if SMTP_HOST, SMTP_USER, or SMTP_PASSWORD is not set.

    SMTP credentials are never included in raised exceptions or log output.
    """
    host = os.environ.get("SMTP_HOST", "")
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    if not host or not user or not password:
        return

    port = int(os.environ.get("SMTP_PORT", "587"))
    to_addr = os.environ.get("SMTP_TO", user)  # default: send to self

    msg = MIMEMultipart("alternative")
    msg["Subject"] = _build_subject(alert)
    msg["From"] = user
    msg["To"] = to_addr

    msg.attach(MIMEText(_build_plain(alert, ai), "plain"))
    msg.attach(MIMEText(_build_html(alert, ai), "html"))

    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, password)
            smtp.sendmail(user, to_addr, msg.as_string())
    except smtplib.SMTPAuthenticationError:
        # Re-raise without credentials in the message — SMTPAuthenticationError
        # may include the server response which can echo the username.
        raise smtplib.SMTPAuthenticationError(535, b"Authentication failed")
