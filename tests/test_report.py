import os
import zipfile

import pytest

import report
from report import _counts_sentence, _date, compose, compose_help, make_zips
from scraper import DOC_TYPES, Document, MatterResult


def _files(tmp_path, sizes):
    paths = []
    for i, size in enumerate(sizes):
        p = tmp_path / f"{i:02d} - doc.pdf"
        p.write_bytes(os.urandom(size))  # random bytes don't compress, like real PDFs
        paths.append(p)
    return paths


def _result(doc_type="Other Documents", counts=None, files=()):
    r = MatterResult(
        "M12205", doc_type, title="Halifax Regional Water Commission - Windsor Street Exchange", status="Closed",
        type="Water", category="Capital Expenditure Approvals", date_received="04/07/2025", date_final="10/23/2025",
        outcome="Directions given",
        counts=counts if counts is not None else {"Exhibits": 13, "Key Documents": 6, "Other Documents": 43},
    )
    r.documents = [Document(f.stem, "", "", [f]) for f in files]
    return r


@pytest.mark.parametrize("counts, expected", [
    ({"Exhibits": 13, "Key Documents": 6, "Other Documents": 43},
     "I found 13 Exhibits, 6 Key Documents, 43 Other Documents, and no Transcripts or Recordings."),
    ({"Exhibits": 1}, "I found 1 Exhibit and no Key Documents, Other Documents, Transcripts, or Recordings."),
    ({t: 2 for t in DOC_TYPES}, "I found 2 Exhibits, 2 Key Documents, 2 Other Documents, 2 Transcripts, and 2 Recordings."),
    ({}, "I didn't find any documents in this matter."),
])
def test_counts_sentence_matches_brief_style(counts, expected):
    assert _counts_sentence(counts) == expected


def test_dates_are_humanized():
    assert _date("04/07/2025") == "April 7, 2025"
    assert _date("not a date") == "not a date"


def test_summary_partial_download(tmp_path):
    files = _files(tmp_path, [1000] * 10)
    r = _result(files=files)
    parts, skipped = make_zips(r, tmp_path / "out")
    subject, body = compose(r, parts, skipped, "Paula")
    assert subject == "M12205 – Other Documents (10 of 43)"
    assert body.startswith("Hi Paula,")
    assert "It relates to Capital Expenditure Approvals within the Water category." in body
    assert "initial filing on April 7, 2025 and a final filing on October 23, 2025" in body
    assert "I downloaded 10 out of the 43 Other Documents and am attaching them as a ZIP here." in body


def test_summary_all_downloaded(tmp_path):
    r = _result("Key Documents", files=_files(tmp_path, [1000] * 6))
    parts, skipped = make_zips(r, tmp_path / "out")
    assert "I downloaded all 6 Key Documents and am attaching them as a ZIP here." in compose(r, parts, skipped)[1]


def test_summary_empty_tab(tmp_path):
    r = _result("Transcripts")
    parts, skipped = make_zips(r, tmp_path / "out")
    _, body = compose(r, parts, skipped)
    assert parts == [] and "Since there are no Transcripts in this matter, there's nothing attached." in body


def test_oversized_file_is_named_not_contradicted(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "MAX_ZIP_BYTES", 5000)
    files = _files(tmp_path, [9000] + [1000] * 5)
    r = _result("Exhibits", counts={"Exhibits": 6}, files=files)
    parts, skipped = make_zips(r, tmp_path / "out")
    _, body = compose(r, parts, skipped)
    assert skipped == [files[0]]
    assert "I downloaded all 6 Exhibits. 5 of them are attached as a ZIP here; 1 is too large to email" in body
    assert "You can get it from the UARB site: search M12205, open the Exhibits tab" in body
    assert "am attaching them" not in body  # the original bug: claiming all were attached


def test_multi_part_is_announced(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "MAX_ZIP_BYTES", 2500)
    r = _result(files=_files(tmp_path, [2000, 2000]))
    parts, skipped = make_zips(r, tmp_path / "out")
    assert len(parts) == 2
    assert "split across 2 emails" in compose(r, parts, skipped)[1]


def test_bin_packing_uses_fewest_zips_and_keeps_order(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "MAX_ZIP_BYTES", 10_000)
    # In listing order this packs as 6 | 5+4 | 5 (3 ZIPs); biggest-first packs 6+4 | 5+5 (2 ZIPs).
    files = _files(tmp_path, [6000, 5000, 4000, 5000])
    parts, skipped = make_zips(_result(files=files), tmp_path / "out")
    assert len(parts) == 2 and not skipped
    assert sorted(f for p in parts for f in p.files) == sorted(files)
    for p in parts:
        assert p.files == sorted(p.files)  # listing order preserved within each ZIP
        assert p.path.stat().st_size <= 10_000 + 1000
        with zipfile.ZipFile(p.path) as zf:
            assert sorted(zf.namelist()) == sorted(f.name for f in p.files)


def test_help_lists_every_doc_type():
    body = compose_help()
    assert body.startswith("Hi there,")
    assert all(t in body for t in DOC_TYPES) and "M12205" in body
