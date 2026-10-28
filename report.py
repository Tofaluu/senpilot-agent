"""Turns a MatterResult into a ZIP and a human-readable summary email."""

import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from scraper import DOC_TYPES, MatterResult

# AgentMail rejects request bodies over ~6MB (413 observed at 6MB of zip), and base64 inflates attachments by a third.
MAX_ZIP_BYTES = 4 * 1024 * 1024

SINGULAR = {
    "Exhibits": "Exhibit", "Key Documents": "Key Document", "Other Documents": "Other Document",
    "Transcripts": "Transcript", "Recordings": "Recording",
}


@dataclass
class ZipPart:
    path: Path
    files: list[Path]


def make_zips(result: MatterResult, base: Path) -> tuple[list[ZipPart], list[Path]]:
    """Packs files into as many ZIPs as needed to stay under the size cap, each sent as its own email.

    Returns (parts, skipped); a file is skipped only if it alone exceeds the cap.
    """
    # First-fit decreasing bin packing; PDFs barely compress, so raw size is a safe upper bound.
    order = {f: i for i, f in enumerate(result.files)}
    groups: list[list[Path]] = []
    sizes: list[int] = []
    skipped = []
    for f in sorted(result.files, key=lambda f: f.stat().st_size, reverse=True):
        fsize = f.stat().st_size
        if fsize > MAX_ZIP_BYTES:
            skipped.append(f)
            continue
        i = next((i for i, s in enumerate(sizes) if s + fsize <= MAX_ZIP_BYTES), None)
        if i is None:
            groups.append([])
            sizes.append(0)
            i = len(groups) - 1
        groups[i].append(f)
        sizes[i] += fsize
    # Keep each part in the site's listing order so the email reads naturally.
    groups = [sorted(g, key=order.get) for g in groups]

    parts = []
    for i, files in enumerate(groups, 1):
        path = base.with_name(f"{base.name}{f'-part{i}' if len(groups) > 1 else ''}.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                zf.write(f, arcname=f.name)
        parts.append(ZipPart(path, files))
    return parts, skipped


def _date(s: str) -> str:
    try:
        d = datetime.strptime(s, "%m/%d/%Y")
        return f"{d:%B} {d.day}, {d.year}"
    except ValueError:
        return s


def _count(n: int, doc_type: str) -> str:
    return f"{n} {SINGULAR[doc_type] if n == 1 else doc_type}"


def _join(items: list[str], conj: str = "and") -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])}, {conj} {items[-1]}" if len(items) > 2 else f"{items[0]} {conj} {items[1]}"


def _counts_sentence(counts: dict[str, int]) -> str:
    present = [_count(counts[t], t) for t in DOC_TYPES if counts.get(t)]
    absent = [t for t in DOC_TYPES if not counts.get(t)]
    if not present:
        return "I didn't find any documents in this matter."
    if absent:
        return f"I found {_join(present + [f'no {_join(absent, 'or')}'])}."
    return f"I found {_join(present)}."


def compose(result: MatterResult, parts: list[ZipPart], skipped: list[Path], greeting_name: str = "") -> tuple[str, str]:
    """Body for the first email: full summary, with parts[0] attached."""
    r = result
    total = r.counts.get(r.doc_type, 0)
    n = sum(len(p.files) for p in parts) + len(skipped)
    attaching = "as a ZIP here" if len(parts) <= 1 else (
        f"as ZIPs split across {len(parts)} emails (each email is limited to about 4 MB). This is part 1 of {len(parts)}")
    lines = [f"Hi {greeting_name or 'there'},", ""]

    about = f"{r.matter} is about the {r.title}." if r.title else f"Here's what I found for {r.matter}."
    if r.category and r.type:
        about += f" It relates to {r.category} within the {r.type} category."
    if r.date_received and r.date_final:
        about += f" The matter had an initial filing on {_date(r.date_received)} and a final filing on {_date(r.date_final)}."
    elif r.date_received:
        about += f" The matter had an initial filing on {_date(r.date_received)}."
    if r.status and r.outcome:
        about += f' It is currently {r.status}, with an outcome of "{r.outcome}".'
    elif r.status:
        about += f" It is currently {r.status}."
    lines.append(about + " " + _counts_sentence(r.counts))
    lines.append("")

    attached = n - len(skipped)
    got = f"all {_count(n, r.doc_type)}" if n == total else f"{n} out of the {_count(total, r.doc_type)}"
    if total == 0:
        lines.append(f"Since there are no {r.doc_type} in this matter, there's nothing attached.")
    elif n == 0:
        lines.append(f"Unfortunately I wasn't able to download any of the {r.doc_type}. Please try again in a few minutes.")
    elif not skipped:
        lines.append(f"I downloaded {got} and am attaching them {attaching}.")
    else:
        one = len(skipped) == 1
        too_big = "too large to email (each email is limited to about 4 MB)"
        if attached:
            lines.append(f"I downloaded {got}. {attached} of them {'is' if attached == 1 else 'are'} attached {attaching}; "
                         f"{len(skipped)} {'is' if one else 'are'} {too_big}:")
        else:
            lines.append(f"I downloaded {got}, but {'it is' if one else 'they are'} {too_big}:")
        lines += [f"  • {f.name} ({f.stat().st_size / 2**20:.1f} MB)" for f in skipped]
        lines.append(f"You can get {'it' if one else 'them'} from the UARB site: search {r.matter}, open the "
                     f"{r.doc_type} tab, and click GO GET IT.")
    failed = [d for d in r.documents if not d.files]
    if failed:
        lines.append(f"(I couldn't download: {', '.join(f'{d.doc_no} {d.title}' for d in failed)}.)")

    if parts:
        lines += ["", f"Attached ({parts[0].path.name}):"] + [f"  • {f.name}" for f in parts[0].files]

    lines += ["", "Best,", "UARB Document Agent"]
    subject = f"{r.matter} – {r.doc_type}" + (f" ({n} of {total})" if total else "")
    return subject, "\n".join(lines)


def compose_part(result: MatterResult, parts: list[ZipPart], i: int) -> str:
    """Body for follow-up emails carrying parts[1:]."""
    p = parts[i]
    return "\n".join([
        f"Part {i + 1} of {len(parts)} of the {result.doc_type} for {result.matter}.", "",
        f"Attached ({p.path.name}):", *[f"  • {f.name}" for f in p.files], "", "UARB Document Agent",
    ])


def compose_help(greeting_name: str = "") -> str:
    return "\n".join([
        f"Hi {greeting_name or 'there'},", "",
        "I'm an email agent for the Nova Scotia Utility and Review Board (UARB) public documents database. Send me "
        "a matter number and a document type, and I'll reply with a summary of the matter, a count of all its "
        "documents, and up to 10 of the documents you asked for as a ZIP.", "",
        "For example:",
        '  "Can you send me the Other Documents for M12205?"', "",
        "Matter numbers are the letter M followed by 5 digits. Document types are: " + _join(DOC_TYPES) + ". "
        "If you change your mind, just reply in the same thread (e.g. \"sorry, I meant Key Documents\") and I'll switch.",
        "", "Best,", "UARB Document Agent",
    ])


def compose_ack(message: str) -> str:
    # A quick "on it" note reads naturally without a greeting; the full reply that follows has one.
    return f"{message}\n\nUARB Document Agent"


def compose_error(message: str, greeting_name: str = "") -> str:
    return "\n".join([f"Hi {greeting_name or 'there'},", "", message, "", "Best,", "UARB Document Agent"])
