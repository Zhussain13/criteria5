"""Criterion 5 formulas, checked against the worked examples in NBA's own SAR format (Tier I, April 2026)."""
import pytest

from app.engine import run_audit
from app.rules import load

V = "criterion5_tier1_2026"
PROGRAM = {"name": "BE CSE", "department": "CSE", "institute_name": "CMR Institute of Technology, CMRIT",
           "tier": "Tier I", "sar_version": V, "cay_start_year": 2025, "ay_start_month": 7}


def item(report, code):
    return next(it for it in report["criteria"][0]["items"] if it["code"] == code)


def audit(metrics):
    return run_audit(PROGRAM, [], [], metrics)


def test_only_criterion_5_and_it_adds_up_to_100():
    r = load(V)
    assert list(r["criteria"]) == [5]
    assert r["criteria"][5]["max"] == 100
    assert sum(i["max"] for i in r["items"].values()) == 100
    assert sorted(r["items"]) == ["5.1", "5.2", "5.3", "5.4", "5.5"]


SFR_EXAMPLE = {("S", "CAY"): 2357, ("S", "CAYm1"): 2345, ("S", "CAYm2"): 2307,
               ("F", "CAY"): 120, ("F", "CAYm1"): 124, ("F", "CAYm2"): 125}


def test_sfr_sar_example():
    it = item(audit(SFR_EXAMPLE), "5.1")
    assert any("19.0" in n for n in it["notes"])  # NBA example: average SFR = 19.00
    assert it["awarded"] == 21


def test_sfr_bands():
    for tf, marks in ((170, 30), (130, 24), (100, 15), (90, 0)):
        m = {(k, y): v for y in ("CAY", "CAYm1", "CAYm2") for k, v in (("S", 2400), ("F", tf))}
        assert item(audit(m), "5.1")["awarded"] == marks, tf


def test_fqi_formula():
    m = {}
    for y in ("CAY", "CAYm1", "CAYm2"):
        m.update({("S", y): 2000, ("PHD", y): 30, ("MTECH", y): 70})  # RF = 100
    # 2.5 x (10 x 30 + 4 x 70) / 100 = 14.5
    assert item(audit(m), "5.2")["awarded"] == pytest.approx(14.5)


def test_cadre_full_marks_and_zero_without_professors():
    m = dict(SFR_EXAMPLE)
    for y in ("CAY", "CAYm1", "CAYm2"):
        rf = m[("S", y)] / 20
        m.update({("AF1", y): rf / 9, ("AF2", y): 2 * rf / 9, ("AF3", y): 6 * rf / 9})
    assert item(audit(m), "5.3")["awarded"] == pytest.approx(25)
    for y in ("CAY", "CAYm1", "CAYm2"):
        m.update({("AF1", y): 0, ("AF2", y): 0})
    assert item(audit(m), "5.3")["awarded"] == 0


def test_adjunct_marks():
    m = {("ADJ_HOURS", "CAYm1"): 60, ("ADJ_HOURS", "CAYm2"): 40, ("ADJ_HOURS", "CAYm3"): 50}
    assert item(audit(m), "5.4")["awarded"] == 1 + 3 + 0 + 3


def test_retention_sar_example():
    m = {("DS", "CAY"): 1964, ("DS", "CAYm1"): 1954, ("DS", "CAYm2"): 1900,
         ("RET_B", "CAY"): 2, ("RET_C", "CAY"): 1, ("RET_D", "CAY"): 1, ("RET_E", "CAY"): 90,
         ("RET_B", "CAYm1"): 3, ("RET_C", "CAYm1"): 2, ("RET_D", "CAYm1"): 0, ("RET_E", "CAYm1"): 94,
         ("RET_B", "CAYm2"): 10, ("RET_C", "CAYm2"): 2, ("RET_D", "CAYm2"): 1, ("RET_E", "CAYm2"): 85}
    assert item(audit(m), "5.5")["awarded"] == pytest.approx(9.5, abs=0.2)  # NBA example: 9.54


def test_pre_qualifiers_faculty_conditions():
    m = dict(SFR_EXAMPLE)
    m.update({("PHD", "CAY"): 30, ("PHD", "CAYm1"): 28, ("HOD_PHD", "CAY"): 1, ("APPROVAL_5Y", "CAY"): 1,
              ("BATCHES_PASSED", "CAY"): 10, ("PROF_PHD", "CAY"): 1, ("ASSOC_PHD", "CAY"): 1,
              ("PROF_PHD", "CAYm1"): 1, ("ASSOC_PHD", "CAYm1"): 1})
    pq = {q["no"]: q["status"] for q in audit(m)["pre_qualifiers"]}
    assert pq == {1: "Complied", 2: "Complied", 3: "Complied", 4: "Complied", 5: "Complied", 6: "Complied"}
    m[("ASSOC_PHD", "CAY")] = 0
    assert {q["no"]: q["status"] for q in audit(m)["pre_qualifiers"]}[3] == "Not complied"
