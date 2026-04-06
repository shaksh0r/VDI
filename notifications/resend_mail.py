"""
Resend integration for lab / VDI access codes.

Setup:
  1. Create an account at https://resend.com
  2. Create an API key → set RESEND_API_KEY
  3. Add and verify your sending domain (DNS), or use Resend's onboarding domain for tests
  4. Set RESEND_FROM to a verified sender, e.g. "VDI Labs <noreply@yourdomain.com>"

Run a test from the repo root (with .env or env vars set):

  python -m notifications.resend_mail --to teammate@example.com --code 123456

Hello-world test (same shape as Resend onboarding docs; reads recipient emails from JSON):

  python -m notifications.resend_mail --hello-test notifications/sample_email_recipients.json

Send a unique 6-digit access code to each person in a lab-style JSON (``students[]``):

  python -m notifications.resend_mail --send-codes notifications/sample_access_codes_batch.json

Dev HTTP upload (no auth): ``uvicorn notifications.roster_upload_app:app --port 8020`` from repo root, then
``POST /send-codes`` with multipart field ``file`` = JSON body (same shape as the sample file).
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Optional

import resend

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger(__name__)


class ResendMailError(Exception):
    """Misconfiguration or Resend API failure."""


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ResendMailError(f"Environment variable {name} is not set")
    return value


_RESEND_ONBOARDING_FROM = "onboarding@resend.dev"
_UNVERIFIED_FROM_DOMAIN = "cloudlab.buet.ac.bd"


def _effective_resend_from() -> str:
    """
    ``RESEND_FROM`` must still be set (e.g. for production), but if it uses an unverified
    lab hostname we substitute Resend's onboarding sender so local tests do not fail API-side.
    """
    raw = _require_env("RESEND_FROM")
    if _UNVERIFIED_FROM_DOMAIN in raw.lower():
        logger.warning(
            "RESEND_FROM uses %s (not verified in Resend); sending as %s",
            _UNVERIFIED_FROM_DOMAIN,
            _RESEND_ONBOARDING_FROM,
        )
        return _RESEND_ONBOARDING_FROM
    return raw


def _configure_client() -> None:
    resend.api_key = _require_env("RESEND_API_KEY")


def send_raw_email(
    *,
    to: str | list[str],
    subject: str,
    text: str,
    html_body: Optional[str] = None,
    reply_to: Optional[str] = None,
) -> str:
    """
    Send one email via Resend. Returns Resend message id.

    ``to`` may be a single address or a list (Resend supports multiple recipients).
    """
    _configure_client()
    from_addr = _effective_resend_from()

    params: resend.Emails.SendParams = {
        "from": from_addr,
        "to": to if isinstance(to, list) else [to],
        "subject": subject,
        "text": text,
    }
    if html_body is not None:
        params["html"] = html_body
    if reply_to:
        params["reply_to"] = reply_to

    try:
        response = resend.Emails.send(params)
    except Exception as e:
        logger.exception("Resend API error")
        raise ResendMailError(str(e)) from e

    if not response or not getattr(response, "id", None):
        raise ResendMailError("Resend returned no message id")

    return str(response.id)


def recipient_emails_from_json(data: dict[str, Any]) -> list[str]:
    """Collect unique recipient addresses from a lab-style JSON payload."""
    out: list[str] = []
    if isinstance(data.get("recipients"), list):
        for item in data["recipients"]:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
    students = data.get("students")
    if isinstance(students, list):
        for s in students:
            if isinstance(s, dict):
                e = (s.get("email") or "").strip()
                if e:
                    out.append(e)
    seen: set[str] = set()
    unique: list[str] = []
    for e in out:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    if not unique:
        raise ResendMailError("JSON has no recipient emails (use 'recipients' or 'students[].email')")
    return unique


def send_hello_world_test(*, to: str | list[str]) -> str:
    """
    Minimal send matching Resend's onboarding example (https://resend.com docs).

    Uses RESEND_API_KEY and RESEND_FROM from the environment.
    """
    _configure_client()
    from_addr = _effective_resend_from()
    params: resend.Emails.SendParams = {
        "from": from_addr,
        "to": to,
        "subject": "Hello World",
        "html": "<p>Congrats on sending your <strong>first email</strong>!</p>",
    }
    try:
        response = resend.Emails.send(params)
    except Exception as e:
        logger.exception("Resend API error")
        raise ResendMailError(str(e)) from e
    if not response or not getattr(response, "id", None):
        raise ResendMailError("Resend returned no message id")
    return str(response.id)


def _generate_unique_six_digit_codes(n: int) -> list[str]:
    if n < 1:
        return []
    seen: set[str] = set()
    out: list[str] = []
    while len(out) < n:
        c = f"{secrets.randbelow(1_000_000):06d}"
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def students_from_lab_json(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse ``students`` entries with at least ``email`` for batch access-code sends."""
    students = data.get("students")
    if not isinstance(students, list) or not students:
        raise ResendMailError("JSON must contain a non-empty 'students' array")
    rows: list[dict[str, Any]] = []
    for s in students:
        if not isinstance(s, dict):
            continue
        email = (s.get("email") or "").strip()
        if not email:
            continue
        fn = (s.get("full_name") or "").strip()
        rows.append(
            {
                "email": email,
                "full_name": fn or None,
            }
        )
    if not rows:
        raise ResendMailError("No valid student rows with 'email' in JSON")
    return rows


