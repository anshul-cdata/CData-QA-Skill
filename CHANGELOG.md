# Changelog

## 1.1.0
### `/filter`
- Phase 3c (RSD extraction) and Phase 3d (RSD validation) now run only when the user provides an RSD. Dynamic tables have no RSD; semi-dynamic tables use `<table>{internal}.rsd`.
- New `RSD: not provided` status for query-plan rows; Phase 5 and Phase 6 handle the no-RSD case.
- New combination testing phase (§4e C1–C7) — multi-column AND / OR / mixed / server+client-side mixes — with run rules in §5d and a "Combination results" section in the Phase 6 report.

### `/general-testing`
- Phase 0 asks for baseline scope (all rows or first 1000, recommended for large tables) and optional target columns to test with all important operators.
- Sample-mode (`SAMPLE_1000`) baseline: containment check replaces count/complement checks; report template and failure-pattern appendix updated.
- Renamed stale `/columns` references to `/general-testing`.

### Repo
- Added README, `.gitattributes` (LF line endings), `scripts/lint.py`, this changelog.

## 1.0.0
- Initial release of the unified CData JDBC driver QA skill (API and DB driver workflows).
