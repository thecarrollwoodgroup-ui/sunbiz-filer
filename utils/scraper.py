"""
utils/scraper.py — Sunbiz business name availability checker.

Searches two Florida Division of Corporations endpoints:
  • Corporations / LLCs  → https://search.sunbiz.org/Inquiry/CorporationSearch/ByName
  • Fictitious names     → https://dos.sunbiz.org/ficinam.html

Public API
----------
search_business_name(business_name, business_type) -> dict
    Returns:
        {
            "available": bool,
            "matches":   list[dict],   # existing registrations found
            "message":   str
        }
"""

import logging
import re
import time
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CORP_SEARCH_URL = (
    "https://search.sunbiz.org/Inquiry/CorporationSearch/ByName"
)
FICTITIOUS_SEARCH_URL = "https://dos.sunbiz.org/ficinam.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; sunbiz-automation/1.0; "
        "+https://github.com/greysolve/sunbiz-automation)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_TIMEOUT = 30  # seconds
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_with_retry(url: str, **kwargs) -> requests.Response:
    """GET with simple retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("GET %s attempt %d/%d failed: %s", url, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                raise


def _post_with_retry(url: str, **kwargs) -> requests.Response:
    """POST with simple retry logic."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            logger.warning("POST %s attempt %d/%d failed: %s", url, attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
            else:
                raise


# ---------------------------------------------------------------------------
# Corporation / LLC search
# ---------------------------------------------------------------------------


def _search_corporations(business_name: str) -> list[dict[str, Any]]:
    """
    Search the Sunbiz corporation/LLC index by name.

    Returns a list of matching entity dicts:
        [{"name": str, "document_number": str, "status": str, "type": str}, ...]
    """
    logger.info("Searching corporations for %r", business_name)

    params = {
        "SearchTerm": business_name,
        "SearchType": "Contains",
    }

    resp = _get_with_retry(CORP_SEARCH_URL, params=params)
    soup = BeautifulSoup(resp.text, "html.parser")

    matches: list[dict[str, Any]] = []

    # The results table has class "searchResultsTable"
    table = soup.find("table", {"class": re.compile(r"searchResults", re.I)})
    if not table:
        logger.debug("No results table found for corporation search")
        return matches

    rows = table.find_all("tr")[1:]  # skip header row
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        name = cells[0].get_text(strip=True)
        doc_number = cells[1].get_text(strip=True)
        status = cells[2].get_text(strip=True)
        entity_type = cells[3].get_text(strip=True) if len(cells) > 3 else ""

        matches.append(
            {
                "name": name,
                "document_number": doc_number,
                "status": status,
                "type": entity_type,
            }
        )

    logger.info("Corporation search returned %d matches", len(matches))
    return matches


# ---------------------------------------------------------------------------
# Fictitious name search
# ---------------------------------------------------------------------------


def _search_fictitious(business_name: str) -> list[dict[str, Any]]:
    """
    Search the Sunbiz fictitious name registry.

    Returns a list of matching entity dicts.
    """
    logger.info("Searching fictitious names for %r", business_name)

    # The fictitious name search uses a form POST
    form_data = {
        "nm": business_name,
        "SearchType": "Contains",
    }

    resp = _post_with_retry(FICTITIOUS_SEARCH_URL, data=form_data)
    soup = BeautifulSoup(resp.text, "html.parser")

    matches: list[dict[str, Any]] = []

    table = soup.find("table", {"class": re.compile(r"searchResults", re.I)})
    if not table:
        # Try any table with results
        table = soup.find("table")

    if not table:
        logger.debug("No results table found for fictitious name search")
        return matches

    rows = table.find_all("tr")[1:]
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        name = cells[0].get_text(strip=True)
        status = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        matches.append({"name": name, "status": status, "type": "Fictitious Name"})

    logger.info("Fictitious name search returned %d matches", len(matches))
    return matches


# ---------------------------------------------------------------------------
# Availability logic
# ---------------------------------------------------------------------------


def _is_exact_match(query: str, result_name: str) -> bool:
    """
    Return True if result_name is an exact (case-insensitive) match for query,
    ignoring common entity suffixes so "Acme" matches "Acme LLC" etc.
    """
    suffixes = r"\s*(LLC|L\.L\.C\.|INC|INC\.|CORP|CORP\.|CO\.|LTD|LTD\.)\s*$"
    clean = lambda s: re.sub(suffixes, "", s.strip(), flags=re.I).strip().lower()
    return clean(query) == clean(result_name)


def _name_is_available(query: str, matches: list[dict]) -> bool:
    """
    A name is considered *unavailable* if any active/current match is an exact
    match for the query.  Dissolved / inactive entities do not block the name.
    """
    active_statuses = {"active", "current", "in use"}
    for m in matches:
        if _is_exact_match(query, m.get("name", "")):
            status = m.get("status", "").lower()
            if any(s in status for s in active_statuses):
                return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_business_name(business_name: str, business_type: str = "LLC") -> dict[str, Any]:
    """
    Search for business name availability on Sunbiz.

    Args:
        business_name: The name to search for.
        business_type: "LLC", "Corp", or "Fictitious".

    Returns:
        {
            "available": bool,
            "matches":   list[dict],
            "message":   str
        }
    """
    business_type_upper = business_type.strip().upper()

    if business_type_upper == "FICTITIOUS":
        matches = _search_fictitious(business_name)
    else:
        matches = _search_corporations(business_name)

    available = _name_is_available(business_name, matches)

    if available:
        message = (
            f"'{business_name}' appears to be available for registration as a "
            f"{business_type}. {len(matches)} similar name(s) found."
        )
    else:
        message = (
            f"'{business_name}' is already registered and active. "
            f"Please choose a different name."
        )

    return {
        "available": available,
        "matches": matches,
        "message": message,
    }
