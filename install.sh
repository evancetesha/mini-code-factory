#!/usr/bin/env sh
set -eu

REPO="https://github.com/evancetesha/mini-code-factory.git"

if command -v uv >/dev/null 2>&1; then
    uv tool install --force "git+${REPO}"
elif command -v pipx >/dev/null 2>&1; then
    pipx install --force "git+${REPO}"
else
    echo "error: install uv (https://docs.astral.sh/uv/) or pipx first" >&2
    exit 1
fi

echo
echo "Installed 'factory'. Next steps:"
echo "  1. Install Herdr and OpenCode, then run: herdr integration install opencode"
echo "  2. cd into a project directory and start Herdr: herdr"
echo "  3. Inside its shell pane, run: factory"
echo
echo "On first run, 'factory' materializes its config into the current directory"
echo "(opencode.json, model-tiers.json, .factory/, .opencode/). Add .factory/ to"
echo ".gitignore if you don't want run artifacts committed."