def send_access_codes_from_lab_data(raw: dict[str, Any]) -> list[dict[str, str]]:
    """
    Parse lab JSON (already loaded), assign unique 6-digit codes, email each student.

    Returns a list of dicts with ``email``, ``code``, ``resend_id`` (for logging / audit).
    """
    if not isinstance(raw, dict):
        raise ResendMailError("JSON root must be an object")
    students = students_from_lab_json(raw)
    lab_title = raw.get("lab_title")
    lab_title_str = lab_title.strip() if isinstance(lab_title, str) and lab_title.strip() else None
    portal = raw.get("portal_url")
    portal_str = portal.strip() if isinstance(portal, str) and portal.strip() else None

    codes = _generate_unique_six_digit_codes(len(students))
    results: list[dict[str, str]] = []
    for row, code in zip(students, codes, strict=True):
        mid = send_access_code_email(
            row["email"],
            code,
            full_name=row["full_name"],
            lab_title=lab_title_str,
            portal_url=portal_str,
        )
        results.append({"email": row["email"], "code": code, "resend_id": mid})
        logger.info("Sent access code to %s (Resend id %s)", row["email"], mid)
    return results


def send_access_codes_from_lab_json(path: Path) -> list[dict[str, str]]:
    """Load lab JSON from disk; see ``send_access_codes_from_lab_data``."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ResendMailError("JSON root must be an object")
    return send_access_codes_from_lab_data(raw)


def send_access_code_email(
    to_email: str,
    code: str,
    *,
    full_name: Optional[str] = None,
    lab_title: Optional[str] = None,
    portal_url: Optional[str] = None,
) -> str:
    """
    Send the standard lab VM access code email. Returns Resend message id.
    """
    safe_code = html.escape(code.strip())
    safe_name = html.escape(full_name.strip()) if full_name else None
    safe_lab = html.escape(lab_title.strip()) if lab_title else None
    portal = html.escape(portal_url.strip()) if portal_url else None

    greeting = f"Hello {safe_name}," if safe_name else "Hello,"
    lab_line = f"<p><strong>Lab:</strong> {safe_lab}</p>" if safe_lab else ""
    portal_line = (
        f'<p>Open the VDI portal: <a href="{portal}">{portal}</a></p>'
        if portal
        else ""
    )

    if lab_title and lab_title.strip():
        subject = lab_title.strip()
        if len(subject) > 200:
            subject = subject[:197] + "..."
    else:
        subject = "Your VDI lab access code"

    text_body = (
        f"{greeting.replace('<br/>', '')}\n\n"
        f"Your access code is: {code.strip()}\n\n"
        "Enter this code in the portal under Join to connect to your VM.\n"
    )
    if lab_title:
        text_body = f"Lab: {lab_title.strip()}\n\n" + text_body
    if portal_url:
        text_body += f"\nPortal: {portal_url.strip()}\n"

    html_body = f"""\
<!DOCTYPE html>
<html>
<body style="font-family: system-ui, sans-serif; line-height: 1.5;">
  <p>{greeting}</p>
  {lab_line}
  <p>Your access code is:</p>
  <p style="font-size: 1.5rem; letter-spacing: 0.2em; font-weight: bold;">{safe_code}</p>
  <p>Enter this code in the VDI portal under <strong>Join</strong> to connect to your VM.</p>
  {portal_line}
</body>
</html>
"""

    return send_raw_email(
        to=to_email,
        subject=subject,
        text=text_body,
        html_body=html_body,
        reply_to=os.getenv("RESEND_REPLY_TO") or None,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Send a test access-code email via Resend")
    parser.add_argument("--hello-test", metavar="JSON", help="Send Resend onboarding-style Hello World to emails in JSON")
    parser.add_argument(
        "--send-codes",
        metavar="JSON",
        help="Lab JSON with students[]: generate unique 6-digit codes and email each student",
    )
    parser.add_argument("--to", help="Recipient email (not used with --hello-test)")
    parser.add_argument("--code", default="123456", help="6-digit code (default 123456)")
    parser.add_argument("--name", default=None, help="Student display name")
    parser.add_argument("--lab", default=None, help="Lab title")
    parser.add_argument("--portal", default=None, help="Portal URL for the HTML body")
    args = parser.parse_args()

    if args.hello_test:
        path = Path(args.hello_test)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit("JSON root must be an object")
        recipients = recipient_emails_from_json(data)
        mid = send_hello_world_test(to=recipients)
        print(f"Sent to {len(recipients)} address(es). Resend id: {mid}")
        return

    if args.send_codes:
        path = Path(args.send_codes)
        try:
            results = send_access_codes_from_lab_json(path)
        except ResendMailError as e:
            raise SystemExit(str(e)) from e
        print(json.dumps({"sent": results}, indent=2))
        return

    if not args.to:
        parser.error("--to is required unless --hello-test or --send-codes is used")

    mid = send_access_code_email(
        args.to,
        args.code,
        full_name=args.name,
        lab_title=args.lab,
        portal_url=args.portal,
    )
    print(f"Sent. Resend id: {mid}")


if __name__ == "__main__":
    main()
