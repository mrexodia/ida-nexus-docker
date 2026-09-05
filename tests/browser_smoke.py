"""Optional UI check against a generated site: python tests/browser_smoke.py URL.

Requires the Python playwright package and an already installed Chromium browser.
"""

import argparse
from urllib.parse import urljoin

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "url", help="HTTP URL of an exported run with at least one IDA database"
    )
    parser.add_argument("--screenshot")
    parser.add_argument("--overview-screenshot")
    parser.add_argument("--library-url", help="optional converted-run library URL")
    parser.add_argument(
        "--channel", help="use an installed browser, e.g. msedge or chrome"
    )
    args = parser.parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=args.channel)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        errors, failures = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "response",
            lambda response: (
                failures.append(response.url) if response.status >= 400 else None
            ),
        )
        if args.library_url:
            page.goto(args.library_url)
            rows = page.locator("[data-search]")
            assert rows.count() > 0
            page.locator("#search").fill("no-such-run-123")
            expect(page.locator("#empty")).to_be_visible()
            assert page.locator("[data-search]:visible").count() == 0
            page.locator("#search").fill("")
            expect(rows.first).to_be_visible()
            rows.first.locator("a").click()
            expect(page.locator(".file-tree").first).to_be_visible()
        page.goto(args.url)
        expect(page.locator(".file-row").first).to_be_visible()
        catalog = page.request.get(urljoin(args.url, "catalog.json")).json()
        expect(page.locator(".stage")).to_have_count(len(catalog["stages"]))
        assert not page.locator('a[href*="ida-nexus"]').count()
        if catalog["stages"]:
            first_stage = catalog["stages"][0]
            stage = page.locator("#" + first_stage["id"])
            if first_stage["prompt"]:
                expect(stage.locator(".prompt-text")).to_have_text(
                    first_stage["prompt"]
                )
                stage.locator(".stage-prompt summary").click()
                expect(stage.locator(".prompt-text")).not_to_be_visible()
                stage.locator(".stage-prompt summary").click()
            if first_stage["sessions"]:
                assert (
                    stage.locator(".stage-session").first.get_attribute("href")
                    == first_stage["sessions"][0]["page"]
                )
                stage.locator(".stage-session").first.click()
                expect(page.locator(".log-heading h1")).to_contain_text(
                    first_stage["title"]
                )
                page.locator(".stage-back").click()
                expect(page.locator("#" + first_stage["id"])).to_be_visible()
        if args.overview_screenshot:
            page.screenshot(path=args.overview_screenshot, full_page=True)
        # Search opens matching folders, then restores the user's collapsed state.
        root_folder = page.locator(".file-folder").first
        root_folder.locator("> summary").click()
        expect(page.locator(".file-row").first).not_to_be_visible()
        page.locator("#artifact-filter").fill("no-such-artifact-123")
        expect(page.locator("#no-artifacts")).to_be_visible()
        page.locator("#artifact-filter").fill(".i64")
        expect(page.locator('.file-row[data-kind="database"]').first).to_be_visible()
        page.locator("#artifact-filter").fill("")
        expect(page.locator(".file-row").first).not_to_be_visible()
        root_folder.locator("> summary").click()
        databases = page.locator('.file-row[href$=".i64.html"]')
        database_link = databases.last.get_attribute("href")
        page.goto(urljoin(args.url, database_link))
        expect(page.locator(".pseudocode")).to_be_visible(timeout=30000)
        expect(page.locator("#ida-status")).to_contain_text("defined items")
        index_url = urljoin(
            page.url, page.locator("#ida-app").get_attribute("data-index")
        )
        database = page.request.get(index_url).json()
        expected_entry = (database.get("entry_points") or database["functions"])[0][
            "address"
        ]
        expect(page.locator(".selection-meta > code")).to_have_text(expected_entry)
        expect(page.locator("#pseudocode-tab")).to_have_attribute(
            "aria-pressed", "true"
        )
        assert (
            page.locator(".ida-main > .tab-row button").first.get_attribute("id")
            == "pseudocode-tab"
        )
        # Explicit bookmarks still override both the default address and view.
        bookmark = (
            urljoin(args.url, database_link)
            + "#addr="
            + database["functions"][0]["address"]
            + "&view=disassembly"
        )
        page.goto(bookmark)
        expect(page.locator(".assembly-row").first).to_be_visible()
        expect(page.locator(".selection-meta > code")).to_have_text(
            database["functions"][0]["address"]
        )
        page.goto(urljoin(args.url, database_link))
        expect(page.locator(".pseudocode")).to_be_visible()
        expect(page.locator(".selection-meta > code")).to_have_text(expected_entry)
        page.locator("#disassembly-tab").click()
        expect(page.locator(".assembly-row").first).to_be_visible()

        def check_workspace(pane):
            assert page.evaluate(
                "document.documentElement.scrollHeight <= innerHeight"
            ), "Document scrolls vertically"
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), "Document scrolls horizontally"
            bounds = page.locator(pane).bounding_box()
            height = page.viewport_size["height"]
            assert bounds["height"] >= height * 0.65, bounds
            assert bounds["y"] + bounds["height"] <= height, bounds

        for width, height in [(1500, 1000), (1366, 768)]:
            page.set_viewport_size({"width": width, "height": height})
            check_workspace(".listing")
        page.locator(".listing").evaluate("element => element.scrollTop = 300")
        scroll_before = page.locator(".listing").evaluate(
            "element => element.scrollTop"
        )
        page.locator("#pseudocode-tab").click()
        expect(page.locator(".pseudocode")).to_be_visible()
        page.go_back()
        expect(page.locator(".listing")).to_be_visible()
        assert (
            page.locator(".listing").evaluate("element => element.scrollTop")
            == scroll_before
        )
        page.locator("#pseudocode-tab").click()
        expect(page.locator(".pseudocode")).to_be_visible()
        check_workspace(".pseudocode")
        # Sidebar links must adopt the current view, even if built in disassembly.
        page.locator("#item-list a").nth(1).click()
        expect(page.locator(".pseudocode")).to_be_visible()
        assert "view=pseudocode" in page.url
        # Function hyperlinks in pseudocode preserve the decompilation view.
        previous_url = page.url
        function_urls = {
            urljoin(page.url, anchor.get_attribute("href"))
            for anchor in page.locator("#item-list a").all()
        }
        for pseudo_link in page.locator('.pseudocode a[href^="#addr="]').all():
            destination = urljoin(page.url, pseudo_link.get_attribute("href"))
            if destination == previous_url or destination not in function_urls:
                continue
            pseudo_link.click()
            expect(page).to_have_url(destination)
            expect(page.locator(".pseudocode")).to_be_visible()
            page.go_back()
            expect(page).to_have_url(previous_url)
            break
        page.locator("#xrefs-tab").click()
        expect(page.locator(".references").first).to_be_visible()
        # The originating view is in the URL, so shared/reloaded xrefs also return to it.
        assert "return=pseudocode" in page.url
        page.reload()
        expect(page.locator(".references").first).to_be_visible()
        outgoing = page.locator(".reference a").first
        if outgoing.count():
            assert "view=pseudocode" in outgoing.get_attribute("href")
            outgoing.click()
            expect(page.locator(".pseudocode")).to_be_visible()
            check_workspace(".pseudocode")
        page.locator("#disassembly-tab").click()
        expect(page.locator(".assembly-row").first).to_be_visible()
        next_chunk = page.get_by_role("link", name="Next →").first
        if next_chunk.count():
            next_chunk.click()
            expect(page.locator("#selected-row")).to_be_visible()
        page.locator("#types-tab").click()
        if page.locator("#item-list a").count():
            page.locator("#item-list a").first.click()
            expect(page.locator(".pseudocode")).to_be_visible()
        page.locator("#functions-tab").click()
        page.locator("#item-list a").first.click()
        page.locator("#disassembly-tab").click()
        expect(page.locator("#selected-row")).to_be_visible()
        if args.screenshot:
            page.screenshot(path=args.screenshot)
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
        expect(page.locator("#jump")).to_be_visible()
        expect(page.locator(".listing")).to_be_visible()
        check_workspace(".listing")
        expect(page.locator("#symbol-sidebar")).not_to_be_visible()
        page.locator("#toggle-symbols").click()
        expect(page.locator("#symbol-sidebar")).to_be_visible()
        page.locator("#item-list a").first.click()
        expect(page.locator("#symbol-sidebar")).not_to_be_visible()
        page.goto(args.url)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        sample = page.locator('.file-row[data-kind="sample"]').first
        if sample.count():
            sample.click()
            expect(page.locator("#ida-app")).to_be_visible()
            expect(page.locator("#pseudocode-tab")).to_have_attribute(
                "aria-pressed", "true"
            )
        page.goto(args.url)
        log = page.locator('.stage-session, .log-link[href^="logs/"]').first
        if log.count():
            log.click()
            expect(page.locator(".pi-log")).to_be_visible()
            frame = page.frame_locator(".pi-log")
            expect(frame.locator("#messages")).not_to_be_empty()
        assert not errors, errors
        assert not failures, failures
        browser.close()
        print(
            "Browser smoke passed: stages, prompts, file tree, listing, pseudocode, xrefs, types, history, mobile, Pi log."
        )


if __name__ == "__main__":
    main()
