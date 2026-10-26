#!/usr/bin/env python3
"""Check that every FR/OD/US ID cited in this skill exists in the PRD.

Usage: check_ids.py [path/to/prd.md]   (default: .ai/docs/prd.md)
Exit 1 and list unknown IDs if any are missing, so the skill never cites a
requirement that does not exist.
"""
import re
import sys
from pathlib import Path

skill_dir = Path(__file__).resolve().parent.parent
prd = Path(sys.argv[1] if len(sys.argv) > 1 else ".ai/docs/prd.md").read_text()
known = set(re.findall(r"\b(?:FR|OD|US)-\d{2,3}\b", prd))

cited = set()
for f in [skill_dir / "SKILL.md", *skill_dir.glob("references/*.md")]:
    text = f.read_text()
    for m in re.finditer(r"\b(FR|OD|US)-(\d{2,3})((?:…|\.\.\.)(\d{2,3}))?\b", text):
        cited.add(f"{m.group(1)}-{m.group(2)}")
        if m.group(4):  # range like FR-001…FR-017 is checked at its endpoints
            cited.add(f"{m.group(1)}-{m.group(4)}")
    for m in re.finditer(r"\b(FR|OD|US)-(\d{2,3})/(\d{2,3})\b", text):
        cited.add(f"{m.group(1)}-{m.group(3)}")

missing = sorted(cited - known)
print(f"cited {len(cited)} IDs, {len(missing)} unknown")
if missing:
    print("unknown:", ", ".join(missing))
    sys.exit(1)
