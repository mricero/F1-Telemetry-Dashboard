#!/bin/sh
# F1 Telemetry Dashboard (f1dash) installer for macOS and Linux (DIST-04).
#
#   curl -LsSf https://raw.githubusercontent.com/mricero/F1-Telemetry-Dashboard/main/install.sh | sh
#
# What it does, without root and without touching your own Python:
#   1. installs uv (Astral's official installer) if `uv` is missing;
#   2. installs the latest release of f1dash as a uv tool (uv fetches Python 3.12 itself);
#   3. puts uv's tool folder on PATH (`uv tool update-shell`).
# Running it again updates to the newest release.
#
# F1DASH_SOURCE=<path or git URL> installs from there instead (CI uses the checkout).

set -eu

REPO="mricero/F1-Telemetry-Dashboard"

say() {
    printf '%s\n' "$*"
}

fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --max-time 15 "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- --timeout=15 "$1"
    else
        return 1
    fi
}

# 1. uv
if ! command -v uv >/dev/null 2>&1; then
    say "Installing uv (https://docs.astral.sh/uv/) ..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        say "Neither curl nor wget is available; install uv by hand: https://docs.astral.sh/uv/"
        exit 1
    fi
    PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    export PATH
    if ! command -v uv >/dev/null 2>&1; then
        say "uv was installed but is not on PATH yet. Open a new terminal and run this again."
        exit 1
    fi
fi

# 2. f1dash, from the latest release tag (or main when there is none yet)
SOURCE="${F1DASH_SOURCE:-}"
if [ -z "$SOURCE" ]; then
    REF=$(fetch "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
        | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1) || REF=""
    if [ -z "$REF" ]; then
        say "No release found on GitHub; installing from main."
        REF="main"
    fi
    SOURCE="git+https://github.com/$REPO@$REF"
fi
say "Installing f1dash from $SOURCE ..."
uv tool install --python 3.12 --reinstall "$SOURCE"

# 3. PATH
uv tool update-shell || say "Could not update the shell profile; add $(uv tool dir --bin) to PATH."

say ""
say "Done. Run: f1dash   (open a new terminal first if the command is not found)"
say "Live car telemetry and positions need your own F1TV subscription token:"
say "set F1TV_SUBSCRIPTION_TOKEN in the .env file that 'f1dash paths' shows."
