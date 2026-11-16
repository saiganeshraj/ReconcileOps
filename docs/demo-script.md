# Five-minute local demonstration

Start with `.\scripts\start-demo.ps1` in `D:\Projects\ReconcileOps`, then open http://localhost:8000. All data is synthetic. Use a fresh demo database or inspect existing run IDs to avoid confusing findings from different runs.

1. **0:00 — Import.** Open Order imports, download the sample CSV, upload it, and click Preview import. Inspect six accepted rows. Confirm and commit. Upload it again to demonstrate six unchanged rows and unchanged unique order count.
2. **0:50 — Sync.** Open Reconciliation runs and click Synchronize provider. Wait for a persisted SUCCEEDED sync: six imported events. Repeat: zero new events, six duplicate deliveries.
3. **1:25 — Run.** Keep cutoff `2026-11-16T12:00:00Z` and create a run. Watch QUEUED/RUNNING/SUCCEEDED, depending on worker timing. Open the run ID to inspect snapshot counts, rule version, attempts, and export. Expect six orders, six events, seven findings.
4. **2:00 — Findings.** Show O101 missing payment; O102 expected INR 300 versus INR 250; O103 two captured payment IDs plus INR 200 net versus INR 100 expected; O104 wrong USD currency plus missing valid INR payment. P999 references unknown O999. O100 is clean. O105_FUTURE is not due and has no monetary finding.
5. **2:50 — Evidence and review.** Filter O102, click Inspect, and show rule calculations, original CSV row, and provider event. Add a note and Resolve. Show the audit entry. Reopen with another note to demonstrate history/version changes. Explain that a review decision never moves money.
6. **3:40 — Recovery.** Open Demo controls, choose 503 once → recovery, and apply. Synchronize provider again. Show SUCCEEDED sync with one recorded HTTP_5XX retry, then zero imported/six duplicate events. The provider automatically clears this one-time fault. Persistent 429, timeout, and 401 modes require selecting Normal operation afterward.
7. **4:25 — Export.** Download the run's full findings CSV. Open Overview and show separate gross capture totals: INR 590.00 and USD 100.00. Explain immutable source IDs versus findings scoped to a new run.

Optional technical extension: replay the same run task ID; successful redelivery does not add an attempt or finding. Stop the broker, create a run, restore the broker, and use `python -m reconcileops.tasks.recovery` to redispatch the persisted queue record. Do not present these checks as demonstrated unless executed.

Automated recording: run `scripts/browser_smoke.py` against the running stack. It writes actual screenshots, video, export, and a Playwright trace to `artifacts/browser/`.
