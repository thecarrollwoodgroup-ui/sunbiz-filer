"""
sunbiz-automation — Flask application entry point.

Routes:
    GET  /                      → Web dashboard
    POST /api/search            → Search business name availability
    POST /api/file              → File a new business registration
    POST /api/email             → Email submission handler (auto-file)
    GET  /api/status/<filing_id>→ Check filing status
"""

import logging
import os

from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from utils.email_handler import parse_and_file_email
from utils.filer import file_business
from utils.scraper import search_business_name

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")

# ---------------------------------------------------------------------------
# Web dashboard
# ---------------------------------------------------------------------------


@app.route("/", methods=["GET"])
def dashboard():
    """Render the main filing dashboard."""
    return render_template("dashboard.html")


@app.route("/status", methods=["GET"])
def status_page():
    """Render the filing status page (filing_id passed as query param)."""
    filing_id = request.args.get("filing_id", "")
    return render_template("status.html", filing_id=filing_id)


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------


@app.route("/api/search", methods=["POST"])
def api_search():
    """
    Search for business name availability.

    Request JSON:
        {
            "business_name": "Acme LLC",
            "business_type": "LLC"   // "LLC" | "Corp" | "Fictitious"
        }

    Response JSON:
        {
            "available": true,
            "matches": [...],
            "message": "..."
        }
    """
    data = request.get_json(silent=True) or {}
    business_name = (data.get("business_name") or "").strip()
    business_type = (data.get("business_type") or "LLC").strip()

    if not business_name:
        return jsonify({"error": "business_name is required"}), 400

    logger.info("Search request — name=%r  type=%r", business_name, business_type)

    try:
        result = search_business_name(business_name, business_type)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("Search failed: %s", exc)
        return jsonify({"error": "Search failed", "detail": str(exc)}), 500


@app.route("/api/file", methods=["POST"])
def api_file():
    """
    File a new business registration.

    Request JSON:
        {
            "business_name":    "Acme LLC",
            "business_type":    "LLC",          // "LLC" | "Corp"
            "business_address": "123 Main St, Miami, FL 33101",
            "mailing_address":  "PO Box 1, Miami, FL 33101",  // optional
            "contact_email":    "owner@example.com",
            "signer": {
                "name":  "Jane Doe",
                "title": "MGR"   // MGR | AMBR (LLC) or President | Secretary (Corp)
            },
            "registered_agent": "Jane Doe"  // optional — defaults to signer name
        }

    Response JSON:
        {
            "success": true,
            "filing_id": "...",
            "message": "..."
        }
    """
    data = request.get_json(silent=True) or {}

    required = ["business_name", "business_type", "business_address", "contact_email", "signer"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    signer = data["signer"]
    if not isinstance(signer, dict) or not signer.get("name") or not signer.get("title"):
        return jsonify({"error": "signer must include 'name' and 'title'"}), 400

    logger.info(
        "File request — name=%r  type=%r  email=%r",
        data["business_name"],
        data["business_type"],
        data["contact_email"],
    )

    try:
        result = file_business(data)
        status_code = 200 if result.get("success") else 422
        return jsonify(result), status_code
    except Exception as exc:
        logger.exception("Filing failed: %s", exc)
        return jsonify({"error": "Filing failed", "detail": str(exc)}), 500


@app.route("/api/email", methods=["POST"])
def api_email():
    """
    Email submission handler.

    Accepts a raw email payload (multipart/form-data or JSON with 'raw_email' key)
    and attempts to parse + auto-file the business registration.

    Request (JSON):
        { "raw_email": "<full RFC 2822 email string>" }

    Response JSON:
        {
            "success": true,
            "filing_id": "...",
            "message": "..."
        }
    """
    data = request.get_json(silent=True) or {}
    raw_email = data.get("raw_email") or request.data.decode("utf-8", errors="replace")

    if not raw_email:
        return jsonify({"error": "No email content provided"}), 400

    logger.info("Email submission received (%d bytes)", len(raw_email))

    try:
        result = parse_and_file_email(raw_email)
        status_code = 200 if result.get("success") else 422
        return jsonify(result), status_code
    except Exception as exc:
        logger.exception("Email handler failed: %s", exc)
        return jsonify({"error": "Email processing failed", "detail": str(exc)}), 500


@app.route("/api/status/<filing_id>", methods=["GET"])
def api_status(filing_id: str):
    """
    Check the status of a previously submitted filing.

    Response JSON:
        {
            "filing_id": "...",
            "status": "pending" | "approved" | "rejected" | "unknown",
            "detail": "..."
        }
    """
    if not filing_id:
        return jsonify({"error": "filing_id is required"}), 400

    logger.info("Status check — filing_id=%r", filing_id)

    try:
        from utils.filer import check_filing_status

        result = check_filing_status(filing_id)
        return jsonify(result), 200
    except Exception as exc:
        logger.exception("Status check failed: %s", exc)
        return jsonify({"error": "Status check failed", "detail": str(exc)}), 500


# ---------------------------------------------------------------------------
# Health check (Railway / load-balancer probe)
# ---------------------------------------------------------------------------


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
