#!/usr/bin/env python3
"""
apply_job.py — SmartApply driver for Clutch 1.75 O
Usage: python3 apply_job.py "https://www.indeed.com/viewjob?jk=XXXXXX"

Connects to Chrome via CDP, clicks Apply on the job listing,
captures the SmartApply popup, and auto-fills the form.
Returns JSON: {"status": "applied|hard_stop|external_apply|error", "message": "..."}
"""

import sys, json, re, time
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

CDP_URL  = "http://100.112.114.41:9334"
RESUME   = "/home/brewuser/Desktop/workspace/resume.pdf"
POSTAL   = "75201"
MAX_PAGES = 20

HARD_STOP_PHRASES = [
    "assessment", "aptitude test", "video interview",
    "hirevue", "pymetrics", "codility", "hackerrank",
    "e-sign", "electronic signature", "docusign",
]

FLAGGED_EMPLOYERS = ["Smart Start", "Quantum World Tele Services"]


def _find_page(ctx, pattern):
    for pg in ctx.pages:
        if pattern in pg.url:
            return pg
    return None


def _body_text(page):
    try:
        return page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def _check_hard_stop(text):
    t = text.lower()
    for phrase in HARD_STOP_PHRASES:
        if phrase.lower() in t:
            return phrase
    return None


def _click_visible_button(page, label, timeout=8000):
    """Click the first visible, enabled button matching label text."""
    btns = page.locator("button").filter(has_text=label).all()
    for btn in btns:
        try:
            if btn.is_visible() and btn.is_enabled():
                btn.scroll_into_view_if_needed()
                btn.click()
                return True
        except Exception:
            continue
    return False


def _react_select(page, select_locator, value_text):
    """Set a <select> value in a way React's onChange fires."""
    try:
        select_locator.select_option(label=value_text)
        page.wait_for_timeout(300)
        return True
    except Exception:
        pass
    # Fallback: native setter + change event
    try:
        page.evaluate("""([sel, val]) => {
            const opts = [...sel.options];
            const opt = opts.find(o => o.text.includes(val));
            if (!opt) return;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLSelectElement.prototype, 'value').set;
            setter.call(sel, opt.value);
            sel.dispatchEvent(new Event('change', { bubbles: true }));
        }""", [select_locator.element_handle(), value_text])
        page.wait_for_timeout(300)
        return True
    except Exception:
        return False


def _handle_resume_page(sa):
    """Resume selection — keep uploaded file, continue."""
    file_radio = sa.locator("input[value=file]")
    if file_radio.count() and not file_radio.is_checked():
        file_radio.click()
        sa.wait_for_timeout(500)
    _click_visible_button(sa, "Continue")
    sa.wait_for_timeout(2000)


def _handle_contact_page(sa):
    """Contact info — fill postal if missing, continue."""
    postal = sa.locator("input[name*='postal'], input[placeholder*='Zip'], input[placeholder*='postal']").first
    if postal.count():
        try:
            if not postal.input_value():
                postal.fill(POSTAL)
        except Exception:
            pass
    _click_visible_button(sa, "Continue")
    sa.wait_for_timeout(2000)


def _handle_qualifications_page(sa):
    """Yes/No qualification questions — answer all radio groups."""
    # Answer yes/no radios: prefer "Yes" where present
    groups = sa.locator("fieldset").all()
    for group in groups:
        radios = group.locator("input[type=radio]").all()
        for r in radios:
            val = (r.get_attribute("value") or "").lower()
            if val in ("yes", "true", "1"):
                try:
                    r.click()
                    break
                except Exception:
                    pass
    _click_visible_button(sa, "Continue")
    sa.wait_for_timeout(2000)


def _handle_demographic_page(sa):
    """Demographic questions — decline all, consent yes."""
    page_text = _body_text(sa).lower()

    # Gender — Not declared
    for r in sa.locator("input[type=radio]").all():
        try:
            label_text = sa.evaluate(
                "el => { const lbl = document.querySelector(`label[for='${el.id}']`); return lbl ? lbl.innerText : ''; }",
                r.element_handle()
            )
            if "not declared" in label_text.lower():
                r.click()
                sa.wait_for_timeout(300)
                break
        except Exception:
            pass

    # Race/ethnicity select — "I do not wish to answer"
    for sel in sa.locator("select").all():
        try:
            opts = sel.evaluate("el => [...el.options].map(o => o.text)")
            decline = next((o for o in opts if "do not wish" in o.lower() or "decline" in o.lower()), None)
            if decline:
                _react_select(sa, sel, decline)
        except Exception:
            pass

    # Veteran — "I do not wish to Self-Identify"
    for r in sa.locator("input[type=radio]").all():
        try:
            label_text = sa.evaluate(
                "el => { const lbl = document.querySelector(`label[for='${el.id}']`); return lbl ? lbl.innerText : ''; }",
                r.element_handle()
            )
            lt = label_text.lower()
            if "not wish" in lt or "self-identify" in lt or "decline" in lt:
                if not r.is_checked():
                    r.click()
                    sa.wait_for_timeout(300)
        except Exception:
            pass

    # Disability — "I do not want to answer"
    for r in sa.locator("input[type=radio]").all():
        try:
            label_text = sa.evaluate(
                "el => { const lbl = document.querySelector(`label[for='${el.id}']`); return lbl ? lbl.innerText : ''; }",
                r.element_handle()
            )
            lt = label_text.lower()
            if "do not want to answer" in lt or "not want" in lt:
                if not r.is_checked():
                    r.click()
                    sa.wait_for_timeout(300)
        except Exception:
            pass

    # Consent checkbox or radio — find "yes" / "consent" / agree
    for r in sa.locator("input[type=radio]").all():
        try:
            label_text = sa.evaluate(
                "el => { const lbl = document.querySelector(`label[for='${el.id}']`); return lbl ? lbl.innerText : ''; }",
                r.element_handle()
            )
            lt = label_text.lower()
            if ("yes" in lt and "consent" in page_text) or "i have read" in lt or "i consent" in lt:
                if not r.is_checked():
                    r.click()
                    sa.wait_for_timeout(300)
        except Exception:
            pass

    # Share with Indeed — decline
    share_chk = sa.locator("input[type=checkbox]").all()
    for chk in share_chk:
        try:
            label_text = sa.evaluate(
                "el => { const lbl = document.querySelector(`label[for='${el.id}']`); return lbl ? lbl.innerText : ''; }",
                r.element_handle()
            )
            if "indeed" in label_text.lower() and chk.is_checked():
                chk.click()
                sa.wait_for_timeout(300)
        except Exception:
            pass

    # Click Review / Submit / Continue
    for label in ("Review your application", "Review", "Submit", "Continue"):
        if _click_visible_button(sa, label):
            sa.wait_for_timeout(3000)
            break


