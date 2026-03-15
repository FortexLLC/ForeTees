#!/usr/bin/env python3
"""
ForeTees Auto-Booker — Inverness Club
Automatically books the first available Saturday tee time every Monday at 7:00am MT.
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta

import pytz
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FORETEES_URL = "https://web.foretees.com/v5/servlet/LoginPrompt?cn=inverness"
BOOKING_HOUR = 7        # Hour the booking window opens (Mountain Time)
BOOKING_MINUTE = 0      # Minute the booking window opens
LOGIN_EARLY_MIN = 2     # Minutes before 7am to log in
MOUNTAIN_TZ = "America/Denver"  # Handles MDT/MST automatically via pytz

# TEST MODE: Set to a specific date string (e.g. "19") to override Saturday selection,
# and skip the 7am wait. Set to None for production.
TEST_DAY_OVERRIDE = "19"  # Thursday March 19 for testing

# Members to add to slots 2-4 (name, ForeTees member ID)
GUEST_MEMBERS = [
    ("Brandon Tolle", "Tolle_H1454"),
    ("Eddie Lutz", "Lutz_H1169"),
    ("Mitchell Roth", "Roth_H1542"),
]

# Minimum open slots required (you auto-fill slot 1 + 3 guests = all 4 must be open)
MIN_OPEN_SLOTS = 4

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("foretees_booker.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_mt():
    """Return the current datetime in Mountain Time."""
    return datetime.now(pytz.timezone(MOUNTAIN_TZ))


def get_target_saturday():
    """Calculate the Saturday that is 5 days from today (Monday)."""
    today = now_mt().date()
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7
    target = today + timedelta(days=days_until_saturday)
    log.info(f"Target Saturday: {target.strftime('%B %d, %Y')} ({days_until_saturday} days from today)")
    return target


def wait_until(target_dt, label="target"):
    """Sleep until the target datetime, logging progress."""
    while True:
        remaining = (target_dt - now_mt()).total_seconds()
        if remaining <= 0:
            log.info(f"Reached {label} time: {now_mt().strftime('%H:%M:%S.%f')}")
            return
        if remaining > 60:
            log.info(f"Waiting for {label}... {remaining:.0f}s remaining")
            time.sleep(30)
        elif remaining > 1:
            time.sleep(0.5)
        else:
            time.sleep(0.01)


def take_screenshot(page, prefix="screenshot"):
    """Save a timestamped screenshot."""
    ts = now_mt().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{ts}.png"
    page.screenshot(path=filename, full_page=True)
    log.info(f"Screenshot saved: {filename}")
    return filename


# ---------------------------------------------------------------------------
# Main booking flow
# ---------------------------------------------------------------------------

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
        log.error("FORETEES_MEMBER and FORETEES_PASSWORD must be set as environment variables.")
        sys.exit(1)

    today = now_mt()
    booking_time = today.replace(hour=BOOKING_HOUR, minute=BOOKING_MINUTE, second=0, microsecond=0)
    login_time = booking_time - timedelta(minutes=LOGIN_EARLY_MIN)

    log.info(f"Current time (MT): {today.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    log.info(f"Login time: {login_time.strftime('%H:%M:%S')}")
    log.info(f"Booking window opens: {booking_time.strftime('%H:%M:%S')}")

    # -----------------------------------------------------------------------
    # Step 1 — Wait until login time (6:58am MT)
    # -----------------------------------------------------------------------
    wait_until(login_time, "login")

    target_saturday = get_target_saturday()

    with sync_playwright() as p:
        # -------------------------------------------------------------------
        # Step 2 — Launch headless Chromium
        # -------------------------------------------------------------------
        log.info("Launching browser...")
        browser = p.chromium.launch(
            headless=True,  # Set to False for local debugging
            args=["--no-sandbox"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        try:
            # ---------------------------------------------------------------
            # Step 3 — Log in to ForeTees
            # ---------------------------------------------------------------
            log.info(f"Navigating to ForeTees login: {FORETEES_URL}")
            page.goto(FORETEES_URL, wait_until="networkidle", timeout=30000)

            log.info("Filling login credentials...")
            page.fill('#user_name', member_id)
            page.fill('#password', password)
            page.click('input.button-primary[type="submit"]')
            page.wait_for_load_state("networkidle", timeout=15000)
            log.info("Login submitted, waiting for page to load...")
            time.sleep(3)
            log.info(f"Post-login URL: {page.url}")

            # Verify login succeeded
            if "LoginPrompt" in page.url:
                log.error("Login failed — still on login page. Check credentials.")
                take_screenshot(page, "error_login")
                sys.exit(1)
            log.info("Login successful.")

            # ---------------------------------------------------------------
            # Step 4 — Navigate to Tee Times > Make, Change, or View
            # ---------------------------------------------------------------
            log.info("Navigating to Tee Times...")
            tee_times_link = page.locator('text="Tee Times"').first
            if tee_times_link.is_visible():
                tee_times_link.click()
                time.sleep(1)

            make_tee_times = page.locator('text="Make, Change, or View Tee Times"').first
            if make_tee_times.is_visible(timeout=5000):
                make_tee_times.click()
            else:
                make_tee_times = page.locator('a:has-text("Make"), a:has-text("Tee Sheet")').first
                make_tee_times.click()

            page.wait_for_load_state("networkidle", timeout=15000)
            log.info("Tee sheet page loaded.")
            time.sleep(1)

            # ---------------------------------------------------------------
            # Step 5 — Select target day on the calendar
            # ---------------------------------------------------------------
            if TEST_DAY_OVERRIDE:
                sat_day = int(TEST_DAY_OVERRIDE)
                log.info(f"TEST MODE: Selecting day {sat_day} instead of Saturday")
            else:
                sat_day = target_saturday.day
                log.info(f"Selecting Saturday: {target_saturday.strftime('%B')} {sat_day}")

            sat_clicked = False
            try:
                day_link = page.locator(f'a:has-text("{sat_day}"), td:has-text("{sat_day}")').first
                day_link.click(timeout=5000)
                sat_clicked = True
                log.info(f"Clicked Saturday (day text '{sat_day}')")
            except Exception:
                pass

            if not sat_clicked:
                sat_date_str = f"{target_saturday.strftime('%B')} {sat_day}"
                try:
                    sat_cell = page.locator(
                        f'[aria-label*="{sat_date_str}"], '
                        f'[title*="{sat_date_str}"], '
                        f'[data-date="{target_saturday.isoformat()}"]'
                    ).first
                    sat_cell.click(timeout=5000)
                    sat_clicked = True
                except Exception:
                    pass

            if not sat_clicked:
                log.error("Could not find Saturday on the calendar.")
                take_screenshot(page, "error")
                sys.exit(1)

            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(1)
            log.info("Saturday tee sheet loaded.")

            # ---------------------------------------------------------------
            # Step 6 — Wait until exactly 7:00:00am MT
            # ---------------------------------------------------------------
            if TEST_DAY_OVERRIDE:
                log.info("TEST MODE: Skipping 7am wait.")
            else:
                log.info("Waiting for booking window to open at 7:00:00am MT...")
                wait_until(booking_time, "booking window")

                # ---------------------------------------------------------------
                # Step 7 — Refresh the page to load available tee times
                # ---------------------------------------------------------------
                log.info("Refreshing page to load tee times...")
                page.reload(wait_until="networkidle", timeout=15000)
                time.sleep(2)
                log.info("Page refreshed.")

            # ---------------------------------------------------------------
            # Step 8 — Find first tee time with enough open slots
            # ---------------------------------------------------------------
            log.info(f"Scanning for tee time with at least {MIN_OPEN_SLOTS} open slots...")

            # ForeTees div-based tee sheet:
            #   Each row: <div class="rwdTr ...">
            #   Player columns: <div class="rwdTd pgCol"> — empty = open slot
            #   Fully booked rows have class "hasRowColor", open rows have "noRowColor"
            #   Partially booked rows may have "hasRowColor" but with some empty pgCol divs
            slot_index = page.evaluate('''(minOpen) => {
                const rows = document.querySelectorAll(".rwdTr");
                for (let i = 0; i < rows.length; i++) {
                    const row = rows[i];
                    const timeSlot = row.querySelector(".time_slot");
                    if (!timeSlot) continue;

                    // Count empty player columns (pgCol divs with no text content)
                    const pgCols = row.querySelectorAll(".pgCol");
                    let openSlots = 0;
                    for (const col of pgCols) {
                        if (col.textContent.trim() === "") openSlots++;
                    }

                    if (openSlots >= minOpen) {
                        return {
                            index: i,
                            time: timeSlot.textContent.trim(),
                            openSlots: openSlots,
                            totalPgCols: pgCols.length
                        };
                    }
                }
                return null;
            }''', MIN_OPEN_SLOTS)

            if not slot_index:
                log.error(f"No tee time found with at least {MIN_OPEN_SLOTS} open slots.")
                take_screenshot(page, "error_no_slots")
                sys.exit(1)

            log.info(f"Found tee time: {slot_index['time']} with {slot_index['openSlots']} open slots")

            # Click the time_slot div in that row
            target_row = page.locator('.rwdTr').nth(slot_index['index'])
            target_row.locator('.time_slot').click()
            log.info(f"Clicked tee time {slot_index['time']}. Waiting for booking form...")
            time.sleep(3)
            page.wait_for_load_state("networkidle", timeout=15000)
            take_screenshot(page, "after_slot_click")

            # Dump page state for debugging the booking form
            page_info = page.evaluate('''() => {
                const inputs = document.querySelectorAll("input[type=text], input[type=search], select");
                let info = "URL: " + window.location.href + "\\n";
                info += "Inputs: " + inputs.length + "\\n";
                for (const inp of inputs) {
                    info += "  <" + inp.tagName + " name='" + inp.name + "' id='" + inp.id
                         + "' placeholder='" + (inp.placeholder || "") + "' class='" + inp.className + "'>\\n";
                }
                const playerEls = document.querySelectorAll("[class*=player], [class*=Player], [id*=player], [id*=Player]");
                info += "Player elements: " + playerEls.length + "\\n";
                for (const pe of playerEls) {
                    info += "  <" + pe.tagName + " id='" + pe.id + "' class='" + pe.className + "'>\\n";
                }
                return info;
            }''')
            log.info(f"Booking form:\n{page_info}")

            # ---------------------------------------------------------------
            # Step 9 — Add the three additional members
            # ---------------------------------------------------------------
            log.info("Adding guest members to the booking...")

            # Try clicking Members button/tab if present
            try:
                members_btn = page.locator('text="Members", a:has-text("Members"), button:has-text("Members")').first
                if members_btn.is_visible(timeout=3000):
                    members_btn.click()
                    time.sleep(1)
            except Exception:
                log.info("No separate Members button found.")

            for i, (name, member_id_guest) in enumerate(GUEST_MEMBERS, start=2):
                log.info(f"Adding player {i}: {name} ({member_id_guest})")
                try:
                    # ForeTees player search fields — try multiple selector patterns
                    search_field = page.locator(
                        f'input[name*="player{i}"], '
                        f'input[name*="slot{i}"], '
                        f'input[name*="mem{i}"], '
                        f'input.playerSearch:nth-of-type({i}), '
                        f'input[placeholder*="Member"]:nth-of-type({i-1})'
                    ).first

                    search_field.click()
                    search_field.fill(member_id_guest)
                    time.sleep(0.8)

                    # Click the autocomplete result
                    autocomplete_result = page.locator(
                        f'text="{name}", '
                        f'text="{member_id_guest}", '
                        f'.autocomplete-item:first-child, '
                        f'.suggestion:first-child, '
                        f'li:has-text("{name.split()[1]}")'
                    ).first
                    autocomplete_result.click(timeout=3000)
                    log.info(f"Added {name} to slot {i}.")
                    time.sleep(0.5)

                except Exception as e:
                    log.warning(f"Could not add {name} to slot {i}: {e}")
                    take_screenshot(page, f"error_player{i}")

            # ---------------------------------------------------------------
            # Step 10 — Confirm and save the booking
            # ---------------------------------------------------------------
            log.info("Confirming the booking...")
            try:
                confirm_btn = page.locator(
                    'input[value="Submit"], '
                    'input[value="Confirm"], '
                    'button:has-text("Submit"), '
                    'button:has-text("Confirm"), '
                    'button:has-text("Save"), '
                    'input[type="submit"]'
                ).first
                confirm_btn.click(timeout=5000)
                page.wait_for_load_state("networkidle", timeout=15000)
                log.info("Booking confirmed!")
                time.sleep(2)
            except Exception as e:
                log.error(f"Could not click confirm/save button: {e}")
                take_screenshot(page, "error")
                sys.exit(1)

            take_screenshot(page, "booking_confirmation")
            log.info("Booking process complete.")

        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)
            take_screenshot(page, "error")
            sys.exit(1)

        finally:
            browser.close()
            log.info("Browser closed.")


if __name__ == "__main__":
    run()
