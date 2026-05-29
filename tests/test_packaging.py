"""Guards that the publish artifacts (server.json) stay consistent with pyproject and the code.

These catch drift before a release ships a server.json that disagrees with the package
name, version, or the environment variables the server actually reads.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)


@pytest.fixture(scope="module")
def server_json() -> dict:
    with open(ROOT / "server.json", "rb") as f:
        return json.load(f)


class TestServerJson:
    def test_is_valid_and_namespaced(self, server_json: dict) -> None:
        assert server_json["name"] == "io.github.jamiew/spotify-mcp"
        assert server_json["$schema"].startswith(
            "https://static.modelcontextprotocol.io/"
        )

    def test_single_pypi_package(self, server_json: dict) -> None:
        packages = server_json["packages"]
        assert len(packages) == 1
        pkg = packages[0]
        assert pkg["registryType"] == "pypi"
        assert pkg["registryBaseUrl"] == "https://pypi.org"
        assert pkg["transport"]["type"] == "stdio"


class TestConsistencyWithPyproject:
    def test_package_identifier_matches_dist_name(
        self, server_json: dict, pyproject: dict
    ) -> None:
        identifier = server_json["packages"][0]["identifier"]
        assert identifier == pyproject["project"]["name"] == "spotify-mcp-jamiew"

    def test_versions_are_in_sync(self, server_json: dict, pyproject: dict) -> None:
        version = pyproject["project"]["version"]
        assert server_json["version"] == version
        assert server_json["packages"][0]["version"] == version

    def test_published_command_is_exposed_as_a_script(self, pyproject: dict) -> None:
        # uvx spotify-mcp-jamiew only works if a console script of that name exists
        assert "spotify-mcp-jamiew" in pyproject["project"]["scripts"]


class TestEnvVarsMatchCode:
    def test_server_json_declares_exactly_the_vars_the_code_reads(
        self, server_json: dict
    ) -> None:
        declared = {
            ev["name"] for ev in server_json["packages"][0]["environmentVariables"]
        }
        source = (ROOT / "src" / "spotify_mcp" / "spotify_api.py").read_text()
        used = set(re.findall(r'os\.getenv\(\s*"(SPOTIFY_[A-Z_]+)"', source))
        assert declared == used

    def test_required_secrets_are_flagged(self, server_json: dict) -> None:
        by_name = {
            ev["name"]: ev for ev in server_json["packages"][0]["environmentVariables"]
        }
        assert by_name["SPOTIFY_CLIENT_ID"]["isSecret"] is True
        assert by_name["SPOTIFY_CLIENT_SECRET"]["isSecret"] is True
