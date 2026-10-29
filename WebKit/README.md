# WebKit (planned)

The web-facing component of PhotoProof. **Nothing is implemented yet.** The framework
choice, layout and pages are specified in the backlog (milestone *Web Application and
Reporting*).

Constraints that already apply:

- WebKit never implements forensic algorithms. It calls the controller and renders
  results.
- It reads identity and settings from configuration (`app.toml`, `settings.yaml`
  `ui.*`) instead of hardcoding them.
- It binds to loopback by default and must never expose `--ignore-blacklist` or any
  other administrative override (requests use `OverrideOrigin.WEB`, which the policy
  rejects).
