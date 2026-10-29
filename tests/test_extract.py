"""Years / degree / cert / work-mode extraction on 10 realistic JD snippets.

Each snippet is modeled on how real finance postings are written (Amazon-style basic/preferred
qualifications, Google-style minimum/preferred, bank "Associate" postings, Workday free text).
"""
from board.extract import extract

# --- 1. Amazon style: Basic vs Preferred qualifications -------------------------------------------
AMAZON = """
Financial Analyst II, Advertising Finance
Description
You will own forecasting and variance analysis for the ads P&L and partner with product and engineering.

Basic Qualifications
- Bachelor's degree in Finance, Accounting, Economics or a related field
- 2+ years of experience in financial analysis or FP&A
- Experience with Excel, including pivot tables and lookups

Preferred Qualifications
- Master's degree or MBA
- 4+ years of finance experience
- Experience with SQL, Python or Tableau
- CPA or CFA designation
"""

# --- 2. Google style: Minimum vs Preferred --------------------------------------------------------
GOOGLE = """
Senior Financial Analyst, Technical Infrastructure
Minimum qualifications:
Bachelor's degree in Finance, Economics, Accounting, Business, or equivalent practical experience.
4 years of experience in finance, accounting, or a related analytical field.
Preferred qualifications:
MBA or Master's degree in a related field.
Experience building financial models and communicating with executives.
"""

# --- 3. Degree routes: Bachelor's + 3 or Master's + 1 -----------------------------------------------
ROUTES = """
Qualifications
Bachelor's degree + 3 years of related experience, or Master's degree + 1 year of related experience.
Strong Excel and PowerPoint skills; ability to work in a fast-paced environment.
"""

# --- 4. Spelled-out number and range --------------------------------------------------------------
SPELLED = """
Requirements:
Minimum of three (3) years of progressive experience in corporate finance or FP&A.
Strong understanding of GAAP and budgeting processes.
Nice to have: five years of experience in SaaS.
"""

RANGE = """
What you'll need:
2-4 years of experience in financial planning and analysis at a technology company.
Bachelor's degree in finance or accounting required.
"""

# --- 5. Bank "Associate" (post-MBA) with certs obtainable after hire -------------------------------
BANK = """
Equity Research Associate, Software
Qualifications:
MBA or equivalent graduate degree required.
5+ years of experience covering technology equities at a bulge-bracket or boutique firm.
Candidates must obtain the SIE, Series 7 and Series 86/87 licenses within 120 days of hire.
CFA charterholder or candidate is a plus.
"""

# --- 6. Entry level, no experience, remote ----------------------------------------------------------
ENTRY = """
Finance Rotational Program Analyst
About the program
This is an entry-level rotational program for recent graduates. No prior experience is required.
You will rotate through FP&A, treasury and accounting over 24 months.
Requirements:
Bachelor's degree in Finance or Accounting by June 2027.
This position is fully remote within the United States.
"""

# --- 7. Skill-specific years should not count as overall experience -------------------------------
SKILL = """
Basic Qualifications:
1+ year of experience in finance, accounting or consulting.
3+ years of experience with SQL and Python for data analysis.
Bachelor's degree required.
"""

# --- 8. Hybrid, cert required, noise years (company history, "past 5 years") -----------------------
HYBRID = """
About us
For over 100 years we have helped families plan for retirement.
The role is hybrid, with three days a week in our Boston office.
Requirements
Series 7 and Series 66 required prior to start.
3+ years of experience in wealth management or investment operations.
Experience managing a book of business over the past 5 years is a plus.
"""

# --- 9. Preferred years only, master's preferred ----------------------------------------------------
PREF_ONLY = """
Pricing Analyst
Responsibilities
Build pricing models and run scenario and sensitivity analysis for enterprise deals.
Required Skills
Bachelor's degree in a quantitative or business field.
Advanced Excel and strong written communication.
Preferred Skills
Master's degree preferred. 3-5 years of pricing or deal desk experience preferred.
CFA Level I candidate preferred.
On-site in Austin, TX.
"""

# --- 10. Flattened one-paragraph JD with inline headings ------------------------------------------
FLAT = (
    "Strategic Finance Analyst. Join our finance team to support planning for a high-growth product line. "
    "Basic Qualifications: Bachelor's degree in finance; at least 6 years of experience in financial planning "
    "required. Preferred Qualifications: MBA; experience with Anaplan. This role offers a hybrid schedule "
    "with 2 days in the office."
)


