"""Cases found by running the extractor on real postings (Google, Citi, Visa)."""
from board.extract import extract

GOOGLE_WRAPPED = """
Minimum qualifications:
- Bachelor's degree in Business, Finance, Economics, Statistics, or another
quantitative field, or equivalent practical experience.
- 7 years of experience in financial planning and analysis (FP&A),
consulting, or a related function, or 5 years with a relevant advanced
degree.
Preferred qualifications:
- Experience in Finance, Consulting, Corporate Strategy, or a similar function.
"""

CITI = """
Required Qualifications & Skills
- 4 to 6 years of experience in Equity Research, Investment Banking, Corporate Finance, or Valuation.
- MBA, graduate degree, or CFA designation, demonstrating advanced financial and analytical expertise.
Beneficial Skills & Qualifications
- Hands-on experience applying AI tools to investment research.
"""

VISA_BOILERPLATE = """
This position will be performed in an office setting. The position will require the incumbent to sit and stand
at a desk, communicate in person and by telephone, frequently operate standard office equipment.
"""


def test_hard_wrapped_bullets_and_advanced_degree_route():
    x = extract(GOOGLE_WRAPPED)
    y = x.years_required
    assert y.min_years == 5                        # lowest bar: 7 years, or 5 with an advanced degree
    assert y.years_bachelors == 7                  # the route that fits a BS holder
    assert x.degree_required.level == "bachelors"  # the advanced degree is a route, not a requirement


def test_credential_menu_is_not_an_mba_requirement():
    x = extract(CITI)
    assert x.years_required.min_years == 4
    assert x.years_required.confidence in ("high", "medium")   # heading "Required Qualifications & Skills"
    assert x.degree_required is None or x.degree_required.level == "bachelors"


def test_office_setting_boilerplate_is_not_onsite():
    assert extract(VISA_BOILERPLATE).work_mode.mode == "unknown"


def test_or_an_advanced_degree_after_years_is_not_masters_required():
    x = extract("Minimum qualifications:\n7 years of experience in FP&A, consulting, or a related function, or an advanced degree.")
    assert x.degree_required is None
