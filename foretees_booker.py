#!/usr/bin/env python3
"""
ForeTees Auto-Booker — Inverness Club
Automatically books the first available Saturday tee time every Monday at 7:00am MT.
"""

import os
import sys
import time
import json
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
TEST_DAY_OVERRIDE = None  # Set to a day string (e.g. "19") for testing

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

            # FAST FILTER: Each teetime_button has a data-ftjson attribute with
            # wasP1..wasP4 fields showing who's booked. Empty string = open slot.
            # We parse this JSON on the tee sheet page to find the first button
            # with all 4 slots open — no clicking/navigating needed.

            scan_results = page.evaluate('''() => {
                const buttons = document.querySelectorAll("a.teetime_button");
                const results = [];
                for (let i = 0; i < buttons.length; i++) {
                    const btn = buttons[i];
                    const raw = btn.getAttribute("data-ftjson");
                    if (!raw) continue;
                    try {
                        const d = JSON.parse(raw);
                        const slots = ["wasP1", "wasP2", "wasP3", "wasP4"];
                        const openCount = slots.filter(k => !d[k] || d[k].trim() === "").length;
                        results.push({
                            index: i,
                            time: d["time:0"] || btn.textContent.trim(),
                            openCount: openCount,
                            wasP1: d.wasP1 || "",
                            wasP2: d.wasP2 || "",
                            wasP3: d.wasP3 || "",
                            wasP4: d.wasP4 || ""
                        });
                    } catch(e) {}
                }
                return results;
            }''')

            log.info(f"Found {len(scan_results)} teetime_button(s) on sheet")
            for r in scan_results:
                log.info(f"  {r['time']}: {r['openCount']} open  "
                         f"[{r['wasP1'] or '—'}, {r['wasP2'] or '—'}, "
                         f"{r['wasP3'] or '—'}, {r['wasP4'] or '—'}]")

            # Find the first button with enough open slots
            target = None
            for r in scan_results:
                if r['openCount'] >= MIN_OPEN_SLOTS:
                    target = r
                    break

            if not target:
                log.error(f"No tee time found with {MIN_OPEN_SLOTS} open slots.")
                take_screenshot(page, "error_no_open_slots")
                sys.exit(1)

            selected_time = target['time']
            log.info(f"Selected tee time: '{selected_time}' (button index {target['index']}, "
                     f"{target['openCount']} open slots)")

            # Click the winning button
            buttons = page.locator('a.teetime_button')
            buttons.nth(target['index']).click()
            time.sleep(3)
            page.wait_for_load_state("networkidle", timeout=15000)
            log.info(f"Clicked tee time '{selected_time}' — booking form loaded.")
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
            # From the screen recording: the Members tab search is in the right
            # panel. The pointer starts at Player #2, and auto-advances after
            # each member is added. Just type in search and click results.

            # Dump the ancestor chain of ftMs-input to find what's hiding it
            ancestor_info = page.evaluate('''() => {
                let el = document.querySelector("input.ftMs-input");
                if (!el) return "ftMs-input not found!";
                let info = "";
                let depth = 0;
                while (el && depth < 15) {
                    const cs = getComputedStyle(el);
                    info += depth + ": <" + el.tagName + " id='" + el.id + "' class='" + (el.className || "").toString().substring(0, 60)
                          + "'> computed: display=" + cs.display + " visibility=" + cs.visibility
                          + " opacity=" + cs.opacity + " height=" + cs.height
                          + " offsetParent=" + (el.offsetParent ? el.offsetParent.tagName : "null") + "\\n";
                    el = el.parentElement;
                    depth++;
                }
                return info;
            }''')
            log.info(f"ftMs-input ancestor chain:\\n{ancestor_info}")

            # Force ALL ancestors visible using COMPUTED style check
            page.evaluate('''() => {
                let el = document.querySelector("input.ftMs-input");
                while (el) {
                    const cs = getComputedStyle(el);
                    if (cs.display === "none") {
                        el.style.setProperty("display", "block", "important");
                    }
                    if (cs.visibility === "hidden") {
                        el.style.setProperty("visibility", "visible", "important");
                    }
                    if (cs.opacity === "0") {
                        el.style.setProperty("opacity", "1", "important");
                    }
                    if (parseInt(cs.height) === 0) {
                        el.style.setProperty("height", "auto", "important");
                    }
                    el = el.parentElement;
                }
            }''')
            time.sleep(1)

            # Check if search input is now visible
            search_visible = page.evaluate('''() => {
                const si = document.querySelector("input.ftMs-input");
                if (!si) return "not found";
                return "visible=" + (si.offsetParent !== null) + " display=" + getComputedStyle(si).display;
            }''')
            log.info(f"After forcing ancestors visible: ftMs-input {search_visible}")
            take_screenshot(page, "after_force_visible")

            for slot_idx, (name, member_id_guest) in zip(empty_slot_indices, GUEST_MEMBERS):
                player_num = slot_idx + 1  # 1-based display number
                last_name = name.split()[1]
                log.info(f"Adding {name} ({member_id_guest}) to player {player_num}...")
                try:
                    # Type the last name into the member search field
                    search_input = page.locator('input.ftMs-input').first
                    search_input.click(force=True, timeout=5000)
                    search_input.fill("")  # Clear previous search
                    search_input.type(last_name, delay=100)  # Type with delay for autocomplete
                    time.sleep(1.5)
                    log.info(f"Typed '{last_name}' in member search.")
                    take_screenshot(page, f"search_slot{slot_idx}")

                    # Click the matching result (format: "LastName_HNNNN, FirstName")
                    # The result could be in any element type (div, li, span, etc.)
                    result = page.locator(f'text="{member_id_guest}"').first
                    if not result.is_visible(timeout=3000):
                        # Try broader match with just the last name
                        result = page.locator(f':has-text("{member_id_guest}")').last
                    result.click(timeout=5000)
                    log.info(f"Added {name} to player {player_num}.")
                    time.sleep(1)
                    take_screenshot(page, f"after_add_slot{slot_idx}")

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

            # ---------------------------------------------------------------
            # Step 10b — Set transportation mode for all players
            # ---------------------------------------------------------------
            log.info("Setting transportation mode (WA = Walk) for all players...")
            trans_set = page.evaluate('''() => {
                const selects = document.querySelectorAll("select");
                let count = 0;
                for (const sel of selects) {
                    // Find transportation selects (they have WA, MC, etc. options)
                    const options = Array.from(sel.options).map(o => o.value);
                    if (options.includes("WA")) {
                        sel.value = "WA";
                        sel.dispatchEvent(new Event("change", { bubbles: true }));
                        count++;
                    }
                }
                return count;
            }''')
            log.info(f"Set transportation mode on {trans_set} player(s).")

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
                time.sleep(3)

                # Check for and dismiss any error/validation dialog
                try:
                    dialog_close = page.locator('button:has-text("Close")').first
                    if dialog_close.is_visible(timeout=2000):
                        dialog_text = page.locator('.ui-dialog').first.text_content()
                        log.warning(f"Dialog appeared after submit: {dialog_text}")
                        take_screenshot(page, "error_dialog")
                        dialog_close.click()
                        time.sleep(1)
                        # Retry submit
                        confirm_btn = page.locator(
                            'a:has-text("Submit Request"), '
                            'button:has-text("Submit Request"), '
                            'input[value="Submit Request"]'
                        ).first
                        confirm_btn.click(timeout=5000)
                        time.sleep(3)
                except Exception:
                    pass

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
