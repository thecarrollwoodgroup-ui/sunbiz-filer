# Sunbiz Automation

A Flask application that automates Florida business registration filings via the
[Florida Division of Corporations (Sunbiz)](https://dos.fl.gov/sunbiz/) portal.

## Features

| Feature | Description |
|---|---|
| **Name search** | Check LLC, Corporation, or Fictitious name availability against live Sunbiz data |
| **E-filing** | Automate new business registration submissions |
| **Email handler** | Parse inbound emails and auto-file if all required fields are present |
| **Status lookup** | Check the status of a previously submitted filing |
| **Web dashboard** | Clean browser UI for manual filings |
| **REST API** | JSON API for programmatic access |

## Project Structure

```
├── app.py                  # Flask routes & entry point
├── automation-requirements.txt
├── Procfile                # Railway / gunicorn start command
├── .env.example            # Environment variable template
├── templates/
│   ├── base.html           # Shared layout
│   ├── dashboard.html      # Filing dashboard
│   └── status.html         # Status lookup page
├── static/
│   └── style.css           # Stylesheet
└── utils/
    ├── __init__.py
    ├── scraper.py          # Sunbiz name-availability scraper
    ├── filer.py            # E-filing automation
    └── email_handler.py    # Inbound email parser & auto-filer
```

## Quick Start

```bash
# 1. Clone and enter the repo
git clone https://github.com/greysolve/sunbiz-automation.git
cd sunbiz-automation

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r automation-requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your values

# 5. Run locally
flask --app app run --debug
# or
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

## API Reference

### `POST /api/search` — Check name availability

```json
// Request
{
  "business_name": "Acme Consulting",
  "business_type": "LLC"   // "LLC" | "Corp" | "Fictitious"
}

// Response
{
  "available": true,
  "matches": [
    { "name": "Acme Consulting Group LLC", "status": "Active", "type": "LLC" }
  ],
  "message": "'Acme Consulting' appears to be available..."
}
```

### `POST /api/file` — File a new business

```json
// Request
{
  "business_name":    "Acme Consulting LLC",
  "business_type":    "LLC",
  "business_address": "123 Main St, Miami, FL 33101",
  "mailing_address":  "PO Box 1, Miami, FL 33101",
  "contact_email":    "owner@example.com",
  "signer": {
    "name":  "Jane Doe",
    "title": "MGR"
  },
  "registered_agent": "Jane Doe"
}

// Response
{
  "success":   true,
  "filing_id": "L24000012345",
  "message":   "Successfully filed 'Acme Consulting LLC' as a LLC.",
  "detail":    "..."
}
```

**Valid signer titles:**
- LLC: `MGR` (Manager), `AMBR` (Authorized Member)
- Corp: `PRESIDENT`, `SECRETARY`, `VICE PRESIDENT`, `TREASURER`

### `POST /api/email` — Email submission handler

```json
// Request
{
  "raw_email": "<full RFC 2822 email string>"
}
```

The email body should contain key-value pairs:

```
Business Name: Acme Consulting LLC
Business Type: LLC
Business Address: 123 Main St, Miami, FL 33101
Contact Email: owner@example.com
Signer Name: Jane Doe
Signer Title: MGR
Registered Agent: Jane Doe
```

### `GET /api/status/<filing_id>` — Check filing status

```json
// Response
{
  "filing_id": "L24000012345",
  "status":    "approved",   // "pending" | "approved" | "rejected" | "unknown"
  "detail":    "..."
}
```

### `GET /healthz` — Health check

```json
{ "status": "ok" }
```

## Deploying to Railway

1. Push this repo to GitHub.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Set environment variables in the Railway dashboard (see `.env.example`).
4. Railway auto-detects the `Procfile` and starts gunicorn.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | Yes | Flask session secret (use a long random string) |
| `PORT` | No | Port to listen on (Railway sets this automatically) |
| `FLASK_DEBUG` | No | Set `true` for local development only |
| `SMTP_HOST` | No | SMTP server for confirmation emails |
| `SMTP_PORT` | No | SMTP port (default: 587) |
| `SMTP_USER` | No | SMTP username |
| `SMTP_PASSWORD` | No | SMTP password |
| `SMTP_FROM` | No | From address for outbound emails |

## Stripe Integration (Planned)

Payment processing will be added in a future release. The `.env.example` file
already includes placeholder variables for `STRIPE_SECRET_KEY`,
`STRIPE_PUBLISHABLE_KEY`, and `STRIPE_WEBHOOK_SECRET`.

## Notes

- This tool automates interactions with the official Sunbiz portal. Portal
  structure changes may require scraper/filer updates.
- Always verify filings at [search.sunbiz.org](https://search.sunbiz.org).
- The app is stateless — no database is required.
