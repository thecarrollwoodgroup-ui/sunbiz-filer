"""
utils/filer.py — Florida Sunbiz e-filing automation.

Automates the Division of Corporations online filing portal at:
    https://dos.fl.gov/sunbiz/start-business/efile/

Public API
----------
file_business(filing_data: dict) -> dict
    Submit a new business registration.  Returns:
        {
            "success":    bool,
            "filing_id":  str | None,
            "message":    str,
            "detail":     str   # raw status text from portal
        }

check_filing_status(filing_id: str) -> dict
    Look up a previously submitted filing.  Returns:
        {
            "filing_id": str,
            "status":    "pending" | "approved" | "rejected" | "unknown",
            "detail":    str
        }
"""

import logging
import re
import time
import uuid
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EFILE_BASE_URL = "https://dos.fl.gov/sunbiz/start-business/efile/"
STATUS_CHECK_URL = "https://dos.fl.gov/sunbiz/start-business/efile/status/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; sunbiz-automation/1.0; "
        "+https://github.com/greysolve/sunbiz-automation)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_TIMEOUT = 60  # seconds — filing can be slow
MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds

# Valid signer titles per entity type
VALID_TITLES = {
    "LLC": {"MGR", "AMBR"},
    "CORP": {"PRESIDENT", "SECRETARY", "VICE PRESIDENT", "TREASURER"},
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_filing_data(data: dict) -> list[str]:
    """Return a list of validation error strings (empty = valid)."""
    errors: list[str] = []

    business_type = (data.get("business_type") or "").strip().upper()
    if business_type not in ("LLC", "CORP"):
        errors.append("business_type must be 'LLC' or 'Corp'")

    signer = data.get("signer") or {}
    title = (signer.get("title") or "").strip().upper()
    valid_titles = VALID_TITLES.get(business_type, set())
    if valid_titles and title not in valid_titles:
        errors.append(
            f"signer.title '{title}' is not valid for {business_type}. "
            f"Must be one of: {', '.join(sorted(valid_titles))}"
        )

    email = data.get("contact_email") or ""
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        errors.append(f"contact_email '{email}' does not appear to be a valid email address")

    return errors


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _get_csrf_token(session: requests.Session, url: str) -> str | None:
    """Fetch a page and extract a CSRF token if present."""
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Common CSRF field names used by Florida state portals
    for name in ("__RequestVerificationToken", "csrf_token", "_token", "authenticity_token"):
        tag = soup.find("input", {"name": name})
        if tag:
            return tag.get("value")

    # Also check meta tags
    meta = soup.find("meta", {"name": re.compile(r"csrf", re.I)})
    if meta:
        return meta.get("content")

    return None


# ---------------------------------------------------------------------------
# Filing logic
# ---------------------------------------------------------------------------


def _build_llc_payload(data: dict, csrf_token: str | None) -> dict:
    """Build the form payload for an LLC filing."""
    signer = data.get("signer", {})
    mailing = data.get("mailing_address") or data.get("business_address", "")
    registered_agent = data.get("registered_agent") or signer.get("name", "")

    payload: dict[str, str] = {
        # Entity info
        "EntityName": data["business_name"],
        "EntityType": "LLC",
        # Addresses
        "PrincipalAddress": data["business_address"],
        "MailingAddress": mailing,
        # Registered agent
        "RegisteredAgentName": registered_agent,
        "RegisteredAgentAddress": data.get("business_address", ""),
        # Signer / organizer
        "OrganizerName": signer.get("name", ""),
        "OrganizerTitle": signer.get("title", "MGR"),
        # Contact
        "ContactEmail": data["contact_email"],
    }

    if csrf_token:
        payload["__RequestVerificationToken"] = csrf_token

    return payload


def _build_corp_payload(data: dict, csrf_token: str | None) -> dict:
    """Build the form payload for a Corporation filing."""
    signer = data.get("signer", {})
    mailing = data.get("mailing_address") or data.get("business_address", "")
    registered_agent = data.get("registered_agent") or signer.get("name", "")

    payload: dict[str, str] = {
        "EntityName": data["business_name"],
        "EntityType": "CORP",
        "PrincipalAddress": data["business_address"],
        "MailingAddress": mailing,
        "RegisteredAgentName": registered_agent,
        "RegisteredAgentAddress": data.get("business_address", ""),
        "OfficerName": signer.get("name", ""),
        "OfficerTitle": signer.get("title", "PRESIDENT"),
        "ContactEmail": data["contact_email"],
    }

    if csrf_token:
        payload["__RequestVerificationToken"] = csrf_token

    return payload


def _parse_filing_response(html: str) -> dict[str, Any]:
    """
    Parse the portal's response HTML to extract filing ID and status.

    Returns:
        {"success": bool, "filing_id": str|None, "detail": str}
    """
    soup = BeautifulSoup(html, "html.parser")

    # Look for a confirmation / filing number
    filing_id: str | None = None

    # Pattern: "Filing Number: L24000012345" or "Document Number: ..."
    text = soup.get_text(" ", strip=True)
    match = re.search(
        r"(?:filing|document|confirmation|reference)\s*(?:number|#|no\.?)[:\s]+([A-Z0-9]+)",
        text,
        re.I,
    )
    if match:
        filing_id = match.group(1).strip()

    # Detect success / failure keywords
    lower_text = text.lower()
    success_keywords = ["successfully filed", "filing accepted", "confirmation", "thank you"]
    error_keywords = ["error", "failed", "rejected", "invalid", "already exists"]

    success = any(kw in lower_text for kw in success_keywords)
    has_error = any(kw in lower_text for kw in error_keywords)

    if has_error and not success:
        success = False

    # Grab a meaningful snippet for the detail field
    detail_tag = (
        soup.find(class_=re.compile(r"(success|confirm|alert|message|error)", re.I))
        or soup.find("h1")
        or soup.find("h2")
    )
    detail = detail_tag.get_text(strip=True) if detail_tag else text[:300]

    return {"success": success, "filing_id": filing_id, "detail": detail}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def file_business(filing_data: dict) -> dict[str, Any]:
    """
    Submit a new business registration to the Florida Sunbiz e-filing portal.

    Args:
        filing_data: dict with keys:
            business_name, business_type, business_address,
            mailing_address (optional), contact_email,
            signer ({"name": str, "title": str}),
            registered_agent (optional)

    Returns:
        {
            "success":   bool,
            "filing_id": str | None,
            "message":   str,
            "detail":    str
        }
    """
    # Validate
    errors = _validate_filing_data(filing_data)
    if errors:
        return {
            "success": False,
            "filing_id": None,
            "message": "Validation failed",
            "detail": "; ".join(errors),
        }

    business_type = filing_data["business_type"].strip().upper()
    business_name = filing_data["business_name"]

    logger.info("Filing %s: %r", business_type, business_name)

    session = _make_session()

    # Step 1 — load the filing page to get CSRF token / session cookies
    try:
        csrf_token = _get_csrf_token(session, EFILE_BASE_URL)
        logger.debug("CSRF token: %s", csrf_token)
    except requests.RequestException as exc:
        logger.error("Could not load e-filing portal: %s", exc)
        return {
            "success": False,
            "filing_id": None,
            "message": "Could not reach the e-filing portal",
            "detail": str(exc),
        }

    # Step 2 — build and submit the form
    if business_type == "LLC":
        payload = _build_llc_payload(filing_data, csrf_token)
    else:
        payload = _build_corp_payload(filing_data, csrf_token)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.post(
                EFILE_BASE_URL,
                data=payload,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            resp.raise_for_status()
            break
        except requests.RequestException as exc:
            logger.warning("Filing POST attempt %d/%d failed: %s", attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                return {
                    "success": False,
                    "filing_id": None,
                    "message": "Filing submission failed after retries",
                    "detail": str(exc),
                }

    # Step 3 — parse the response
    result = _parse_filing_response(resp.text)

    if result["success"]:
        message = (
            f"Successfully filed '{business_name}' as a {business_type}. "
            f"Filing ID: {result['filing_id'] or 'see detail'}"
        )
    else:
        message = f"Filing for '{business_name}' was not confirmed. Review the detail."

    return {
        "success": result["success"],
        "filing_id": result["filing_id"],
        "message": message,
        "detail": result["detail"],
    }


def check_filing_status(filing_id: str) -> dict[str, Any]:
    """
    Check the status of a previously submitted filing.

    Args:
        filing_id: The filing/document number returned by file_business().

    Returns:
        {
            "filing_id": str,
            "status":    "pending" | "approved" | "rejected" | "unknown",
            "detail":    str
        }
    """
    logger.info("Checking status for filing_id=%r", filing_id)

    url = f"{STATUS_CHECK_URL}{filing_id}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Status check request failed: %s", exc)
        return {
            "filing_id": filing_id,
            "status": "unknown",
            "detail": f"Could not reach status portal: {exc}",
        }

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text(" ", strip=True).lower()

    if any(kw in text for kw in ["approved", "active", "filed", "accepted"]):
        status = "approved"
    elif any(kw in text for kw in ["pending", "processing", "under review"]):
        status = "pending"
    elif any(kw in text for kw in ["rejected", "denied", "cancelled", "withdrawn"]):
        status = "rejected"
    else:
        status = "unknown"

    detail_tag = soup.find(class_=re.compile(r"(status|result|message)", re.I)) or soup.find("p")
    detail = detail_tag.get_text(strip=True) if detail_tag else text[:300]

    return {"filing_id": filing_id, "status": status, "detail": detail}
