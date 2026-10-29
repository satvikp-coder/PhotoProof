---
id: PP-038
title: "feat(WebKit): select the web framework and build navigation and base layout"
milestone: M5
labels: [type:feature, priority:P1, area:WebKit, effort:medium, status:needs-design]
depends_on: []
---
## Summary

Choose the WebKit technology stack through a short written decision record, then
implement `WebKit/app.py` with navigation, a base layout, an identity and status header,
and an accessible, responsive theme. Configuration comes from `app.toml` and
`settings.yaml` `ui.*`.

## Problem Statement

`WebKit/` contains only a README. Every UI issue depends on a stable application shell,
a routing structure and a decision about the framework.

## Objective

A running local web app (`python main.py web`) that shows the application identity, the
health summary and the installed plugins, and provides navigation to pages that are
still placeholders.

## Technical Background

Candidates:

- **Streamlit**: fastest to build; limited layout control; reruns the script per
  interaction.
- **FastAPI + Jinja2 + HTMX**: server-rendered, small JS, testable with `TestClient`.
- **NiceGUI**: Python components, FastAPI underneath.

The criteria are accessibility (WCAG 2.1 AA), testability, file-upload control
(streaming, limits), the dependency footprint, and a security posture (CSRF, CSP).
Recommendation to evaluate first: FastAPI + Jinja2 + HTMX.

## Functional Requirements

- ADR added to `docs/architecture/decisions.md`.
- A CLI command `web` that binds to `ui.host:ui.port` (loopback by default, as the
  production rule already enforces).
- Layout: header (name and version from config), navigation (Analyze, Compare, History,
  Diagnostics, About), a footer showing the release channel and a "foundation – no
  verdicts" notice while detectors are missing.
- Status page: health summary from `default_registry()` and plugin list with policy
  decisions (read-only).
- Theme: system light/dark, restrained palette, keyboard-navigable, visible focus states,
  contrast ≥ 4.5:1.

## Technical Implementation

`WebKit/app.py` (application factory), `WebKit/pages/`, `WebKit/components/`,
`WebKit/static/`. WebKit calls `photoproof.core` services only (never implements
algorithms). Security headers: CSP (no inline scripts), `X-Content-Type-Options`,
`Referrer-Policy`, and CSRF tokens for forms.

## Integration Requirements

`ui.host` and `ui.port` stop being *reserved* settings. Every request that reaches the
policy uses `OverrideOrigin.WEB`; there is no blacklist-bypass UI.

## Security and Edge Cases

Binding to a non-loopback address must require an explicit setting and show a warning
in the UI (authentication does not exist). No directory listing for static files. Error
pages leak no stack traces unless `development.debug`.

## Testing Requirements

HTTP tests for every page (status 200, security headers present), an accessibility
smoke test (axe-core via Playwright, optional in CI), and a test that the bypass is
unreachable.

## Acceptance Criteria

- [ ] ADR merged with the chosen framework and rationale.
- [ ] `python main.py web` serves the shell on loopback, with identity read from configuration.
- [ ] Security headers are present on all responses (test).
- [ ] Keyboard navigation and contrast meet WCAG 2.1 AA on the shell pages (manual checklist in the PR).
- [ ] No route exposes `--ignore-blacklist` or any other administrative override.

## Expected Outcome

A professional, accessible shell that the remaining UI issues plug into.

## Documentation Requirements

ADR, `WebKit/README.md`, operations docs (`web` command), settings reference (`ui.*`).

## Dependencies

None technically. Real content arrives with PP-039 and later issues.

## Priority and Effort

P1, medium. The framework decision is needed first.
