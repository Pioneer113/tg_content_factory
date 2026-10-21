# Changelog

Keep a Changelog format, SemVer. Consumers pin exact versions — the
`tg-messenger` precedent (`>=0.1.0` with no tags/changelog) is not repeated.

## [0.1.0] — 2026-10-21

### Added

- Package scaffold (#1420, epic #1416 stage 1.1): `SharedAccount` model,
  `SharedAccountStore` storage Protocol, `JsonFileStore` default
  implementation for the standalone single-account messenger, `py.typed`.
- No factory code moved yet — wave A (#1421) and wave B (#1422) follow.
