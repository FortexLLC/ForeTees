#!/usr/bin/env python3
"""
Diagnostic: Dump tee sheet button attributes to see if slot availability
is encoded in the HTML (e.g., data-ftjson) without clicking each button.
"""

import os
import sys
import json
import time
import logging

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.FileHandler("diagnose_teesheet.log"), logging.StreamHandler(sys.stdout)])
log = logging.getLogger(__name__)

FORETEES_URL = "https://web.foretees.com/v5/servlet/LoginPrompt?cn=inverness"
TARGET_DAY = "19"  # Thursday March 19

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
            time.sleep(3)
            log.info(f"Logged in. URL: {page.url}")

            # Navigate to tee sheet
            page.locator('text="Tee Times"').first.click()
            time.sleep(1)
            page.locator('text="Make, Change, or View Tee Times"').first.click()
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(1)

            # Select target day
            page.locator(f'a:has-text("{TARGET_DAY}"), td:has-text("{TARGET_DAY}")').first.click()
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(1)
            log.info("Tee sheet loaded.")

            # Dump ALL teetime_button attributes
            button_data = page.evaluate('''() => {
                const buttons = document.querySelectorAll("a.teetime_button");
                const results = [];
                for (const btn of buttons) {
                    const attrs = {};
                    for (const attr of btn.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    // Also grab the parent row's HTML structure
                    const row = btn.closest("[class*='row'], [class*='slot'], div");
                    const rowInfo = row ? {
                        tag: row.tagName,
                        class: row.className.toString().substring(0, 100),
                        id: row.id,
                        childCount: row.children.length
                    } : null;

                    results.push({
                        text: btn.textContent.trim(),
                        outerHTML: btn.outerHTML.substring(0, 500),
                        attributes: attrs,
                        parentRow: rowInfo
                    });
                }
                return results;
            }''')

            log.info(f"\n{'='*80}\nFound {len(button_data)} teetime_button(s)\n{'='*80}")
            for i, btn in enumerate(button_data):
                log.info(f"\n--- Button {i+1}: '{btn['text']}' ---")
                log.info(f"outerHTML: {btn['outerHTML']}")
                if 'data-ftjson' in btn.get('attributes', {}):
                    try:
                        ftjson = json.loads(btn['attributes']['data-ftjson'])
                        log.info(f"data-ftjson (parsed): {json.dumps(ftjson, indent=2)}")
                    except:
                        log.info(f"data-ftjson (raw): {btn['attributes']['data-ftjson']}")
                else:
                    log.info(f"All attributes: {btn['attributes']}")
                log.info(f"Parent row: {btn.get('parentRow')}")

            # Also dump the broader tee sheet row structure for context
            row_data = page.evaluate('''() => {
                // Look for the container holding tee time rows
                const rows = document.querySelectorAll("[class*='teetime'], [class*='teeRow'], [class*='ft-row']");
                const results = [];
                for (let i = 0; i < Math.min(rows.length, 5); i++) {
                    results.push({
                        tag: rows[i].tagName,
                        class: rows[i].className.toString().substring(0, 150),
                        id: rows[i].id,
                        innerHTML: rows[i].innerHTML.substring(0, 800)
                    });
                }
                return results;
            }''')
            if row_data:
                log.info(f"\n{'='*80}\nTee sheet row structure (first 5):\n{'='*80}")
                for i, row in enumerate(row_data):
                    log.info(f"\n--- Row {i+1} ---")
                    log.info(f"Tag: {row['tag']}, Class: {row['class']}, ID: {row['id']}")
                    log.info(f"innerHTML: {row['innerHTML']}")

            # Screenshot for reference
            page.screenshot(path="diagnose_teesheet.png", full_page=True)
            log.info("Screenshot saved: diagnose_teesheet.png")

        finally:
            browser.close()

if __name__ == "__main__":
    run()
