import os
import re
import sys
from playwright.sync_api import sync_playwright, expect

def test_flow_email_subscription_invalid():
    headless = os.getenv("HEADLESS", "false").lower() in (
        "true",
        "1",
        "yes",
    )
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        try:
            page.goto('https://www.automationexercise.com/', wait_until='domcontentloaded', timeout=30000)

            # Step 1: Enter an invalid email address in the subscription input
            email_input = page.locator("#susbscribe_email")
            email_input.scroll_into_view_if_needed()
            email_input.fill("invalid-email-format")

            # Step 2: Click on the subscription button
            page.click("button.btn.btn-success")

            # Assert that an error message is displayed
            expect(page.locator(".error, .alert, [role='alert'], [data-test='error']")).to_be_visible()

            print(f"[FINAL_URL] {page.url}")
            context.storage_state(path=os.path.splitext(os.path.abspath(__file__))[0] + ".storage_state.json")
            print("[TEST PASSED] Subscription is rejected with an invalid email format")
        except Exception as exc:
            try:
                print(f"[FAILURE_URL] {page.url}")
                error_texts = page.locator(".error, .alert, [role='alert'], [data-test='error'], h1, h2, h3").all_inner_texts()
                clean_errors = [t.strip() for t in error_texts if t.strip()]
                if clean_errors:
                    import json
                    print(f"[VISIBLE_ERRORS] {json.dumps(clean_errors[:5])}")
                page.screenshot(path=os.path.splitext(os.path.abspath(__file__))[0] + "_failure.png")
            except Exception:
                pass
            raise exc
        finally:
            context.close()
            browser.close()

if __name__ == "__main__":
    test_flow_email_subscription_invalid()