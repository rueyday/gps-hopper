#!/usr/bin/env bash
# Launch GPS Hopper with a Python whose Tk actually works.
#
# The Python that ships with macOS is bound to Tk 8.5, which refuses to start on
# current macOS builds ("macOS 15 (1507) or later required"). Conda, python.org
# and Homebrew's python-tk all ship 8.6, so pick the first of those that works.
set -euo pipefail
cd "$(dirname "$0")"

candidates=(
  "$(command -v python3 || true)"
  "$HOME/anaconda3/bin/python3"
  "$HOME/miniconda3/bin/python3"
  "$HOME/opt/anaconda3/bin/python3"
  /Library/Frameworks/Python.framework/Versions/3.*/bin/python3
  /opt/homebrew/bin/python3.13
  /opt/homebrew/bin/python3.12
  /opt/homebrew/bin/python3.11
)

for py in "${candidates[@]}"; do
  [ -x "$py" ] || continue
  if "$py" - <<'PROBE' >/dev/null 2>&1
import sys, tkinter
sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)
PROBE
  then
    echo "running with $py"
    exec "$py" -m gpshopper
  fi
done

echo "No Python with a working Tk 8.6 found." >&2
echo "Fix it with either:  conda install tk        (if you use conda)" >&2
echo "                     brew install python-tk  (for Homebrew python)" >&2
exit 1
