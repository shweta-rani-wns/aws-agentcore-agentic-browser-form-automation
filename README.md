# Booking Automation Lab (real backend, AgentCore-ready)

A fresh test application for building and verifying browser automation against a website you own. It includes:
- A real booking form with server-side validation and a persisted booking record.
- Cascading country → region → city dropdowns.
- JavaScript `alert()` and `confirm()` dialog exercises.
- Email OTP challenge (real email via Amazon SES when configured; no hardcoded OTP).
- CAPTCHA verification using Google reCAPTCHA v2 checkbox (requires keys).
- Application audit events stored with the booking.
- A static frontend that can be hosted by AWS Amplify/S3 and a Python API suitable for local development; AWS deployment can use API Gateway + Lambda + DynamoDB as the next deployment step.

**Security note:** This is a controlled test app, not a production booking system. Do not put real customer data into it. Do not bypass CAPTCHA or 2FA. Automation should pause for a human to complete CAPTCHA or enter the OTP. Browser recording in AgentCore is separate from application audit logging.

## Local development (Windows PowerShell)

Use Python 3.12:
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```
Open http://127.0.0.1:8000.

For email OTP, configure SES in `.env`:
- `AWS_REGION`
- `SES_FROM_EMAIL` (verified sender)
- `SES_TO_EMAIL` (verified recipient while SES is in sandbox)
- `OTP_TTL_SECONDS=300`

If SES is not configured, OTP delivery is deliberately unavailable: the app does not print or expose OTPs in the webpage. This avoids a fake "2FA" flow. In SES sandbox, sender and recipient generally need verification. Request production access before sending to arbitrary recipients.

For CAPTCHA, create a reCAPTCHA v2 checkbox site key and secret for your test domain and set:
- `RECAPTCHA_SITE_KEY`
- `RECAPTCHA_SECRET_KEY`
- `RECAPTCHA_VERIFY_URL=https://www.google.com/recaptcha/api/siteverify`

The server rejects final submission if CAPTCHA keys are missing or verification fails. For local testing, configure reCAPTCHA's documented test keys or use your own development keys; do not silently disable the check.

## Browser dialogs
Click **Test alert** to trigger a real JavaScript `alert()`. Click **Test confirm** to trigger a real JavaScript `confirm()`. Playwright can handle these via a `page.on("dialog", ...)` handler in the AgentCore automation script.

## Audit vs recording
- Application audit: records booking creation/verification events in SQLite for local development.
- AgentCore Browser recording: a separate Browser tool capability configured in AWS, with recording destination/permissions. It captures browser-session events; it is not automatically enabled by this app.

## AWS deployment
The app is structured for a static frontend + API backend. The included `app.py` is for local development only and uses SQLite. Do not deploy SQLite in an ephemeral serverless container and expect durable records.
Recommended low-idle-cost AWS production-style architecture:
1. Frontend: AWS Amplify Hosting (or S3 + CloudFront).
2. API: API Gateway HTTP API + Lambda.
3. Persistent bookings/audit: DynamoDB on-demand.
4. OTP delivery: Amazon SES (or SNS if SMS is specifically required).
5. CAPTCHA: Google reCAPTCHA verified by the backend.
6. Browser automation: AgentCore Browser, invoked by a separate Python worker/agent after the app is deployed.

We will migrate the local API persistence to DynamoDB and create the deployment template after the local end-to-end behavior is verified. The local app is not yet an AWS deployment template.

## Test booking data
Use obviously synthetic test data. The server creates an actual record in `data/bookings.sqlite3` only after CAPTCHA and OTP checks pass. A successful browser click alone is not considered success.
