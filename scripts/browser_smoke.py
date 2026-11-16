"""Exercise the real local stack and save screenshots, trace, and a short recording."""

import csv
import io
import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://localhost:8000")
OUTPUT = Path(os.environ.get("SMOKE_OUTPUT_DIR", "artifacts/browser"))


def wait_for_success(request, path):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = request.get(BASE_URL + path)
        assert response.ok, response.text()
        detail = response.json()
        if detail["status"] == "SUCCEEDED":
            return detail
        assert detail["status"] != "FAILED", detail
        time.sleep(0.25)
    raise TimeoutError(f"Task did not finish: {path}")


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, record_video_dir=str(OUTPUT / "recordings")
        )
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE_URL)
        page.get_by_role("heading", name="Every payment. A clear picture.").wait_for()
        page.locator("#metrics .metric").nth(3).wait_for()
        page.screenshot(path=str(OUTPUT / "01-overview.png"), full_page=True)
        page.get_by_role("button", name="Order imports", exact=False).click()
        page.locator("#csv-file").set_input_files("fixtures/orders_baseline.csv")
        page.get_by_role("button", name="Preview import", exact=True).click()
        page.locator("#preview-summary").filter(has_text="6 rows").wait_for()
        page.screenshot(path=str(OUTPUT / "02-import-preview.png"), full_page=True)
        page.get_by_role("button", name="Confirm and commit import").click()
        page.locator("#notice").filter(has_text="committed").wait_for()
        page.locator('nav button[data-page="runs"]').click()
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/syncs") and response.request.method == "POST"
            )
        ) as sync_response:
            page.locator("#sync-provider").click()
        sync_id = sync_response.value.json()["sync_id"]
        wait_for_success(context.request, "/api/syncs/" + sync_id)
        with page.expect_response(
            lambda response: (
                response.url.endswith("/api/runs") and response.request.method == "POST"
            )
        ) as run_response:
            page.get_by_role("button", name="Create run", exact=False).click()
        run_id = run_response.value.json()["id"]
        run = wait_for_success(context.request, "/api/runs/" + run_id)
        page.screenshot(path=str(OUTPUT / "03-runs.png"), full_page=True)
        assert (run["orders_count"], run["events_count"], run["findings_count"]) == (6, 6, 7), run
        page.locator('nav button[data-page="findings"]').click()
        page.locator('#filters input[name="run_id"]').fill(run_id)
        page.locator('#filters input[name="order_id"]').fill("O102")
        page.get_by_role("button", name="Apply filters").click()
        page.locator("#findings-table tbody tr").filter(has_text=run_id[:8]).filter(
            has_text="O102"
        ).get_by_role("button", name="Inspect", exact=False).click()
        page.locator("#evidence").wait_for(state="visible")
        page.locator("#source-json").filter(has_text="250.00").wait_for()
        page.locator("#review-note").fill(
            "Browser smoke: checked source rows and provider evidence."
        )
        page.locator("#review-action").click()
        page.locator("#review-action").filter(has_text="Reopen finding").wait_for()
        page.screenshot(path=str(OUTPUT / "04-evidence-review.png"), full_page=True)
        response = context.request.get(BASE_URL + "/api/runs/" + run_id + "/findings.csv")
        assert response.ok
        csv_text = response.text()
        assert "AMOUNT_MISMATCH" in csv_text and "MISSING_PAYMENT" in csv_text
        exported = list(csv.DictReader(io.StringIO(csv_text)))
        assert len(exported) == 7 and all(row["run_id"] == run_id for row in exported)
        reviewed = next(row for row in exported if row["entity_id"] == "O102")
        assert reviewed["status"] == "RESOLVED"
        assert (
            reviewed["resolution_note"]
            == "Browser smoke: checked source rows and provider evidence."
        )
        (OUTPUT / "findings.csv").write_text(csv_text, encoding="utf-8")
        page.locator('nav button[data-page="audit"]').click()
        page.locator("#audit-table").filter(has_text="RESOLVE_FINDING").wait_for()
        page.screenshot(path=str(OUTPUT / "05-audit.png"), full_page=True)
        page.locator('nav button[data-page="overview"]').click()
        page.locator("#currencies").filter(has_text="590.00").wait_for()
        page.evaluate("window.scrollTo(0,0)")
        page.screenshot(path=str(OUTPUT / "06-overview-seeded.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(OUTPUT / "07-mobile-overview.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
            "Mobile layout overflows the viewport"
        )
        assert not errors, errors
        (OUTPUT / "result.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "run_id": run_id,
                    "browser_errors": errors,
                    "base_url": BASE_URL,
                    "delivery_mode": os.environ.get(
                        "SMOKE_DELIVERY_MODE", "configured running stack"
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        context.tracing.stop(path=str(OUTPUT / "trace.zip"))
        context.close()
        browser.close()
        print(f"Browser smoke passed; screenshots, recording, export, and trace are in {OUTPUT}.")


if __name__ == "__main__":
    main()