def drive_form(sa):
    """Navigate through SmartApply pages until submitted or hard stop."""
    for _ in range(MAX_PAGES):
        url = sa.url
        text = _body_text(sa)

        stop = _check_hard_stop(text)
        if stop:
            return {"status": "hard_stop", "message": f"Hard stop: {stop}"}

        if "application-complete" in url or "submitted" in url.lower() or "thank you" in text.lower()[:200]:
            return {"status": "applied", "message": "Application submitted."}

        if "external" in url or ("apply" not in url and "indeed" not in url):
            return {"status": "external_apply", "message": f"Redirected to external: {url}"}

        if "resume-selection" in url:
            _handle_resume_page(sa)
        elif "contact" in url or "profile" in url:
            _handle_contact_page(sa)
        elif "qualifications" in url or "questions" in url:
            if "demographic" in url:
                _handle_demographic_page(sa)
            else:
                _handle_qualifications_page(sa)
        elif "review" in url:
            if not _click_visible_button(sa, "Submit"):
                _click_visible_button(sa, "Submit your application")
            sa.wait_for_timeout(3000)
        else:
            # Generic: try Continue, then next
            if not _click_visible_button(sa, "Continue"):
                _click_visible_button(sa, "Next")
            sa.wait_for_timeout(2000)

        if sa.url == url:
            # Page didn't advance — take stock
            text2 = _body_text(sa)
            if "Thank" in text2[:300]:
                return {"status": "applied", "message": "Application submitted."}
            return {"status": "error", "message": f"Stuck at {url}"}

    return {"status": "error", "message": "Exceeded page limit"}


def apply(job_url):
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0]

        # Check flagged employers
        for emp in FLAGGED_EMPLOYERS:
            if emp.lower() in job_url.lower():
                return {"status": "hard_stop", "message": f"Flagged employer: {emp}"}

        # Find or reuse an existing tab for the job listing
        tab = None
        for pg in ctx.pages:
            if "indeed.com/viewjob" in pg.url or "indeed.com/rc/clk" in pg.url:
                tab = pg
                break
        if not tab:
            tab = ctx.new_page()

        # Navigate to job listing
        tab.goto(job_url, wait_until="domcontentloaded", timeout=30000)
        tab.wait_for_timeout(2000)

        # Check employer name in page
        title_text = _body_text(tab)[:500]
        for emp in FLAGGED_EMPLOYERS:
            if emp.lower() in title_text.lower():
                return {"status": "hard_stop", "message": f"Flagged employer: {emp}"}

        # Click Apply now / Apply button — open popup
        apply_btn = None
        for selector in (
            "button:has-text('Apply now')",
            "a:has-text('Apply now')",
            "button:has-text('Apply')",
            "[data-testid='applyButton']",
            "#applyButtonLinkContainer button",
        ):
            try:
                el = tab.locator(selector).first
                if el.is_visible():
                    apply_btn = el
                    break
            except Exception:
                continue

        if not apply_btn:
            return {"status": "error", "message": "Apply button not found on job listing page"}

        # Capture popup
        try:
            with tab.expect_popup(timeout=10000) as popup_info:
                apply_btn.click()
            sa = popup_info.value
        except PWTimeout:
            # Some apply buttons navigate in-tab
            apply_btn.click()
            tab.wait_for_timeout(3000)
            if "smartapply" in tab.url or "indeedapply" in tab.url:
                sa = tab
            else:
                sa = _find_page(ctx, "smartapply") or _find_page(ctx, "indeedapply")
                if not sa:
                    return {"status": "error", "message": "SmartApply did not open"}

        # Wait for content
        for _ in range(10):
            if len(_body_text(sa)) > 100:
                break
            time.sleep(1)

        return drive_form(sa)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"status": "error", "message": "Usage: apply_job.py <job_url>"}))
        sys.exit(1)

    result = apply(sys.argv[1])
    print(json.dumps(result, indent=2))
