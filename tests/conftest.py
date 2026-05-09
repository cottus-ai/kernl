from pathlib import Path

import pytest

from akernl.agent import AgentManifest, parse
from akernl.bundle import pack

SAMPLE_AGENT_SOURCE = """\
from akernl import agent, tool

@agent(name="sample", model="claude-sonnet-4-20250514", max_steps=3)
class SampleAgent:
    question: str

    @tool
    def echo(self, message: str) -> str:
        \"\"\"Echo input.\"\"\"
        return message
"""


@pytest.fixture()
def sample_agent_source() -> str:
    return SAMPLE_AGENT_SOURCE


@pytest.fixture()
def sample_manifest(sample_agent_source: str, tmp_path: Path) -> AgentManifest:
    p = tmp_path / "sample.agent.py"
    p.write_text(sample_agent_source)
    return parse(p)


@pytest.fixture()
def tmp_krn(sample_agent_source: str, sample_manifest: AgentManifest, tmp_path: Path) -> Path:
    out = tmp_path / "sample.krn"
    pack(sample_manifest, sample_agent_source, out)
    return out
