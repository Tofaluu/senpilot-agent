import os

# Set before any project import: mail.py and agent.py build API clients at import time, and load_dotenv
# never overrides existing variables, so tests can't pick up real keys from .env.
os.environ["AGENTMAIL_API_KEY"] = "test"
os.environ["AGENTMAIL_INBOX"] = "agent@agentmail.to"
os.environ["ANTHROPIC_API_KEY"] = "test"

import pytest  # noqa: E402

import agent  # noqa: E402


@pytest.fixture(autouse=True)
def reset_agent_state():
    for d in (agent.thread_names, agent.replaced_at, agent.last_help, agent.fetching_threads):
        d.clear()
    yield
