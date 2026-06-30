"""
Step 3 of the email agent: add a send-reply tool, gated by manual approval.

What's new vs the previous version:
  - The Gmail scope now includes gmail.send, not just readonly.
  - draft_reply() returns structured JSON (needs_reply + reply_text)
    instead of mixed prose, so the loop can act on it cleanly.
  - A new send_reply() tool actually sends a properly-threaded reply
    via the Gmail API.
  - IMPORTANT: sending only happens after you type 'y' at a prompt.
    The LLM drafts; you approve; only then does the tool fire. This
    keeps a human in the loop for the one step that has a real,
    irreversible effect on the outside world.

Setup (same as before):
    pip install google-genai google-auth google-auth-oauthlib google-api-python-client
    export GEMINI_API_KEY="your-key-from-aistudio.google.com"

NOTE: since the scope changed, delete your old token.json before
running this version -- you'll be asked to re-approve access, this
time including permission to send.
"""

import os
import base64
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google import genai

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]
MODEL = "gemini-2.5-flash"

gemini_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


def get_gmail_service():
    """Same auth flow as before -- reuses your saved token.json if present."""
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=8080, open_browser=False)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_email_body(payload):
    """
    Gmail stores email bodies in nested 'parts', base64-encoded, and the
    structure varies (plain text vs HTML vs multipart). This walks the
    structure and pulls out the plain text version.
    """
    if "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain":
                data = part["body"].get("data")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            # Some emails nest parts inside parts (multipart/alternative etc.)
            if "parts" in part:
                result = get_email_body(part)
                if result:
                    return result
    else:
        data = payload["body"].get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    return "(no plain text body found)"


def fetch_unread_emails(service, max_results=5):
    """Fetches unread emails with full content (headers + body), not just metadata."""
    results = service.users().messages().list(
        userId="me", labelIds=["UNREAD"], maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg in messages:
        msg_data = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg_data["payload"]["headers"]}
        body = get_email_body(msg_data["payload"])

        emails.append({
            "id": msg["id"],
            "thread_id": msg_data["threadId"],
            "from": headers.get("From", "(unknown)"),
            "subject": headers.get("Subject", "(no subject)"),
            "message_id_header": headers.get("Message-ID", ""),
            "body": body[:2000],  # cap length -- plenty for drafting a reply
        })

    return emails


def send_reply(service, email_data, reply_text):
    """
    The actual 'send' tool. Builds a properly-threaded reply (same
    subject with Re:, references the original message) and sends it
    via the Gmail API.

    This is only ever called AFTER the human has approved the draft --
    see the approval gate in __main__.
    """
    import email.mime.text
    import re

    to_addr = re.search(r"<(.+?)>", email_data["from"])
    to_addr = to_addr.group(1) if to_addr else email_data["from"]

    subject = email_data["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    message = email.mime.text.MIMEText(reply_text)
    message["to"] = to_addr
    message["subject"] = subject
    if email_data["message_id_header"]:
        message["In-Reply-To"] = email_data["message_id_header"]
        message["References"] = email_data["message_id_header"]

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    sent = service.users().messages().send(
        userId="me",
        body={"raw": raw, "threadId": email_data["thread_id"]},
    ).execute()

    return sent


def mark_as_read(service, email_id):
    """
    Removes the UNREAD label so this email won't be re-fetched and
    re-processed on the next run. Called either after a reply is sent,
    or when the agent judges no reply is needed -- but NOT when the
    user explicitly skips a needs-reply email, since that should stay
    pending for a future run.
    """
    service.users().messages().modify(
        userId="me",
        id=email_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()


def send_notification(message_text):
    """
    Pings a Telegram chat -- a real notification channel separate from
    terminal output, so you'd know a draft is ready even if you're not
    watching the script run.

    Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment
    variables. Fails silently (prints a warning) rather than crashing
    the whole agent if Telegram is unreachable -- a notification
    failing shouldn't take down the core email-handling logic.
    """
    import requests

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[Notification skipped -- TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set]")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": message_text}, timeout=10)
    except Exception as e:
        print(f"[Notification failed -- {e}]")


def draft_reply(email, max_retries=3):
    """
    This is the actual 'thinking' step -- the LLM reads the email and
    decides what a good reply looks like. No tool involved here, this
    is pure language generation.

    Returns a dict: {"needs_reply": bool, "reply_text": str}
    so the main loop can decide what to do without re-parsing prose.

    Includes basic retry/backoff: free-tier APIs have rate limits, and
    a real agent should handle that gracefully rather than crash --
    this matters in production too, not just while testing.
    """
    import time
    import json

    prompt = f"""You are helping draft an email reply. Here is the email:

From: {email['from']}
Subject: {email['subject']}
Body:
{email['body']}

Decide if this email genuinely needs a reply (e.g. a real question, an
invitation, a request) versus if it's a notification/newsletter that
doesn't need one (e.g. LinkedIn activity updates, newsletters, automated alerts).

Respond ONLY in this exact JSON format, nothing else, no markdown fences:
{{"needs_reply": true or false, "reply_text": "the drafted reply, or empty string if no reply needed"}}
"""

    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(model=MODEL, contents=prompt)
            text = response.text.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            return json.loads(text)
        except json.JSONDecodeError:
            # Model didn't return clean JSON -- treat as no reply needed
            return {"needs_reply": False, "reply_text": ""}
        except Exception as e:
            is_rate_limit = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            is_overloaded = "503" in str(e) or "UNAVAILABLE" in str(e)
            if is_rate_limit or is_overloaded:
                wait = 5 * (attempt + 1)  # simple backoff: 5s, 10s, 15s
                reason = "Rate limited" if is_rate_limit else "Server overloaded"
                print(f"[{reason} -- waiting {wait}s before retry {attempt + 1}/{max_retries}]")
                time.sleep(wait)
            else:
                raise  # not a known transient issue, don't hide other errors

    # All retries exhausted -- skip this email rather than crash the whole run
    print("[Gave up on this email after repeated rate-limit errors -- skipping]")
    return {"needs_reply": False, "reply_text": ""}


if __name__ == "__main__":
    service = get_gmail_service()
    emails = fetch_unread_emails(service)

    if not emails:
        print("No unread messages found.")
    else:
        for email in emails:
            print("=" * 60)
            print(f"From: {email['from']}")
            print(f"Subject: {email['subject']}")
            print("-" * 60)

            result = draft_reply(email)

            if not result["needs_reply"]:
                print("[Agent]: No reply needed -- looks like a notification/newsletter.")
                mark_as_read(service, email["id"])
                print()
                continue

            print(f"[Agent's draft reply]:\n{result['reply_text']}")
            print()

            send_notification(
                f"📧 Draft reply ready for: {email['subject']}\n"
                f"From: {email['from']}\n\n"
                f"Draft:\n{result['reply_text']}\n\n"
                f"Go to your terminal to approve or skip."
            )

            # --- Manual approval gate ---
            # The LLM only ever WRITES the reply. Sending is a real action
            # on the world, so a human confirms before the send tool fires.
            choice = input("Send this reply? (y/n): ").strip().lower()
            if choice == "y":
                send_reply(service, email, result["reply_text"])
                mark_as_read(service, email["id"])
                print("[Sent]")
            else:
                # Left unread on purpose -- you chose not to act on this
                # one yet, so it should come up again on the next run.
                print("[Skipped -- not sent, left unread for next time]")
            print()