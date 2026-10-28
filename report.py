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

    if total == 0:
        lines.append(f"Since there are no {r.doc_type} in this matter, there's nothing attached.")
    elif n == 0:
        lines.append(f"Unfortunately I wasn't able to download any of the {r.doc_type}. Please try again in a few minutes.")
    elif n == total:
        lines.append(f"I downloaded all {_count(n, r.doc_type)} and am attaching them {attaching}.")
    else:
        lines.append(f"I downloaded {n} out of the {_count(total, r.doc_type)} and am attaching them {attaching}.")

    if skipped:
        lines.append(f"({len(skipped)} file(s) are too large to email on their own, so they aren't attached: "
                     f"{', '.join(f.name for f in skipped)}.)")
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


def compose_error(message: str, greeting_name: str = "") -> str:
    return "\n".join([f"Hi {greeting_name or 'there'},", "", message, "", "Best,", "UARB Document Agent"])
