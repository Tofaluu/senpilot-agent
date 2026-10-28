# UARB Document Agent

An email agent that fetches regulatory filings from the [Nova Scotia UARB public documents database](https://uarb.novascotia.ca/fmi/webd/UARB15).

Email **tofalu-6976@agentmail.to** something like:

> Hi Agent, can you give me Other Documents files from M12205? Thanks!

and it replies in the same thread with a summary of the matter, a count of every document type, and up to 10 documents as a ZIP.

## How it works

```
email ─► AgentMail inbox ─► agent.py ─► Claude reads the request ─► scraper.py (Playwright)
                                                                        │
reply ◄─ AgentMail ◄─ mail.py ◄─ report.py (ZIP + summary) ◄────────────┘
```

| File | Responsibility |
|---|---|
| `agent.py` | Polls the inbox, uses Claude (structured output) to extract `{matter, doc_type}`, sends an instant acknowledgement, runs up to 3 requests concurrently, replies with results or friendly errors |
| `scraper.py` | Drives Chromium through the FileMaker WebDirect site: search, metadata, tab counts, download via the "Go Get It" dialog |
| `report.py` | Packs files into ≤4 MB ZIPs (AgentMail's request limit) and writes the summary email from a template |
| `mail.py` | AgentMail send/reply with attachments |

Design notes:

- **AI only where input is messy.** Claude interprets the email; every number in the reply comes from deterministic code.
- **Email is untrusted.** Claude has no tools, only fills a schema, and the code re-validates it (`M` + 5 digits, known doc type) before acting.
- **The scraper verifies what it scraped.** It checks the page shows the requested matter before trusting it (an early bug silently opened M00001).
- **Stateless dedup.** An email counts as handled if the agent has replied to it (`in_reply_to`), so restarts and redeploys never double-answer.
- **Large filings** are split across several ZIPs in the same thread rather than failing.

## Run locally

```bash
uv sync
uv run playwright install chromium
cp .env.example .env   # fill in keys
uv run python agent.py                                   # the agent
uv run python scraper.py M12205 "Key Documents" --show   # just the scraper, with a visible browser
```

## Deploy

The `Dockerfile` builds on the official Playwright image; `railway.toml` runs it as an always-restarting worker. Set `AGENTMAIL_API_KEY`, `AGENTMAIL_INBOX` and `ANTHROPIC_API_KEY` as environment variables on the host.

`recon*.py` are the exploration scripts used to reverse-engineer the site before writing the scraper.
