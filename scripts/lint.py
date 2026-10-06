#!/usr/bin/env python3
"""Lint the cdata-qa skill: stale commands, missing files, broken phase refs.

Usage: python scripts/lint.py        (run from the repo root; exits 1 on errors)
"""
import re, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
skill = (root / "SKILL.md").read_text(encoding="utf-8")
wf = {p.name: p for p in (root / "workflows").glob("*.md")}
errors, warnings = [], []

# 1. every workflows/*.md named in SKILL.md exists, and every workflow file is referenced
refs = set(re.findall(r"workflows/([A-Za-z0-9_.-]+\.md)", skill))
for r in sorted(refs - wf.keys()):
    errors.append(f"SKILL.md references missing file workflows/{r}")
for f in sorted(wf.keys() - refs):
    warnings.append(f"workflows/{f} is not referenced from SKILL.md")

# 2. every /command in SKILL.md front matter has a row in the command table; flag stale names
fm = skill.split("---")[1]
cmds = set(re.findall(r"^\s+(/[a-z-]+)", fm, re.M)) | set(re.findall(r"(/qpt(?:true|false))", fm))
for c in sorted(cmds):
    if c not in skill.split("---", 2)[2]:
        errors.append(f"command {c} listed in front matter but not documented in body")
for p in [root / "SKILL.md", *wf.values()]:
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if "/columns" in line:
            errors.append(f"{p.relative_to(root)}:{n} stale command /columns (use /general-testing)")

# 3. Phase / section cross-references resolve inside each workflow
for name, p in wf.items():
    text = p.read_text(encoding="utf-8")
    heads = " ".join(re.findall(r"^#{1,4} .*$", text, re.M))
    declared = set(re.findall(r"Phase (\d+[a-z]?)\b", heads))
    for ref in set(re.findall(r"Phase (\d+[a-z]?)\b", text)) - declared:
        # only flag if the file has phases at all
        if declared:
            warnings.append(f"{name}: references Phase {ref} but no heading declares it")
    for sec in set(re.findall(r"§(\d+[a-z]\b)", text)):
        if not re.search(rf"^#{{2,5}} {re.escape(sec)}\b", text, re.M):
            warnings.append(f"{name}: references §{sec} but no '### {sec}' heading found")

for w in warnings: print("WARN ", w)
for e in errors:   print("ERROR", e)
print(f"{len(errors)} error(s), {len(warnings)} warning(s)")
sys.exit(1 if errors else 0)
