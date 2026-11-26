"""TDS sections, rates and thresholds for payments to suppliers (Income
Tax Act, as amended by the Finance Act 2025). Change them here when the
law changes; nothing else hard-codes a rate.

Rates apply to the value before GST, when GST is shown separately on the
bill (CBDT Circular 23/2017).
"""
from decimal import Decimal

C_JOB_WORK = '194C'
J_PROFESSIONAL = '194J_PROF'
J_TECHNICAL = '194J_TECH'
Q_GOODS = '194Q'

# code: (label, Form 26Q section code)
SECTIONS = {
    C_JOB_WORK: ("194C · Contractors, job work, transport", '94C'),
    J_PROFESSIONAL: ("194J · Professional fees", '94J-B'),
    J_TECHNICAL: ("194J · Fees for technical services", '94J-A'),
    Q_GOODS: ("194Q · Purchase of goods", '94Q'),
}

# One bill over SINGLE, or the supplier's bills in the financial year over
# AGGREGATE, and TDS applies (to the whole year's amount, catching up
# earlier bills). 194J has only a yearly threshold.
SINGLE_LIMIT = {C_JOB_WORK: Decimal('30000')}
AGGREGATE_LIMIT = {C_JOB_WORK: Decimal('100000'), J_PROFESSIONAL: Decimal('50000'), J_TECHNICAL: Decimal('50000')}
# 194Q: only on purchases from one seller above this in the year, and only
# for buyers whose turnover was over ₹10 crore last year.
Q_EXEMPT_UP_TO = Decimal('5000000')

NO_PAN_RATE = Decimal('20')       # s.206AA
NO_PAN_RATE_194Q = Decimal('5')   # s.206AA for 194Q


def rate(section, pan):
    """The rate in percent for a deductee's PAN (blank when they have none)."""
    if not pan:
        return NO_PAN_RATE_194Q if section == Q_GOODS else NO_PAN_RATE
    if section == C_JOB_WORK:
        # The PAN's 4th character is the holder's status: P individual, H HUF.
        return Decimal('1') if pan[3:4] in ('P', 'H') else Decimal('2')
    if section == J_PROFESSIONAL:
        return Decimal('10')
    if section == J_TECHNICAL:
        return Decimal('2')
    if section == Q_GOODS:
        return Decimal('0.1')
    raise ValueError(f"Unknown TDS section {section!r}")


def due_date(deducted_on):
    """Deposit by the 7th of the next month; for March, by 30 April."""
    from datetime import date
    if deducted_on.month == 3:
        return date(deducted_on.year, 4, 30)
    year, month = (deducted_on.year + 1, 1) if deducted_on.month == 12 else (deducted_on.year, deducted_on.month + 1)
    return date(year, month, 7)
