import pytest

from scraper import MATTER_RE, fetch_matter


@pytest.mark.parametrize("matter, ok", [("M12205", True), ("M1220", False), ("M122061", False), ("12205", False)])
def test_matter_format(matter, ok):
    assert bool(MATTER_RE.match(matter)) is ok


@pytest.mark.parametrize("matter, doc_type", [("12205", "Exhibits"), ("M12205", "PDFs")])
def test_bad_input_rejected_before_launching_a_browser(tmp_path, matter, doc_type):
    with pytest.raises(ValueError):
        fetch_matter(matter, doc_type, tmp_path)
