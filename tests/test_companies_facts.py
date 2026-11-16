"""config/companies.yaml: sector tier facts, the defense / defunct / duplicate cleanup, and "size is never used"."""
import re
from pathlib import Path

from board import config

TIERS = {"future", "adjacent", "traditional"}
NAMES = [c["name"] for c in config.companies_config()]
BY_NAME = {c["name"]: c for c in config.companies_config()}


def scannable():
    return [c for c in config.companies_config() if c.get("scan_status") == "OK"]


def test_every_scannable_company_has_a_dated_sector_tier():
    for c in scannable():
        assert c["sector_tier"] in TIERS, c["name"]
        assert c["approx"] is True and str(c["as_of"]) >= "2026-11-15", c["name"]


def test_size_and_market_cap_fields_are_gone_and_never_read():
    for c in config.companies_config():
        assert not ({"market_cap_usd_b", "employees", "valuation", "facts_confidence"} & set(c)), c["name"]
    for path in [p for p in Path(config.ROOT, "board").rglob("*.py") if p.name != "extract.py"]:    # extract.py: "employees who ..." is JD wording
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"market_cap|\bemployees\b", text), path.name


def test_boeing_removed_and_defense_primes_are_off():
    assert "Boeing" not in BY_NAME
    for n in ("Lockheed Martin", "RTX", "Northrop Grumman", "General Dynamics", "L3Harris"):
        assert BY_NAME[n]["scan_status"] == "off" and BY_NAME[n]["note"] == "defense: moves backward", n


def test_defunct_and_acquired_are_off_with_a_note():
    for n in ("Signature Bank", "Silvergate", "Silicon Valley Bank", "First Republic", "Cruise", "Bank of the West", "Splunk", "Slack",
              "VMware", "Cerner", "Black Knight", "Activision", "Credit Karma", "Discover", "Eaton Vance", "Owl Rock", "Metromile",
              "TCF", "Chemical", "Umpqua", "Sterling Bancorp", "Investors Bancorp", "BKD", "Dixon Hughes Goodman", "Duff & Phelps"):
        assert BY_NAME[n]["scan_status"] == "off" and BY_NAME[n].get("note"), n


def test_duplicates_are_merged_and_the_scannable_entry_survives():
    for gone in ("Netflix Inc", "Uber Technologies", "DoorDash Inc", "Expedia Group", "Vanguard Group", "Ernst & Young", "Tata Consultancy",
                 "Gerson Lehrman Group", "Square", "Rocket Mortgage", "Affirm Holdings", "Hancock", "M&T", "RBC Capital Markets"):
        assert gone not in BY_NAME, gone
    for kept in ("Netflix", "Uber", "DoorDash", "Expedia", "Vanguard", "EY", "TCS", "GLG", "Block", "Rocket Companies", "Affirm",
                 "Hancock Whitney", "M&T Bank", "Royal Bank of Canada"):
        assert kept in BY_NAME, kept
    assert BY_NAME["Block"]["scan_status"] == "OK" and BY_NAME["Netflix"]["scan_status"] == "OK"
    assert len(NAMES) == len(set(NAMES))


def test_off_is_a_string_not_a_yaml_boolean():
    assert all(isinstance(c["scan_status"], str) for c in config.companies_config())


def test_probe_finds_are_scannable_with_a_parsed_endpoint():
    from board.fetch.boards import parse_board
    for n, ats in (("OpenAI", "ashby"), ("Lyft", "greenhouse"), ("Okta", "greenhouse"), ("Spotify", "lever"), ("KLA", "workday"),
                   ("eBay", "workday")):
        assert BY_NAME[n]["scan_status"] == "OK"
        assert parse_board(BY_NAME[n])["ats"] == ats, n
