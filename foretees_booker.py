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
            #   Available rows have <a class="teetime_button"> with data-ftjson.
            #   Some buttons lead to partially booked slots. We click each button,
            #   check how many player name fields are empty on the booking form,
            #   and go back if fewer than MIN_OPEN_SLOTS are open.

            buttons = page.locator('a.teetime_button')
            button_count = buttons.count()
            log.info(f"Found {button_count} available teetime_button(s)")

            if button_count == 0:
                log.error("No available tee time buttons found on the tee sheet.")
                take_screenshot(page, "error_no_slots")
                sys.exit(1)

            # Try each teetime_button until we find one with enough open slots
            selected_time = None
            for btn_idx in range(button_count):
                btn = buttons.nth(btn_idx)
                button_text = btn.text_content().strip()
                log.info(f"Trying tee time button {btn_idx + 1}/{button_count}: '{button_text}'")
                btn.click()
                time.sleep(3)
                page.wait_for_load_state("networkidle", timeout=15000)

                # Count empty player name fields on the booking form
                empty_slots = page.evaluate('''() => {
                    const inputs = document.querySelectorAll("input.ftS-playerNameInput");
                    let empty = 0;
                    for (const inp of inputs) {
                        if (!inp.value || inp.value.trim() === "") empty++;
                    }
                    return empty;
                }''')
                log.info(f"Tee time '{button_text}': {empty_slots} empty slot(s)")

                if empty_slots >= MIN_OPEN_SLOTS:
                    selected_time = button_text
                    log.info(f"Found tee time with {empty_slots} open slots: '{button_text}'")
                    break
                else:
                    log.info(f"Only {empty_slots} open slots, need {MIN_OPEN_SLOTS}. Going back...")
                    page.go_back(wait_until="networkidle", timeout=15000)
                    time.sleep(2)
                    # Re-locate buttons after navigation
                    buttons = page.locator('a.teetime_button')

            if not selected_time:
                log.error(f"No tee time found with {MIN_OPEN_SLOTS} open slots.")
                take_screenshot(page, "error_no_open_slots")
                sys.exit(1)

            take_screenshot(page, "after_slot_click")

            # ---------------------------------------------------------------
            # Step 9 — Add the three additional members
            # ---------------------------------------------------------------
            log.info("Adding guest members to the booking...")

            # Dismiss the "Adding a Member or Guest" info dialog if it appears
            try:
                close_btn = page.locator('button:has-text("Close")').first
                if close_btn.is_visible(timeout=2000):
                    close_btn.click()
                    time.sleep(0.5)
                    log.info("Dismissed info dialog.")
            except Exception:
                pass

            # Find empty player slots (slot_player_row_N containers)
            empty_slot_indices = page.evaluate('''() => {
                const indices = [];
                for (let i = 0; i < 4; i++) {
                    const row = document.getElementById("slot_player_row_" + i);
                    if (row) {
                        const nameInput = row.querySelector("input.ftS-playerNameInput");
                        if (nameInput && (!nameInput.value || nameInput.value.trim() === "")) {
                            indices.push(i);
                        }
                    }
                }
                return indices;
            }''')
            log.info(f"Empty player slot indices: {empty_slot_indices}")

            # Fill all empty slots with guests (slot 0 has the account owner)
            # Slot indices are 0-based; player numbers displayed are 1-based
            for slot_idx, (name, member_id_guest) in zip(empty_slot_indices, GUEST_MEMBERS):
                player_num = slot_idx + 1  # 1-based display number
                log.info(f"Adding {name} ({member_id_guest}) to player {player_num} (slot {slot_idx})...")
                try:
                    # Click the player row to set the pointer to that slot
                    page.locator(f'#slot_player_row_{slot_idx}').click()
                    time.sleep(0.5)

                    # Dismiss any dialog that pops up
                    try:
                        dlg_close = page.locator('button:has-text("Close")').first
                        if dlg_close.is_visible(timeout=1000):
                            dlg_close.click()
                            time.sleep(0.3)
                    except Exception:
                        pass

                    # Click the "Members" tab — use force + no_wait_after since
                    # the <a href="Member_searchmem"> causes Playwright to wait
                    # for navigation that never completes (it's an AJAX action)
                    members_link = page.locator('a[href*="searchmem"]').first
                    members_link.click(force=True, no_wait_after=True)
                    time.sleep(2)
                    log.info("Clicked Members tab.")

                    # Dump right panel HTML for debugging
                    panel_html = page.evaluate('''() => {
                        const panel = document.querySelector("#playerSelectPanel, .ftMs-container, [class*=playerSelect]");
                        return panel ? panel.innerHTML.substring(0, 2000) : "No panel found";
                    }''')
                    log.info(f"Right panel HTML: {panel_html[:500]}")
                    take_screenshot(page, f"members_tab_slot{slot_idx}")

                    # Check if ftMs-input is now visible; if not, try jQuery trigger
                    search_visible = page.locator('input.ftMs-input').first.is_visible()
                    if not search_visible:
                        log.info("ftMs-input not visible, trying jQuery trigger...")
                        page.evaluate('''() => {
                            if (typeof jQuery !== "undefined") {
                                jQuery('a[href*="searchmem"]').trigger("click");
                            }
                        }''')
                        time.sleep(2)
                        take_screenshot(page, f"jquery_members_slot{slot_idx}")

                    # Type the member number into the search field for accuracy
                    search_input = page.locator('input.ftMs-input').first
                    search_input.click(force=True, timeout=5000)
                    search_input.fill(member_id_guest)
                    time.sleep(1.5)
                    log.info(f"Typed '{member_id_guest}' in member search.")
                    take_screenshot(page, f"search_slot{slot_idx}")

                    # Click the matching result from the search results
                    result = page.locator(f'li:has-text("{name.split()[1]}"), .ftMs-resultName:has-text("{name.split()[1]}")').first
                    result.click(timeout=5000)
                    log.info(f"Added {name} to player {player_num}.")
                    time.sleep(1)

                except Exception as e:
                    log.warning(f"Could not add {name} to player {player_num}: {e}")
                    take_screenshot(page, f"error_player{player_num}")

            # ---------------------------------------------------------------
            # Step 10 — Verify members were added, then confirm booking
            # ---------------------------------------------------------------
            # Check how many slots are now filled
            filled_count = page.evaluate('''() => {
                const inputs = document.querySelectorAll("input.ftS-playerNameInput");
                let filled = 0;
                for (const inp of inputs) {
                    if (inp.value && inp.value.trim() !== "") filled++;
                }
                return filled;
            }''')
            log.info(f"Filled player slots: {filled_count} / 4")
            if filled_count < 4:
                log.error(f"Only {filled_count} players filled — expected 4. NOT submitting.")
                take_screenshot(page, "error_incomplete")
                sys.exit(1)

            log.info("All 4 players filled. Confirming the booking...")
            take_screenshot(page, "before_submit")
            try:
                # Submit button may be <a>, <button>, or <input> element
                confirm_btn = page.locator(
                    'a:has-text("Submit Request"), '
                    'button:has-text("Submit Request"), '
                    'input[value="Submit Request"]'
                ).first
                confirm_btn.click(timeout=5000)
                page.wait_for_load_state("networkidle", timeout=15000)
                log.info("Booking submitted!")
                time.sleep(3)
            except Exception as e:
                log.error(f"Could not click Submit Request button: {e}")
                take_screenshot(page, "error_submit")
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
