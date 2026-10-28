"""Scrapes the Nova Scotia UARB public documents database (FileMaker WebDirect)."""

import logging
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page, sync_playwright

log = logging.getLogger("scraper")
URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
BLOCKED = {"image", "font", "media"}
DOC_TYPES = ["Exhibits", "Key Documents", "Other Documents", "Transcripts", "Recordings"]
MATTER_RE = re.compile(r"^M\d{5}$")


class MatterNotFound(Exception):
    pass


class Cancelled(Exception):
    """The request was superseded (e.g. the user sent a correction) before it finished."""


@dataclass
class Document:
    doc_no: str
    title: str
    date: str
    files: list[Path] = field(default_factory=list)


@dataclass
class MatterResult:
    matter: str
    doc_type: str
    title: str = ""
    status: str = ""
    type: str = ""
    category: str = ""
    date_received: str = ""
    date_final: str = ""
    outcome: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    documents: list[Document] = field(default_factory=list)

    @property
    def files(self) -> list[Path]:
        return [f for d in self.documents for f in d.files]


# Collects every visible element that directly owns text, with its position.
# FileMaker renders values in absolutely positioned divs, so position is the only reliable structure.
TEXT_NODES_JS = """
() => {
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const text = [...el.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim();
    if (!text) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    out.push({text, x: r.x, y: r.y, width: r.width, height: r.height, cls: el.className.toString()});
  }
  return out;
}
"""


def _click(page: Page, box: dict) -> None:
    # FileMaker overlays transparent divs on top of widgets, so element.click() is intercepted.
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)


def _search(page: Page, matter: str) -> None:
    # Not "networkidle": FileMaker's push channel and heartbeats keep the network busy, so wait for the UI instead.
    page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    page.get_by_text("eg M01234").wait_for(timeout=45000)

    _click(page, page.get_by_text("eg M01234").bounding_box())
    # The placeholder is swapped for a contenteditable on focus; typing earlier is silently lost.
    page.wait_for_function("document.activeElement && document.activeElement.isContentEditable", timeout=10000)
    # On a slow CPU FileMaker can drop keystrokes, and a truncated number searches as "No Records Found".
    for _ in range(3):
        page.keyboard.press("Control+A")
        page.keyboard.type(matter, delay=30)
        page.wait_for_timeout(500)
        typed = page.evaluate("document.activeElement ? document.activeElement.innerText.trim() : ''")
        if typed == matter:
            break
        log.warning("typed %r but field shows %r; retyping", matter, typed)
    else:
        raise RuntimeError(f"could not enter {matter} into the search box")

    box_y = page.get_by_text("Go Directly to Matter").bounding_box()["y"]
    btn = min((b for b in page.get_by_text("Search", exact=True).all() if b.bounding_box()["y"] > box_y),
              key=lambda b: b.bounding_box()["y"])
    _click(page, btn.bounding_box())

    page.wait_for_function(
        "document.body.innerText.includes('Exhibits -') || document.body.innerText.includes('No Records Found')",
        timeout=30000,
    )
    if "No Records Found" in page.inner_text("body"):
        raise MatterNotFound(matter)


def _parse_header(page: Page, result: MatterResult) -> None:
    nodes = page.evaluate(TEXT_NODES_JS)
    tabs_y = min(n["y"] for n in nodes if n["text"].startswith("Exhibits -"))
    labels = {
        "matter": "Matter No", "title": "Title - Description", "type": "Type",
        "date_received": "Date Received", "date_final": ("Date Final", "Decision Date"), "outcome": "Outcome",
    }
    label_pos = {}
    for key, prefixes in labels.items():
        for n in nodes:
            if n["y"] < tabs_y and n["text"].startswith(prefixes):
                label_pos[key] = n
                break
    label_y = max(n["y"] for n in label_pos.values())
    values = [n for n in nodes if label_y + 15 < n["y"] < tabs_y and n not in label_pos.values()
              and "Back to Search" not in n["text"]]

    # Each value belongs to the label whose left edge is nearest; the second row holds Status/Category.
    columns: dict[str, list[dict]] = {k: [] for k in label_pos}
    for v in values:
        key = min(label_pos, key=lambda k: abs(label_pos[k]["x"] - v["x"]))
        columns[key].append(v)
    for col in columns.values():
        col.sort(key=lambda n: n["y"])

    def first(key, idx=0):
        col = [n for n in columns.get(key, []) if "fm-text-character" not in n["cls"] or key == "title"]
        return col[idx]["text"] if len(col) > idx else ""

    shown = first("matter")
    if shown != result.matter:
        raise RuntimeError(f"Expected {result.matter} but page shows {shown!r}")
    result.status = first("matter", 1)
    result.type = first("type")
    result.category = first("type", 1)
    result.title = " ".join(n["text"] for n in columns.get("title", []))
    result.date_received = first("date_received")
    result.date_final = first("date_final")
    result.outcome = first("outcome")

    body = page.inner_text("body")
    for name in DOC_TYPES:
        m = re.search(rf"{name} - (\d+)", body)
        result.counts[name] = int(m.group(1)) if m else 0


