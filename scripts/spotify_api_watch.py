#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx"]
# ///
"""Probe Spotify's Web API changelog for entries we haven't reviewed yet.

Spotify publishes no RSS feed and no changelog index, but the URLs are perfectly
predictable (.../references/changes/<month>-<year>) and 404 when absent, so
probing the month space is the only reliable way to notice a new entry.

Exits 1 when something unreviewed turns up, so a cron/routine can alert on it.
Ported from spotify-mcp-cloudflare's scripts/spotify-api-watch.ts.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

CHANGELOG_BASE = (
    "https://developer.spotify.com/documentation/web-api/references/changes"
)
SEEN_PATH = Path(__file__).with_name("spotify-api-seen.json")
FIRST = (2026, 2)  # the February 2026 breaking change

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def candidate_slugs(now: datetime) -> list[str]:
    """Every month slug from the first changelog through next month.

    One month past today because Spotify has published entries mid-month before.
    """
    end = (now.year, now.month + 1) if now.month < 12 else (now.year + 1, 1)
    slugs = []
    year, month = FIRST
    while (year, month) <= end:
        slugs.append(f"{MONTHS[month - 1]}-{year}")
        month += 1
        if month > 12:
            month, year = 1, year + 1
    return slugs


def main() -> int:
    seen: list[str] = json.loads(SEEN_PATH.read_text())

    with httpx.Client(follow_redirects=True, timeout=15) as client:
        found = [
            slug
            for slug in candidate_slugs(datetime.now(UTC))
            if client.head(f"{CHANGELOG_BASE}/{slug}").is_success
        ]

    unreviewed = [slug for slug in found if slug not in seen]
    vanished = [slug for slug in seen if slug not in found]

    for slug in found:
        marker = "NEW " if slug in unreviewed else "    "
        print(f"{marker}{CHANGELOG_BASE}/{slug}")
    if vanished:
        print(f"\nreviewed but no longer reachable: {', '.join(vanished)}")

    if "--accept" in sys.argv and unreviewed:
        SEEN_PATH.write_text(json.dumps(found) + "\n")
        print(f"\naccepted {len(unreviewed)} entry(s) into the reviewed set")
        return 0

    if unreviewed:
        print(
            f"\n{len(unreviewed)} unreviewed changelog entry(s) — "
            "read them, then re-run with --accept"
        )
        return 1

    print("\nno unreviewed changelog entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
