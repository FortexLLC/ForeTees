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

# Members to add to slots 2-4 (name, ForeTees member ID)
GUEST_MEMBERS = [
    ("Brandon Tolle", "Tolle_H1454"),
    ("Eddie Lutz", "Lutz_H1169"),
    ("Mitchell Roth", "Roth_H1542"),
]

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
    # If today is Monday (weekday 0), Saturday is 5 days out
    days_until_saturday = (5 - today.weekday()) % 7
    if days_until_saturday == 0:
        days_until_saturday = 7  # next Saturday if today is already Saturday
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
            # Sub-second precision — tight spin loop
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
        # Try loading from .env for local development
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

    tz = pytz.timezone(MOUNTAIN_TZ)
    today = now_mt()

    # Calculate login time (2 minutes before booking window)
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
            # ForeTees login form — fields identified by placeholder text
            page.fill('input[placeholder="Username"]', member_id)
            page.fill('input[placeholder="Password"]', password)

            # Submit the login form — "SIGN IN" button
            page.click('button:has-text("SIGN IN"), input[value="SIGN IN"]')
            page.wait_for_load_state("networkidle", timeout=15000)
            log.info("Login submitted, waiting for page to load...")
            time.sleep(3)
            take_screenshot(page, "after_login")
            log.info(f"Post-login URL: {page.url}")

            # ---------------------------------------------------------------
            # Step 4 — Navigate to Tee Times > Make, Change, or View
            # ---------------------------------------------------------------
            log.info("Navigating to Tee Times...")

            # Try clicking the Tee Times menu item
            tee_times_link = page.locator('text="Tee Times"').first
            if tee_times_link.is_visible():
                tee_times_link.click()
                time.sleep(1)

            # Click the sub-menu item
            make_tee_times = page.locator('text="Make, Change, or View Tee Times"').first
            if make_tee_times.is_visible(timeout=5000):
                make_tee_times.click()
            else:
                # Fallback: try partial text match
                make_tee_times = page.locator('a:has-text("Make"), a:has-text("Tee Sheet")').first
                make_tee_times.click()

            page.wait_for_load_state("networkidle", timeout=15000)
            log.info("Tee sheet page loaded.")
            time.sleep(1)

            # ---------------------------------------------------------------
            # Step 5 — Select Saturday on the calendar (BEFORE 7am)
            # ---------------------------------------------------------------
            sat_day = target_saturday.day
            sat_month = target_saturday.strftime("%B")
            sat_date_str = f"{sat_month} {sat_day}"
            log.info(f"Selecting Saturday: {sat_date_str}")

            # Strategy A: click by link text matching the day number
            sat_clicked = False
            try:
                # Try clicking a calendar cell with the day number
                # ForeTees calendars often use links or buttons with the day number
                day_link = page.locator(f'a:has-text("{sat_day}"), td:has-text("{sat_day}")').first
                day_link.click(timeout=5000)
                sat_clicked = True
                log.info(f"Clicked Saturday (strategy A: day text '{sat_day}')")
            except Exception:
                pass

            # Strategy B: try aria-label or title attribute
            if not sat_clicked:
                try:
                    sat_cell = page.locator(
                        f'[aria-label*="{sat_date_str}"], '
                        f'[title*="{sat_date_str}"], '
                        f'[data-date="{target_saturday.isoformat()}"]'
                    ).first
                    sat_cell.click(timeout=5000)
                    sat_clicked = True
                    log.info(f"Clicked Saturday (strategy B: aria-label/title)")
                except Exception:
                    pass

            if not sat_clicked:
                log.error(f"Could not find Saturday ({sat_date_str}) on the calendar.")
                take_screenshot(page, "error")
                sys.exit(1)

            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(1)
            log.info("Saturday tee sheet loaded (pre-7am — tee times not yet available).")

            # ---------------------------------------------------------------
            # Step 6 — Wait until exactly 7:00:00am MT
            # ---------------------------------------------------------------
            log.info("Waiting for booking window to open at 7:00:00am MT...")
            wait_until(booking_time, "booking window")

            # ---------------------------------------------------------------
            # Step 7 — Refresh the page to load available tee times
            # ---------------------------------------------------------------
            log.info("Refreshing page to load tee times...")
            page.reload(wait_until="networkidle", timeout=15000)
            time.sleep(2)
            log.info("Page refreshed — scanning for available tee times.")

            # ---------------------------------------------------------------
            # Step 8 — Select the first available tee time
            # ---------------------------------------------------------------
            log.info("Looking for first available tee time slot...")

            # ForeTees v5 tee sheet — best-guess selectors for available slots
            # These target common patterns: green cells, "available" text, open slots
            available_slot = None
            selectors = [
                'td.pointed:not(.booked)',           # Common ForeTees pattern
                'td[onclick]:not(.booked)',           # Clickable cells
                'a:has-text("Available")',            # Text-based
                '.teeSlot:not(.booked)',              # Class-based
                'td.open',                            # Open slot
                'tr.pointed td:first-child',          # First column of pointed row
            ]

            for selector in selectors:
                try:
                    slot = page.locator(selector).first
                    if slot.is_visible(timeout=2000):
                        available_slot = slot
                        log.info(f"Found available slot with selector: {selector}")
                        break
                except Exception:
                    continue

            if not available_slot:
                log.error("No available tee time slots found.")
                take_screenshot(page, "error")
                sys.exit(1)

            available_slot.click()
            log.info("Clicked first available tee time. Waiting for slot lock...")
            time.sleep(3)  # Wait for ForeTees to lock the slot

            # ---------------------------------------------------------------
            # Step 9 — Add the three additional members
            # ---------------------------------------------------------------
            log.info("Adding guest members to the booking...")

            # Click Members button/tab if present
            try:
                members_btn = page.locator('text="Members", a:has-text("Members"), button:has-text("Members")').first
                if members_btn.is_visible(timeout=3000):
                    members_btn.click()
                    time.sleep(1)
            except Exception:
                log.info("No separate Members button found — member fields may already be visible.")

            for i, (name, member_id_guest) in enumerate(GUEST_MEMBERS, start=2):
                log.info(f"Adding player {i}: {name} ({member_id_guest})")
                try:
                    # Find the search/input field for this player slot
                    # ForeTees typically has input fields for each player slot
                    search_field = page.locator(
                        f'input[name*="player{i}"], '
                        f'input[name*="slot{i}"], '
                        f'input[name*="mem{i}"], '
                        f'input.playerSearch:nth-of-type({i}), '
                        f'input[placeholder*="Member"]:nth-of-type({i-1})'
                    ).first

                    search_field.click()
                    search_field.fill(member_id_guest)

                    # Wait for autocomplete suggestions
                    time.sleep(0.8)

                    # Click the matching autocomplete result
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

            # Take confirmation screenshot
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
