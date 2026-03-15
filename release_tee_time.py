#!/usr/bin/env python3
"""Quick script to release a locked tee time by clicking Go Back."""

import os, sys, time, logging
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.FileHandler("release.log"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

FORETEES_URL = "https://web.foretees.com/v5/servlet/LoginPrompt?cn=inverness"
TARGET_DAY = "20"

def run():
    member_id = os.environ.get("FORETEES_MEMBER")
    password = os.environ.get("FORETEES_PASSWORD")
    if not member_id or not password:
        try:
            from dotenv import load_dotenv
            load_dotenv()
            member_id = os.environ.get("FORETEES_MEMBER")
            password = os.environ.get("FORETEES_PASSWORD")
        except ImportError:
            pass
    if not member_id or not password:
        log.error("Set FORETEES_MEMBER and FORETEES_PASSWORD"); sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        try:
            # Login
            page.goto(FORETEES_URL, wait_until="networkidle", timeout=30000)
            page.fill('#user_name', member_id)
            page.fill('#password', password)
            page.click('input.button-primary[type="submit"]')
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)
            log.info(f"Logged in. URL: {page.url}")

            # Navigate to tee sheet
            page.locator('text="Tee Times"').first.click()
            time.sleep(1)
            page.locator('text="Make, Change, or View Tee Times"').first.click()
            page.wait_for_load_state("networkidle", timeout=15000)

            # Select day
            page.locator(f'a:has-text("{TARGET_DAY}"), td:has-text("{TARGET_DAY}")').first.click()
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(1)
            log.info("Tee sheet loaded.")

            # Click the first available tee time button (will trigger "Not Allowed" or booking form)
            btn = page.locator('a.teetime_button').first
            btn_text = btn.text_content().strip()
            log.info(f"Clicking tee time: {btn_text}")
            btn.click()
            time.sleep(3)
            page.screenshot(path="release_before.png")

            body_text = page.text_content("body").lower()
            if "not allowed" in body_text or "go back" in body_text:
                log.info("Found 'Not Allowed' or 'Go Back' page. Clicking Go Back...")
                go_back = page.locator('a:has-text("Go Back"), button:has-text("Go Back"), input[value="Go Back"]').first
                go_back.click(timeout=5000)
                page.wait_for_load_state("networkidle", timeout=10000)
                log.info("Clicked Go Back — tee time released!")
            elif page.locator('input.ftS-playerNameInput').first.is_visible(timeout=3000):
                log.info("Booking form loaded (we're in this tee time). Clicking Go Back...")
                go_back = page.locator('a:has-text("Go Back"), button:has-text("Go Back"), input[value="Go Back"]').first
                go_back.click(timeout=5000)
                page.wait_for_load_state("networkidle", timeout=10000)
                log.info("Clicked Go Back — tee time released!")
            else:
                log.warning("Unexpected page state.")

            page.screenshot(path="release_after.png")
            log.info("Done.")

        finally:
            browser.close()

if __name__ == "__main__":
    run()
