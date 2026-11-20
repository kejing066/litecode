#!/bin/sh
# Install lite (a lite agentic coding tool) and run its TUI.
#
#   curl https://raw.githubusercontent.com/kejing066/litecode/main/install.sh | sh
#
# Overrides:
#   LITE_REPO_URL      default: https://raw.githubusercontent.com/kejing066/litecode/main
#   LITE_INSTALL_DIR   default: $HOME/.local/bin
#   LITE_INSTALL_ONLY=1  install without running the TUI
#   LITECODE_CONFIG_DIR / LITECODE_OPENCODE_CONFIG_DIR (passed through to lite)
set -eu

LITE_REPO_URL="${LITE_REPO_URL:-https://raw.githubusercontent.com/kejing066/litecode/main}"
LITE_INSTALL_DIR="${LITE_INSTALL_DIR:-$HOME/.local/bin}"
LITE_SRC="$LITE_INSTALL_DIR/src"
LITE_PKG="$LITE_SRC/lite"
LITE_BIN="$LITE_INSTALL_DIR/lite"

die() {
    echo "error: $*" >&2
    exit 1
}

command -v python3 >/dev/null 2>&1 || die "python3 is required (not found on PATH)"

if command -v curl >/dev/null 2>&1; then
    fetch() { curl -fsSL "$1" -o "$2"; }
elif command -v wget >/dev/null 2>&1; then
    fetch() { wget -qO "$2" "$1"; }
else
    die "curl or wget is required (neither found on PATH)"
fi

# Files of the lite package, relative to the repo root.
LITE_FILES="__init__.py __main__.py agent.py cli.py config.py llm.py tools.py tui.py"

mkdir -p "$LITE_PKG" || die "cannot create directory $LITE_PKG"
for f in $LITE_FILES; do
    fetch "$LITE_REPO_URL/lite/$f" "$LITE_PKG/$f" \
        || die "failed to download $LITE_REPO_URL/lite/$f"
    [ -s "$LITE_PKG/$f" ] || die "empty download from $LITE_REPO_URL/lite/$f"
done

# Launcher: `lite` runs the package from $LITE_SRC.
cat > "$LITE_BIN" <<EOF
#!/usr/bin/env python3
import sys

sys.path.insert(0, "$LITE_SRC")

from lite.cli import main

sys.exit(main())
EOF
chmod +x "$LITE_BIN"

echo "installed lite to $LITE_BIN"

case ":$PATH:" in
    *":$LITE_INSTALL_DIR:"*) ;;
    *) echo "add to PATH: export PATH=\"\$PATH:$LITE_INSTALL_DIR\"" ;;
esac

if [ -n "${LITE_INSTALL_ONLY:-}" ]; then
    echo "run it with: $LITE_BIN --tui"
    exit 0
fi

# Run the TUI when attached to a terminal: stdin when it is a terminal,
# else the controlling terminal (/dev/tty) when it can actually be opened;
# otherwise print the "run it with" hint.
if [ -t 0 ]; then
    "$LITE_BIN" --tui
elif (exec 3< /dev/tty) 2>/dev/null; then
    "$LITE_BIN" --tui < /dev/tty
else
    echo "run it with: $LITE_BIN --tui"
fi
