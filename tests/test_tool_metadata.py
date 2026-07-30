"""Guards on the MCP surface itself: every tool declares the metadata clients
rely on, and the README's tool table matches the code.

Ported from the `check:meta` CI step in spotify-mcp-cloudflare. Annotations are
not cosmetic — clients decide what to auto-run and what to confirm from
`readOnlyHint`/`destructiveHint`, so a tool shipping without them is a real
defect, and README drift is how people end up calling tools that don't exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from spotify_mcp.fastmcp_server import mcp

ROOT = Path(__file__).resolve().parents[1]

# Descriptions are per-tool context the model pays for on every request.
# Whole-surface guidance belongs in INSTRUCTIONS instead.
MAX_DESCRIPTION_CHARS = 1200


@pytest.fixture(scope="module")
async def tools():
    return await mcp.list_tools()


@pytest.fixture(scope="module")
def readme_tools() -> set[str]:
    readme = (ROOT / "README.md").read_text()
    table = readme.split("### Tools\n", 1)[1].split("\n\n", 1)[0]
    return set(re.findall(r"^\| `([a-z_]+)`", table, re.MULTILINE))


class TestToolAnnotations:
    async def test_every_tool_has_a_title(self, tools):
        assert [t.name for t in tools if not t.title] == []

    async def test_every_tool_has_behaviour_annotations(self, tools):
        missing = [
            t.name
            for t in tools
            if t.annotations is None or t.annotations.readOnlyHint is None
        ]
        assert missing == []

    async def test_writes_declare_a_destructive_hint(self, tools):
        # Only read-only tools may leave destructiveHint unset; for a write, the
        # client has no way to guess.
        missing = [
            t.name
            for t in tools
            if not t.annotations.readOnlyHint and t.annotations.destructiveHint is None
        ]
        assert missing == []

    async def test_every_tool_has_an_icon(self, tools):
        assert [t.name for t in tools if not t.icons] == []

    async def test_descriptions_stay_within_budget(self, tools):
        too_long = [
            t.name
            for t in tools
            if t.description and len(t.description) > MAX_DESCRIPTION_CHARS
        ]
        assert too_long == []


class TestReadmeParity:
    async def test_readme_documents_every_tool(self, tools, readme_tools):
        assert sorted({t.name for t in tools} - readme_tools) == []

    async def test_readme_lists_no_tools_that_do_not_exist(self, tools, readme_tools):
        assert sorted(readme_tools - {t.name for t in tools}) == []


class TestServerMetadata:
    def test_server_ships_instructions(self):
        assert mcp.instructions
        # The point of INSTRUCTIONS is the guidance the tool list can't carry.
        assert "search_music" in mcp.instructions
