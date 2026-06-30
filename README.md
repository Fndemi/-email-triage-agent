# Email Triage Agent

A small AI agent that reads my unread Gmail messages and uses an LLM (Google Gemini) to decide which ones actually need a reply — and drafts one when they do.

Built as a hands-on way to learn how AI agents actually work under the hood: tool calling, connecting a model to a real external API, and the decide-act-observe loop, rather than just prompting a chatbot.

## What it does

1. Connects to Gmail via the Gmail API (OAuth 2.0) and fetches unread messages, including the full email body.
2. Sends each email to Gemini, which judges whether it's a genuine message needing a reply (a real question, invitation, or request) versus a notification/newsletter that doesn't.
3. For emails that need a reply, it drafts one and prints it to the terminal.

**Note:** this version only drafts replies — it does not send anything automatically. That's a deliberate safety choice while the project is still being tested; auto-sending is a planned next step once draft quality is trustworthy.

## How it works

The project separates two roles, which is the core idea behind any agent:

- **Tools** — code that does real actions in the world. Here, the Gmail API tool fetches emails (and later, will send replies).
- **The LLM** — does the "thinking": reads the email content and decides what's needed, then writes the reply text. It never touches Gmail directly; it only produces text, which the tool layer then acts on.

A small driver loop ties them together: fetch unread emails → for each one, ask the LLM what to do → print the result.

## Stack

- **Python**
- **Gmail API** (OAuth 2.0, `google-api-python-client`) — read access to inbox
- **Google Gemini API** (`google-genai`, free tier, `gemini-2.5-flash`) — drafts replies

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install google-auth google-auth-oauthlib google-api-python-client google-genai
```

You'll need:
- A Gmail API OAuth client (`credentials.json`) from [Google Cloud Console](https://console.cloud.google.com) — enable the Gmail API, create an OAuth Client ID (type: Desktop app).
- A free Gemini API key from [Google AI Studio](https://aistudio.google.com).

```bash
export GEMINI_API_KEY="your-key-here"
python email_agent.py
```

The first run will open a browser-based Google login to authorize Gmail access. After that, a `token.json` is saved so you won't be asked again.

## Roadmap

- [x] Read unread emails (headers + body) via Gmail API
- [x] LLM judges reply-worthiness and drafts a reply
- [ ] Add a send-reply tool, with a manual approval step before sending
- [ ] Add a notification step (e.g. ping when a draft is ready for review)
- [ ] Turn the single pass into a proper agent loop that can re-check or take multiple steps per email

## Why I built this

I wanted to move from *using* AI tools to actually *building* with them — understanding tool calling and the agent loop well enough to explain and extend it, not just prompt a chatbot. This project also solves something I actually deal with: too many unread emails, most of which don't need my attention, a few of which do.