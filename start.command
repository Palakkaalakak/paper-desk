#!/bin/bash
# macOS / Linux launcher. If double-clicking does nothing, see START_HERE.md.
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3 python3.13 python3.12 python3.11 /usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo ""
  echo "  Python 3 is not installed."
  echo ""
  echo "  On a Mac the quickest fix is to run this in Terminal:"
  echo "      xcode-select --install"
  echo "  or install from https://www.python.org/downloads/"
  echo ""
  read -r -p "  Press return to close." _
  exit 1
fi

echo "Using $PY"
exec "$PY" serve.py
