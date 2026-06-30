"""
Step 1 of the email agent: prove Gmail access works.

This is NOT the agent yet -- it's just the "tool" piece: a function
that fetches real emails. We get this working on its own first,
before wiring an LLM on top of it.

Setup:
    pip install google-auth google-auth-oauthlib google-api-python-client
    Put your downloaded credentials.json in this same folder.

First run: a browser window opens, you log in and approve access.
After that, a token.json file is saved so you won't be asked again.
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Read-only scope to start -- safest option while testing.
# We'll add send/reply permission later, once reading works.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def get_gmail_service():
    """Handles the OAuth login flow and returns an authenticated Gmail client."""
    creds = None

    # Reuse saved login if we have one
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    # If no valid saved login, do the browser login flow
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            # Fixed port (not random) so we can troubleshoot WSL<->Windows
            # localhost routing reliably. Also print the URL ourselves
            # since gio (Linux's browser-opener) doesn't exist in WSL.
            creds = flow.run_local_server(
                port=8080,
                open_browser=False,
            )

        # Save it so next time we skip the browser step
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def list_recent_unread(service, max_results=5):
    """Fetches a handful of unread emails -- just subject + sender, to keep it simple."""
    results = service.users().messages().list(
        userId="me", labelIds=["UNREAD"], maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    if not messages:
        print("No unread messages found.")
        return

    print(f"Found {len(messages)} unread message(s):\n")
    for msg in messages:
        msg_data = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject"]
        ).execute()

        headers = {h["name"]: h["value"] for h in msg_data["payload"]["headers"]}
        print(f"From: {headers.get('From', '(unknown)')}")
        print(f"Subject: {headers.get('Subject', '(no subject)')}")
        print("-" * 40)


if __name__ == "__main__":
    service = get_gmail_service()
    list_recent_unread(service)