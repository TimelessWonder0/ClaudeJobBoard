"""Static company facts in config/companies.yaml: every scannable company has them, marked approximate and dated."""
from board import config

TIERS = {"future", "adjacent", "traditional"}
CONFIDENCE = {"high", "medium", "low"}


def scannable():
    return [c for c in config.companies_config() if c.get("scan_status") == "OK"]


def test_every_scannable_company_has_facts():
    missing = [c["name"] for c in scannable() if "as_of" not in c]
    assert missing == []


def test_facts_are_dated_approximate_and_well_formed():
    for c in scannable():
        assert c["approx"] is True and str(c["as_of"]) == "2026-11-15", c["name"]
        assert c["sector_tier"] in TIERS and c["facts_confidence"] in CONFIDENCE, c["name"]
        assert c["valuation"] in {"public", "private", "n/a"}, c["name"]
        assert isinstance(c["employees"], int) and c["employees"] > 0, c["name"]
        cap = c["market_cap_usd_b"]
        assert cap is None or cap > 0, c["name"]
        if c["valuation"] == "public":
            assert cap is not None, f"{c['name']}: public company without a market cap"


def test_yaml_still_parses_with_original_fields():
    for c in config.companies_config():
        assert {"name", "category", "metros", "priority", "ats", "scan_status"} <= set(c)
