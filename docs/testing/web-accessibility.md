# Web accessibility verification

The shell uses semantic header/nav/main/footer landmarks, a skip link, one h1 per
page, `aria-current` navigation, visible focus and text-labelled findings. It uses
system fonts and `prefers-color-scheme`; no animation, JS or external assets are
required. Automated checks complement, not replace, keyboard and assistive-technology
review. Do not claim full WCAG conformance solely from axe.

## Optional browser smoke test

Use Node.js and a local installation of `playwright` and `@axe-core/playwright` in a
temporary directory. Run `npx playwright install chromium` in that directory, then
start the actual launcher against a disposable source/configuration/runtime copy:
`python tests/browser/preview.py --port 18501`. This copies no evidence and creates
its own temporary locked environment. Stop it with Ctrl+C after testing. The test script is
`tests/browser/smoke.cjs`; run it with that tools directory as the working directory:

```bash
node /absolute/path/to/PhotoProof/tests/browser/smoke.cjs http://127.0.0.1:18501
```

It checks all five pages in light/dark mode at desktop and mobile sizes, WCAG 2.1 AA
axe rules, horizontal overflow, skip-link focus and keyboard navigation, and saves
screenshots in `screenshots/` under the temporary working directory. The script only
issues read-only browser requests and must target a disposable runtime for testing.
Browser tooling is optional and is not installed by the runtime launcher or required
by CI. No test must point at a real evidence directory.

## Manual checklist for later review

- [ ] With mouse unused, Tab to the skip link, press Enter and confirm focus reaches
  main; then visit every navigation item with Tab/Shift+Tab and Enter.
- [ ] Confirm focus is visible on sidebar links, content links and scrollable tables
  in light and dark system appearances. No focus traps or hover-only information.
- [ ] At 320, 768 and 1440 CSS-pixel widths and 200% zoom, read all page content with
  no page-level horizontal scrolling, clipped navigation, or overlapping controls.
- [ ] Use a screen reader (NVDA/Firefox or VoiceOver/Safari) to confirm landmarks,
  page title/h1, current navigation item, health severity text and table headers.
- [ ] Verify normal text contrast is at least 4.5:1 and large text at least 3:1 in
  both appearances; do not communicate health solely through color.
- [ ] Check foundation notices and placeholders do not imply an analysis occurred.
- [ ] With JavaScript disabled, visit every page and read live diagnostics.

Screen-reader review and subjective keyboard usability require a human; record which
items were actually performed in the PR rather than checking this list speculatively.
