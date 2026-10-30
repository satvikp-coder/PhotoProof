# Team and ownership

Every open issue has one owner, shown by its `owner:*` label and by its assignee.
Filter the [issues tab](https://github.com/satvikp-coder/PhotoProof/issues?q=is%3Aopen+label%3Aowner%3Agarv)
by label to see one person's track. Effort points: small 1, medium 2, large 3.

| Owner | Track | Issues | Points |
|---|---|---|---|
| Varun Karthic ([@varunkarthic](https://github.com/varunkarthic)) | Ingestion, decoders, isolation, hardening, performance | 24 | 49 |
| Satvik Patel ([@satvikp-coder](https://github.com/satvikp-coder)) | WebKit, reports, fusion, jobs, retention, Windows | 25 | 52 |
| Garv Gurwara ([@inevitable-lettus](https://github.com/inevitable-lettus)) | Detectors, copy-move, comparison, datasets, benchmarks | 24 | 50 |
| Shambhavee ([@shambhavee08](https://github.com/shambhavee08)) | CLI tools, plugin scaffold, metrics, backups, smaller detectors | 10 | 14 |

## Allocation

| Owner | Group | Issues |
|---|---|---|
| Varun | Ingestion | #1, #2, #3, #4, #5, #11, #12 |
| Varun | Further formats | #6, #7, #8, #69 |
| Varun | Isolation and security | #73, #9, #52, #59, #60, #61, #81 |
| Varun | Performance | #53, #57 |
| Varun | Repository | #75, #76, #78, #79 |
| Satvik | WebKit | #38, #39, #40, #41, #42, #43, #44, #45 |
| Satvik | Reports | #46, #47, #31 |
| Satvik | Fusion | #32, #33, #34, #35, #36, #68 |
| Satvik | Jobs, storage, replay | #50, #51, #55, #56, #82, #54 |
| Satvik | Windows runtime security | #80 |
| Satvik | Repository (admin) | #63 |
| Garv | Foundations | #72, #13, #58 |
| Garv | Compression, noise, edges | #16, #17, #18, #19, #26 |
| Garv | Copy-move | #77, #20, #21, #22, #23, #24 |
| Garv | Comparison | #27, #28, #29, #30 |
| Garv | Evaluation and research | #64, #65, #37, #67, #70, #71 |
| Shambhavee | CLI and developer tooling | #74, #48, #49, #83 |
| Shambhavee | Smaller detectors | #14, #15, #25 |
| Shambhavee | Metrics, backups, previews | #66, #62, #10 |

## Platforms

Satvik and Shambhavee develop on Windows; Varun and Garv develop on macOS. Issues with
Windows-specific code are owned or verified on Windows:

- Satvik owns #80 (ACLs) and #51 (worker shutdown without SIGTERM).
- Varun owns #73, #9, #52 and #59 from macOS. Satvik verifies their Windows behaviour
  (Job Objects, AppContainer) on a real Windows machine before they merge.

## Handoffs between tracks

These are all the hard dependencies between people. Everything else between tracks is
an *integrates with* link, which never stops work from starting.

| Merge first | Owner | Unblocks |
|---|---|---|
| #72, #13 | Garv | #12, #32, #14, #15, #25. **Do these first: both are small.** |
| #4 | Varun | #10 |
| #11, #12, #52 | Varun | #27, #70 |
| #1 | Varun | #54 |
| #33, #34, #35 | Satvik | #37 |
| #58 | Garv | #59, #61 |
| #19, #24 | Garv | #57 |
| #66 | Shambhavee | #37, #67, #68, #70 |
| #48 | Shambhavee | #54 |

## Reviews

`.github/CODEOWNERS` requests the primary and secondary reviewer for each area. It
only takes effect for people with write access to the repository.
