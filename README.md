# CData QA Skill

A Claude Code skill (`cdata-qa`) for QA-testing CData JDBC drivers — both API-backed (REST/SOAP) and database-backed drivers.

## Install

Copy this repo's contents into a skill folder named `cdata-qa`:

```bash
# user-level (all projects)
git clone https://github.com/anshul-cdata/CData-QA-Skill.git ~/.claude/skills/cdata-qa
# or project-level
git clone https://github.com/anshul-cdata/CData-QA-Skill.git .claude/skills/cdata-qa
```

On Windows the user-level folder is `%USERPROFILE%\.claude\skills\cdata-qa`.

## Commands

| API drivers | What it tests |
|---|---|
| `/filter` | Server-side filter pushdown, incl. AND/OR combinations; RSD cross-check only when an RSD is provided |
| `/auth` | AuthScheme + PRP end-to-end validation |
| `/cud` | INSERT / UPDATE / DELETE |
| `/sp` | Stored procedures |
| `/general-testing` | Column/data validation; baseline of all rows or first 1000; optional target columns |
| `/perf` | Performance / log pattern analysis |
| `/usage` | Token and cost report |

| DB drivers | Commands |
|---|---|
| QPT=True | `/qpttrue create \| insert \| select \| update \| delete` |
| QPT=False | `/qptfalse create \| insert \| select \| update \| delete` |

`SKILL.md` is the entry point; each command's workflow lives in `workflows/`.

## Layout

```
SKILL.md          entry point, command routing
workflows/        one file per command
scripts/lint.py   consistency checks
CHANGELOG.md
```

## Checking changes

```bash
python scripts/lint.py
```

Verifies that every workflow referenced in `SKILL.md` exists, every command is documented, no stale command names remain, and phase/section cross-references resolve.
