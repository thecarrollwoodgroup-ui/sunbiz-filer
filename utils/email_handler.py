"""
utils/email_handler.py — Inbound email parser and auto-filer.

Parses a raw RFC 2822 email, extracts business registration fields,
and calls utils.filer.file_business() if all required fields are present.

Expected email format (plain-text body):
    Business Name: Acme Consulting LLC
    Business Type: LLC
    Business Address: 123 Main St, Miami, FL 33101
    Mailing Address: PO Box 1, Miami, FL 33101
    Contact Email: owner@example.com
    Signer Name: Jane Doe
    Signer Title: MGR
    Registered Agent: Jane Doe

Public API
----------
parse_and_file_email(raw_email: str) -> dict
    Returns:
        {
            "success":    bool,
            "filing_id":  str | None,
            "message":    str,
            "parsed":     dict,   # fields extracted from the email
            "errors":     list    # validation / parsing errors
        }
"""

import email
import logging
import re
import smtplib
import os
from email.mime.text import MIMEText
from typing import Any

from utils.filer import file_business

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

# Maps canonical field names → list of regex patterns that match email labels
FIELD_PATTERNS: dict[str, list[str]] = {
    "business_name":    [r"business\s*name", r"entity\s*name", r"company\s*name"],
    "business_type":    [r"business\s*type", r"entity\s*type", r"type"],
    "business_address": [r"business\s*address", r"principal\s*address", r"street\s*address"],
    "mailing_address":  [r"mailing\s*address", r"mail\s*address", r"po\s*box"],
    "contact_email":    [r"contact\s*email", r"email", r"e-mail"],
    "signer_name":      [r"signer\s*name", r"organizer\s*name", r"officer\s*name", r"owner\s*name"],
    "signer_title":     [r"signer\s*title", r"title", r"role"],
    "registered_agent": [r"registered\s*agent", r"agent"],
}

REQUIRED_FIELDS = [
    "business_name",
    "business_type",
    "business_address",
    "contact_email",
    "signer_name",
    "signer_title",
]


def _extract_body(raw_email: str) -> str:
    """Extract the plain-text body from a raw email string."""
    try:
        msg = email.message_from_string(raw_email)
    except Exception as exc:
        logger.warning("Could not parse email headers: %s", exc)
        return raw_email  # treat the whole thing as body

    body_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                charset = part.get_content_charset() or "utf-8"
                try:
                    body_parts.append(part.get_payload(decode=True).decode(charset, errors="replace"))
                except Exception:
                    body_parts.append(str(part.get_payload()))
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                body_parts.append(payload.decode(charset, errors="replace"))
            else:
                body_parts.append(str(msg.get_payload()))
        except Exception:
            body_parts.append(str(msg.get_payload()))

    return "\n".join(body_parts)


def _extract_sender(raw_email: str) -> str | None:
    """Extract the From address from a raw email."""
    try:
        msg = email.message_from_string(raw_email)
        from_header = msg.get("From", "")
        # Extract bare email address
        match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", from_header)
        return match.group(0) if match else from_header or None
    except Exception:
        return None


def _parse_fields(body: str) -> dict[str, str]:
    """
    Parse key: value pairs from the email body.

    Handles lines like:
        Business Name: Acme LLC
        business_name: Acme LLC
        BUSINESS NAME - Acme LLC
    """
    parsed: dict[str, str] = {}

    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue

        # Split on first colon or dash separator
        sep_match = re.match(r"^(.+?)[\s]*[:\-–—]+[\s]*(.+)$", line)
        if not sep_match:
            continue

        label_raw = sep_match.group(1).strip()
        value = sep_match.group(2).strip()

        if not value:
            continue

        # Match label against known field patterns
        for field_name, patterns in FIELD_PATTERNS.items():
            for pattern in patterns:
                if re.fullmatch(pattern, label_raw, re.I):
                    if field_name not in parsed:  # first match wins
                        parsed[field_name] = value
                    break

    return parsed


def _build_filing_data(parsed: dict[str, str]) -> dict[str, Any]:
    """Convert parsed email fields into the filing_data dict expected by filer.py."""
    return {
        "business_name":    parsed.get("business_name", ""),
        "business_type":    parsed.get("business_type", "LLC"),
        "business_address": parsed.get("business_address", ""),
        "mailing_address":  parsed.get("mailing_address", ""),
        "contact_email":    parsed.get("contact_email", ""),
        "signer": {
            "name":  parsed.get("signer_name", ""),
            "title": parsed.get("signer_title", ""),
        },
        "registered_agent": parsed.get("registered_agent", ""),
    }


# ---------------------------------------------------------------------------
# Confirmation email
# ---------------------------------------------------------------------------


def _send_confirmation(to_address: str, filing_result: dict) -> None:
    """
    Send a confirmation (or failure) email back to the submitter.

    Requires environment variables:
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    """
    smtp_host = os.environ.get("SMTP_HOST")
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    smtp_user = os.environ.get("SMTP_USER")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user or "noreply@sunbiz-automation.app")

    if not smtp_host:
        logger.warning("SMTP_HOST not configured — skipping confirmation email")
        return

    subject = (
        "✅ Filing Confirmed" if filing_result.get("success")
        else "❌ Filing Failed"
    )

    body_lines = [
        f"Filing status: {'SUCCESS' if filing_result.get('success') else 'FAILED'}",
        f"Message: {filing_result.get('message', '')}",
    ]
    if filing_result.get("filing_id"):
        body_lines.append(f"Filing ID: {filing_result['filing_id']}")
    if filing_result.get("detail"):
        body_lines.append(f"\nDetail:\n{filing_result['detail']}")

    body = "\n".join(body_lines)

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_address

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)
            server.sendmail(smtp_from, [to_address], msg.as_string())
        logger.info("Confirmation email sent to %s", to_address)
    except Exception as exc:
        logger.error("Failed to send confirmation email to %s: %s", to_address, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_and_file_email(raw_email: str) -> dict[str, Any]:
    """
    Parse an inbound email and auto-file the business registration if all
    required fields are present.

    Args:
        raw_email: Full RFC 2822 email string.

    Returns:
        {
            "success":   bool,
            "filing_id": str | None,
            "message":   str,
            "parsed":    dict,   # fields extracted from the email
            "errors":    list    # missing / invalid fields
        }
    """
    sender = _extract_sender(raw_email)
    body = _extract_body(raw_email)
    parsed = _parse_fields(body)

    logger.info(
        "Email from %s — parsed fields: %s",
        sender,
        list(parsed.keys()),
    )

    # Check for missing required fields
    missing = [f for f in REQUIRED_FIELDS if not parsed.get(f)]
    if missing:
        message = (
            f"Email is missing required fields: {', '.join(missing)}. "
            "Filing was NOT submitted."
        )
        logger.warning(message)
        result = {
            "success": False,
            "filing_id": None,
            "message": message,
            "parsed": parsed,
            "errors": [f"Missing: {f}" for f in missing],
        }
        if sender:
            _send_confirmation(sender, result)
        return result

    # All required fields present — attempt filing
    filing_data = _build_filing_data(parsed)
    filing_result = file_business(filing_data)

    result = {
        "success": filing_result.get("success", False),
        "filing_id": filing_result.get("filing_id"),
        "message": filing_result.get("message", ""),
        "parsed": parsed,
        "errors": [] if filing_result.get("success") else [filing_result.get("detail", "")],
    }

    # Send confirmation back to the submitter
    reply_to = parsed.get("contact_email") or sender
    if reply_to:
        _send_confirmation(reply_to, filing_result)

    return result