def _list_documents(page: Page, doc_type: str, limit: int) -> list[tuple[Document, dict]]:
    tab = page.get_by_text(f"{doc_type} -", exact=False).first
    _click(page, tab.bounding_box())
    page.get_by_text("GO GET IT").first.wait_for(timeout=30000)
    page.wait_for_timeout(1000)

    nodes = page.evaluate(TEXT_NODES_JS)
    tabs_bottom = max(n["y"] for n in nodes if n["text"].startswith("Exhibits -")) + 30
    buttons = sorted((n for n in nodes if n["text"] == "GO GET IT"), key=lambda n: n["y"])
    # Doc numbers vary by tab (102674, A-1, "A -5"), so identify them by column, not format.
    left_col = [n for n in nodes if n["y"] > tabs_bottom and n["x"] < 100]

    rows = []
    for btn in buttons[:limit]:
        # The doc number is one line above its GO GET IT button; the security label shares the button's line.
        above = [d for d in left_col if 15 < btn["y"] - d["y"] < 60]
        if not above:
            continue
        num = max(above, key=lambda d: d["y"])
        same_line = [n for n in nodes if abs(n["y"] - num["y"]) < 8 and n is not num]
        date = next((n["text"] for n in same_line if re.fullmatch(r"\d{2}/\d{2}/\d{4}", n["text"])), "")
        title_parts = [n for n in same_line if num["x"] < n["x"] < btn["x"] - 50
                       and not re.fullmatch(r"\.\w+|\d{2}/\d{2}/\d{4}", n["text"])]
        title = " ".join(n["text"] for n in sorted(title_parts, key=lambda n: n["x"]))
        rows.append((Document(re.sub(r"\s+", "", num["text"]), title, date), btn))
    return rows


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()[:100]


def _download(page: Page, doc: Document, btn: dict, out_dir: Path) -> None:
    _click(page, btn)
    page.locator(".fm-download-button").first.wait_for(timeout=30000)
    # The dialog can list several files for one document.
    for i in range(page.locator(".fm-download-button").count()):
        with page.expect_download(timeout=120000) as info:
            page.locator(".fm-download-button").nth(i).click()
        dl = info.value
        ext = Path(dl.suggested_filename).suffix
        stem = _safe(f"{doc.doc_no} - {doc.title}") if doc.title else doc.doc_no
        target = out_dir / f"{stem}{'' if i == 0 else f' ({i + 1})'}{ext}"
        dl.save_as(target)
        doc.files.append(target)
    page.get_by_text("Close", exact=True).click()
    page.locator(".fm-download-button").first.wait_for(state="detached", timeout=15000)


def fetch_matter(matter: str, doc_type: str, out_dir: Path, limit: int = 10, headless: bool = True,
                 cancelled: Callable[[], bool] = lambda: False) -> MatterResult:
    matter = matter.strip().upper()
    if not MATTER_RE.match(matter):
        raise ValueError(f"Invalid matter number: {matter}")
    if doc_type not in DOC_TYPES:
        raise ValueError(f"Unknown document type: {doc_type}")
    out_dir.mkdir(parents=True, exist_ok=True)
    result = MatterResult(matter=matter, doc_type=doc_type)

    t0 = time.perf_counter()

    def mark(stage: str) -> None:
        log.info("%s %s: %s at %.1fs", matter, doc_type, stage, time.perf_counter() - t0)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            for attempt in range(3):
                # Fresh context each try: the site resumes the previous session's layout on reload.
                ctx = browser.new_context(viewport={"width": 1400, "height": 2400}, accept_downloads=True)
                # Logos and fonts aren't needed to read or click anything.
                ctx.route("**/*", lambda route: route.abort() if route.request.resource_type in BLOCKED
                          else route.continue_())
                page = ctx.new_page()
                if cancelled():
                    raise Cancelled(matter)
                try:
                    _search(page, matter)
                    mark("search done")
                    _parse_header(page, result)
                    mark("header parsed")
                    break
                except MatterNotFound:
                    # Only report "not found" if a fresh session agrees; a false negative tells the user a real matter doesn't exist.
                    ctx.close()
                    if attempt >= 1:
                        raise
                    log.warning("%s not found on attempt %d; confirming with a fresh session", matter, attempt + 1)
                except Exception as e:
                    ctx.close()
                    if attempt == 2:
                        raise
                    log.warning("attempt %d failed: %s; retrying", attempt + 1, e)

            if result.counts.get(doc_type, 0) > 0:
                rows = _list_documents(page, doc_type, limit)
                mark(f"listed {len(rows)} docs")
                for doc, btn in rows:
                    if cancelled():
                        raise Cancelled(matter)
                    try:
                        _download(page, doc, btn, out_dir)
                    except Exception as e:
                        log.warning("failed to download %s: %s", doc.doc_no, e)
                    result.documents.append(doc)
                mark(f"downloaded {len(result.files)} files")
        finally:
            browser.close()
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    m = args[0] if len(args) > 0 else "M12205"
    t = args[1] if len(args) > 1 else "Other Documents"
    r = fetch_matter(m, t, Path(args[2] if len(args) > 2 else "downloads") / m, headless="--show" not in sys.argv)
    print(f"{r.matter}: {r.title}")
    print(f"  status={r.status} type={r.type} category={r.category}")
    print(f"  received={r.date_received} final={r.date_final} outcome={r.outcome}")
    print(f"  counts={r.counts}")
    for d in r.documents:
        print(f"  [{d.doc_no}] {d.date} {d.title} -> {[f.name for f in d.files]}")
