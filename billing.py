"""Anniversary-based subscription billing logic (pure date math, no DB).

Model:
- Each student has an `anchor_date` = the day they first joined (from their first
  Zoom class). Renewal is monthly on that day-of-month.
- `extension_days` shifts the anchor forward permanently (the 14-day extension
  granted to all active students on 2026-06-19 is stored as extension_days=14).
- `paid_through` = the date the student's subscription is paid up to (the start of
  their first UNPAID cycle). None means they've never been recorded as paid.
"""
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta


def effective_anchor(anchor_date, extension_days=0):
    """The billing anchor after applying the extension shift."""
    if anchor_date is None:
        return None
    return anchor_date + timedelta(days=extension_days or 0)


def cycle_start_on_or_before(anchor, today):
    """Most recent monthly anniversary of `anchor` that is <= today.
    relativedelta clamps invalid days (e.g. 31st -> 30th/28th)."""
    months = (today.year - anchor.year) * 12 + (today.month - anchor.month)
    cand = anchor + relativedelta(months=months)
    if cand > today:
        cand = anchor + relativedelta(months=months - 1)
    return cand


def next_due_date(anchor_date, extension_days, paid_through, today):
    """The date the student's next payment is due.
    If paid_through is set, that IS the next due date. Otherwise it's the start
    of the current (unpaid) cycle derived from the anchor."""
    if paid_through:
        return paid_through
    ea = effective_anchor(anchor_date, extension_days)
    if ea is None:
        return None
    if ea > today:
        return ea  # first cycle hasn't started yet
    return cycle_start_on_or_before(ea, today)


def renewal_status(anchor_date, extension_days, paid_through, today, grace=7):
    """Return (status, due_date, days) where status is one of:
       'paid'    -> subscription valid; `days` = days until it lapses
       'due'     -> lapsed within the grace window; `days` = days overdue
       'overdue' -> lapsed beyond grace; `days` = days overdue
       'unknown' -> no anchor and never paid; can't compute yet
    """
    if anchor_date is None and paid_through is None:
        return ('unknown', None, None)
    due = next_due_date(anchor_date, extension_days, paid_through, today)
    if due is None:
        return ('unknown', None, None)
    if due > today:
        return ('paid', due, (due - today).days)
    days_over = (today - due).days
    return (('due' if days_over <= grace else 'overdue'), due, days_over)


def advance_paid_through(anchor_date, extension_days, paid_through, months, today):
    """New paid_through after paying for `months` months.
    Extends from the existing paid_through if still valid, else from the current
    due date (so paying covers the cycle they owe), else from today as a fallback
    when no anchor is known yet."""
    if paid_through and paid_through >= today:
        base = paid_through
    else:
        base = next_due_date(anchor_date, extension_days, paid_through, today)
        if base is None:
            base = today
    return base + relativedelta(months=months)
