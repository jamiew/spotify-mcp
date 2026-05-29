#!/bin/bash
set -euo pipefail

# Release script for spotify-mcp
# Usage: ./release.sh [--dry-run]

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "=== DRY RUN MODE ==="
fi

# Get version from pyproject.toml
VERSION=$(grep -E "^version = " pyproject.toml | sed 's/version = "\(.*\)"/\1/')
TAG="v$VERSION"

echo "Releasing spotify-mcp $TAG"
echo ""

# Check for uncommitted changes
if [[ -n "$(git status --porcelain)" ]]; then
    echo "ERROR: Uncommitted changes detected. Commit or stash them first."
    exit 1
fi

# Check if tag exists locally
if ! git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "ERROR: Tag $TAG doesn't exist locally."
    echo "Create it with: git tag -a $TAG -m 'Release notes here'"
    exit 1
fi

# Check if release already exists on GitHub
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "ERROR: Release $TAG already exists on GitHub."
    exit 1
fi

# Run quality checks
echo "Running quality checks..."
uv run mypy src/
uv run pytest
echo "Quality checks passed!"
echo ""

# Build the package
echo "Building package..."
rm -rf dist/
uv build
echo "Build complete!"
echo ""

# Note: PyPI + MCP Registry publishing is handled by .github/workflows/publish.yml,
# which fires on the GitHub release created below (via OIDC trusted publishing).

# Get tag annotation for release notes
RELEASE_NOTES=$(git tag -l --format='%(contents)' "$TAG")

if [[ -z "$RELEASE_NOTES" ]]; then
    echo "ERROR: Tag $TAG has no annotation. Use annotated tags:"
    echo "  git tag -a $TAG -m 'Release notes here'"
    exit 1
fi

echo "Release notes:"
echo "---"
echo "$RELEASE_NOTES"
echo "---"
echo ""

if $DRY_RUN; then
    echo "[DRY RUN] Would push commits and tags"
    echo "[DRY RUN] Would create GitHub release $TAG"
    echo "[DRY RUN] Would upload: $(ls dist/)"
    exit 0
fi

# Push commits and tags
echo "Pushing to GitHub..."
git push
git push --tags

# Create GitHub release with assets
echo "Creating GitHub release..."
gh release create "$TAG" \
    --title "$TAG" \
    --notes "$RELEASE_NOTES" \
    dist/*

echo ""
echo "Released $TAG!"
echo "https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/$TAG"
