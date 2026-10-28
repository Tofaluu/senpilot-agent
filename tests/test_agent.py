from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import agent
import mail
from agent import DocRequest, Understanding, help_allowed, is_automated

T0 = datetime(2026, 10, 28, 6, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("addr, headers, expected", [
    ("paula@senpilot.com", {"X-Received": "x"}, False),
    ("paula@senpilot.com", {"Auto-Submitted": "auto-replied"}, True),
    ("paula@senpilot.com", {"auto-submitted": "Auto-Generated"}, True),   # header names are case-insensitive
    ("bob@x.com", {"Auto-Submitted": "no"}, False),                       # RFC 3834: "no" means a human sent it
    ("news@bigco.com", {"List-Unsubscribe": "<mailto:u@x>"}, True),
    ("alerts@bigco.com", {"Precedence": "bulk"}, True),
    ("noreply@github.com", {}, True),
    ("no-reply@service.com", {}, True),
    ("MAILER-DAEMON@google.com", {}, True),
    ("noreen@senpilot.com", {}, False),                                   # a real name, not "noreply"
])
def test_is_automated(addr, headers, expected):
    assert is_automated(addr, headers) is expected


def test_help_cooldown_is_per_sender(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(agent.time, "time", lambda: now[0])
    assert help_allowed("a@x.com")
    assert not help_allowed("a@x.com")
    assert help_allowed("b@x.com")
    now[0] += agent.HELP_COOLDOWN_SECONDS + 1
    assert help_allowed("a@x.com")


def understanding(**kw):
    base = dict(is_request=True, wants_help=False, sender_first_name="", requests=[], replaces_previous=False,
                clarification="")
    return Understanding(**{**base, **kw})


@pytest.fixture
def inbox(monkeypatch):
    """Fakes AgentMail and the scraper; records what the agent would have sent and fetched."""
    state = SimpleNamespace(messages={}, replies=[], fulfilled=[], understood=[], verdicts={})

    def get(_inbox, msg_id):
        return state.messages[msg_id]

    def fake_understand(subject, body):
        state.understood.append(body)
        return state.verdicts[body]

    monkeypatch.setattr(mail, "client", SimpleNamespace(inboxes=SimpleNamespace(messages=SimpleNamespace(get=get))))
    monkeypatch.setattr(mail, "reply", lambda msg_id, text, attachment=None, to=None: state.replies.append((msg_id, text)))
    monkeypatch.setattr(agent, "understand", fake_understand)
    monkeypatch.setattr(agent, "fulfil", lambda msg_id, req, name, stale: state.fulfilled.append((msg_id, req, name, stale)))

    def email(msg_id, body, verdict, sender="Paula Prado <paula@senpilot.com>", headers=None, thread="t1", at=T0):
        state.messages[msg_id] = SimpleNamespace(
            from_=sender, subject="Request", headers=headers or {}, text=body, extracted_text=None, preview=None,
            thread_id=thread, timestamp=at)
        state.verdicts[body] = verdict
        agent.handle(msg_id)

    state.email = email
    return state


def test_automated_mail_never_reaches_the_model(inbox):
    inbox.email("m1", "I am out of the office", understanding(is_request=False),
                headers={"Auto-Submitted": "auto-replied"})
    assert inbox.understood == [] and inbox.replies == []


def test_own_mail_is_ignored(inbox):
    inbox.email("m1", "hello", understanding(), sender="Agent <agent@agentmail.to>")
    assert inbox.understood == [] and inbox.replies == []


def test_help_reply_once_then_cooldown(inbox):
    inbox.email("m1", "hi", understanding(is_request=False, wants_help=True))
    inbox.email("m2", "hi again", understanding(is_request=False, wants_help=True))
    assert len(inbox.replies) == 1 and "I'm an email agent" in inbox.replies[0][1]


def test_off_topic_gets_no_reply(inbox):
    inbox.email("m1", "what is replit", understanding(is_request=False, wants_help=False))
    assert inbox.replies == []


def test_clarification_is_sent_and_nothing_fetched(inbox):
    inbox.email("m1", "send me record for 12205",
                understanding(clarification="Which document type would you like for M12205?"))
    assert inbox.fulfilled == []
    assert "Which document type would you like for M12205?" in inbox.replies[0][1]


def test_model_output_is_revalidated(inbox):
    # Even if the model returns a malformed matter number without asking, the code refuses to act on it.
    inbox.email("m1", "exhibits for M1220", understanding(requests=[DocRequest(matter="M1220", doc_type="Exhibits")]))
    assert inbox.fulfilled == []
    assert "Please include a matter number" in inbox.replies[0][1]


def test_valid_request_is_acknowledged_then_fetched(inbox):
    inbox.email("m1", "key docs for m12205",
                understanding(requests=[DocRequest(matter="M12205", doc_type="Key Documents")]))
    assert inbox.replies[0][1].startswith("Got it! Fetching the Key Documents for M12205 now")
    assert [(r.matter, r.doc_type) for _, r, _, _ in inbox.fulfilled] == [("M12205", "Key Documents")]


def test_correction_makes_the_earlier_job_stale(inbox):
    inbox.email("m1", "other docs for m12205",
                understanding(requests=[DocRequest(matter="M12205", doc_type="Other Documents")]), at=T0)
    inbox.email("m2", "sorry, I meant key documents",
                understanding(requests=[DocRequest(matter="M12205", doc_type="Key Documents")], replaces_previous=True),
                at=T0 + timedelta(seconds=30))
    (_, _, _, stale_first), (_, _, _, stale_second) = inbox.fulfilled
    assert stale_first() and not stale_second()
    assert inbox.replies[1][1].startswith("No problem, switching to the Key Documents for M12205")


def test_correction_processed_first_still_wins(inbox):
    # With concurrent workers the correction can be handled before the original; timestamps decide, not order.
    inbox.email("m2", "sorry, I meant key documents",
                understanding(requests=[DocRequest(matter="M12205", doc_type="Key Documents")], replaces_previous=True),
                at=T0 + timedelta(seconds=30))
    inbox.email("m1", "other docs for m12205",
                understanding(requests=[DocRequest(matter="M12205", doc_type="Other Documents")]), at=T0)
    assert [r.doc_type for _, r, _, _ in inbox.fulfilled] == ["Key Documents"]


def test_cancel_with_no_new_request(inbox):
    inbox.email("m1", "never mind", understanding(replaces_previous=True))
    assert inbox.replies[0][1].startswith("No problem, I've cancelled that request.")


def test_signed_name_is_remembered_per_thread(inbox):
    inbox.email("m1", "exhibits please\nThanks, Paula", understanding(sender_first_name="Paula", clarification="Which matter?"))
    inbox.email("m2", "M12205", understanding(clarification="Which document type?"))
    inbox.email("m3", "hello", understanding(clarification="Which matter?"), thread="other-thread")
    assert [text.splitlines()[0] for _, text in inbox.replies] == ["Hi Paula,", "Hi Paula,", "Hi there,"]
