"""Thin wrapper around AgentMail for sending and replying with a ZIP attachment."""

import base64
import os
import sys
from pathlib import Path

from agentmail import AgentMail
from dotenv import load_dotenv

load_dotenv()
INBOX = os.environ.get("AGENTMAIL_INBOX", "")
client = AgentMail(api_key=os.environ.get("AGENTMAIL_API_KEY", ""))


def _attachments(path: Path | None) -> list[dict] | None:
    if not path:
        return None
    return [{
        "filename": path.name,
        "content_type": "application/zip",
        "content": base64.b64encode(path.read_bytes()).decode(),
    }]


def send(to: str, subject: str, text: str, attachment: Path | None = None):
    return client.inboxes.messages.send(INBOX, to=to, subject=subject, text=text, attachments=_attachments(attachment))


def reply(message_id: str, text: str, attachment: Path | None = None, to: str | None = None):
    # Replying to our own sent message would default back to us, so callers can pin the recipient.
    extra = {"to": to} if to else {}
    return client.inboxes.messages.reply(INBOX, message_id, text=text, attachments=_attachments(attachment), **extra)


if __name__ == "__main__":
    # Smoke test: scrape a real matter and email the result to TEST_RECIPIENT.
    from report import compose, compose_part, make_zips
    from scraper import fetch_matter

    matter = sys.argv[1] if len(sys.argv) > 1 else "M12205"
    doc_type = sys.argv[2] if len(sys.argv) > 2 else "Other Documents"
    out = Path("downloads") / f"{matter}-{doc_type.replace(' ', '_')}"
    result = fetch_matter(matter, doc_type, out)
    parts, skipped = make_zips(result, out)
    subject, body = compose(result, parts, skipped)
    print(subject, "\n\n", body, sep="")
    resp = send(os.environ["TEST_RECIPIENT"], subject, body, parts[0].path if parts else None)
    print("\nSent part 1:", resp.message_id)
    for i in range(1, len(parts)):
        r = reply(resp.message_id, compose_part(result, parts, i), parts[i].path, to=os.environ["TEST_RECIPIENT"])
        print(f"Sent part {i + 1}:", r.message_id)
