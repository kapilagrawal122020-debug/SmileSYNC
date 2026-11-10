"""Thin wrapper over the WhatsApp Business Cloud API (Meta).

Business-started messages must use TEMPLATES that are approved in Meta Business Manager,
so every message here is sent as a template with text parameters.
Without WHATSAPP_TOKEN set, nothing is sent: the call only logs ("dry run").
"""
import json
import logging
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def send_template(phone, template, params=(), language="en"):
    """Send one template message to a 10-digit Indian number. Returns (status, error)."""
    if not settings.WHATSAPP_TOKEN:
        logger.info("WhatsApp dry run to %s: %s %s", phone, template, list(params))
        return "dry_run", ""

    payload = {
        "messaging_product": "whatsapp",
        "to": f"91{phone}",
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": language},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": str(p)} for p in params]}
            ],
        },
    }
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{settings.WHATSAPP_PHONE_ID}/messages"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(request, timeout=10)
        return "sent", ""
    except Exception as exc:  # network error, template not approved, number not on WhatsApp...
        logger.warning("WhatsApp send failed to %s: %s", phone, exc)
        return "failed", str(exc)[:300]