def test_amazon_basic_vs_preferred():
    x = extract(AMAZON)
    assert x.years_required.min_years == 2
    assert x.years_required.confidence in ("high", "medium")
    assert "2+ years" in x.years_required.evidence
    assert x.years_preferred.min_years == 4
    assert x.degree_required.level == "bachelors"
    assert x.degree_preferred.level in ("masters", "mba")
    certs = {c.cert: c.status for c in x.certs}
    assert certs["CPA"] == "preferred" and certs["CFA"] == "preferred"


def test_google_minimum_vs_preferred():
    x = extract(GOOGLE)
    assert x.years_required.min_years == 4
    assert x.degree_required.level == "bachelors"           # "or equivalent practical experience"
    assert x.degree_preferred.level in ("mba", "masters")


def test_degree_routes_take_lowest_bar_and_keep_bachelors_route():
    x = extract(ROUTES)
    y = x.years_required
    assert y.min_years == 1                                  # lowest bar across routes
    assert y.years_bachelors == 3                            # the route that fits a BS holder
    assert {r["degree"] for r in y.routes} == {"bachelors", "masters"}
    assert x.degree_required is None or x.degree_required.level == "bachelors"


def test_spelled_out_years_and_preferred_split():
    x = extract(SPELLED)
    assert x.years_required.min_years == 3
    assert "three (3) years" in x.years_required.evidence
    assert x.years_preferred.min_years == 5


def test_range_uses_lower_bound():
    x = extract(RANGE)
    assert x.years_required.min_years == 2
    assert x.degree_required.level == "bachelors"


def test_bank_associate_mba_required_and_certs_obtainable():
    x = extract(BANK)
    assert x.degree_required.level in ("mba", "masters")
    assert x.degree_required.confidence in ("medium", "high")
    assert x.years_required.min_years == 5
    certs = {c.cert: c.status for c in x.certs}
    assert certs["SIE"] == "obtainable"
    assert certs["Series 7"] == "obtainable"
    assert certs["Series 86"] == "obtainable"
    assert certs["CFA"] == "preferred"


def test_entry_level_no_years_and_remote():
    x = extract(ENTRY, location="Remote - US")
    assert x.years_required is None
    assert "No prior experience" in x.no_experience_needed
    assert x.work_mode.mode == "remote"
    assert x.degree_required.level == "bachelors"


def test_skill_specific_years_not_counted():
    x = extract(SKILL)
    assert x.years_required.min_years == 1
    assert any(s["years"] == 3 for s in x.years_skill_specific)


def test_hybrid_required_certs_and_noise_years_ignored():
    x = extract(HYBRID)
    assert x.work_mode.mode == "hybrid"
    assert "three days" in x.work_mode.evidence
    assert x.years_required.min_years == 3                   # not 100 (company age), not 5 (past 5 years)
    certs = {c.cert: c.status for c in x.certs}
    assert certs["Series 7"] == "required" and certs["Series 66"] == "required"


def test_preferred_only_degree_and_years_and_onsite():
    x = extract(PREF_ONLY)
    assert x.years_required is None
    assert x.years_preferred.min_years == 3
    assert x.degree_required.level == "bachelors"
    assert x.degree_preferred.level == "masters"
    assert {c.cert: c.status for c in x.certs}["CFA"] == "preferred"
    assert x.work_mode.mode == "onsite"


def test_flat_text_with_inline_headings_and_hybrid_days():
    x = extract(FLAT)
    assert x.years_required.min_years == 6
    assert x.degree_required.level == "bachelors"
    assert x.degree_preferred.level == "mba"
    assert x.work_mode.mode == "hybrid"


def test_ms_excel_is_not_a_masters_degree():
    x = extract("Requirements:\nProficiency in MS Excel and MS Office. Bachelor's degree required.")
    assert x.degree_required.level == "bachelors"
    assert x.degree_preferred is None


def test_location_multi_city_remote_listing_is_not_remote():
    x = extract("Locations: San Francisco, CA; Remote (US). Hybrid role.", location="San Francisco, CA")
    assert x.work_mode.mode == "hybrid"
