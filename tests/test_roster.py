"""Counting the Criterion 5 numbers from the faculty list."""
from app.roster import Roster, cadre_of, degree_of
from app.years import AssessmentYears

YEARS = AssessmentYears(2025)  # CAY 2025-26, CAYm1 2024-25, CAYm2 2023-24


def person(i, **kw):
    base = {"id": i, "name": f"CSE Faculty {i:03d}", "designation": "Assistant Professor", "designation_at_joining": "",
            "designated_on": None, "joining_date": "2015-07-01", "leaving_date": None, "qualification": "M.Tech",
            "phd_date": None, "allied": 0, "nature": "Regular", "full_time": 1, "first_year": 0, "is_hod": 0}
    base.update(kw)
    return base


def test_names_of_cadre_and_degree():
    assert cadre_of("Professor & HoD") == "Professor"
    assert cadre_of("Assoc. Prof") == "Associate Professor"
    assert cadre_of("Asst Professor") == "Assistant Professor"
    assert cadre_of("Professor of Practice") == "Professor of Practice"
    assert degree_of("Ph.D.") == "phd" and degree_of("M.E.") == "mtech" and degree_of("B.E.") == "other"


def test_counted_only_if_six_months_in_the_year():
    r = Roster([person(1, joining_date="2026-02-01"),            # 5 months of CAY: not counted
                person(2, joining_date="2025-12-01"),            # 7 months of CAY: counted
                person(3, leaving_date="2024-10-31")], YEARS)    # left early in CAYm1
    m = r.metrics()
    assert m[("F", "CAY")] == 1
    assert m[("F", "CAYm1")] == 0   # person 2 had not joined; person 3 left after 4 months
    assert m[("F", "CAYm2")] == 1


def test_promotion_and_phd_dates_are_respected():
    r = Roster([person(1, designation="Associate Professor", designation_at_joining="Assistant Professor",
                       designated_on="2025-08-01", qualification="Ph.D.", phd_date="2024-03-10")], YEARS)
    m = r.metrics()
    assert (m[("AF2", "CAY")], m[("AF3", "CAYm1")]) == (1, 1)          # promoted in CAY
    assert (m[("PHD", "CAY")], m[("PHD", "CAYm1")], m[("MTECH", "CAYm2")]) == (1, 1, 1)  # Ph.D. in Mar 2024


def test_pop_ff_allied_and_retention():
    fac = [person(1, designation="Professor of Practice", nature="Professor of Practice"),
           person(2, first_year=1), person(3, allied=1, joining_date="2024-07-01"),
           person(4, joining_date="2023-07-01"), person(5, full_time=0)]
    m = Roster(fac, YEARS).metrics({("S", "CAY"): 1000, ("S", "CAYm1"): 1000, ("S", "CAYm2"): 1000})
    assert m[("F", "CAY")] == 3          # PoP and part-time excluded; allied included
    assert m[("POP", "CAY")] == 1 and m[("FF", "CAY")] == 1
    assert m[("AF", "CAY")] == 2         # department alone (person 2 and 4)
    assert m[("RET_D", "CAY")] == 1      # person 4: completes 3 years at the end of CAY -> 3-4 band
    assert m[("RET_E", "CAY")] == 1      # person 2: since 2015


def test_pop_limited_to_20_percent_of_required():
    fac = [person(i, designation="Professor of Practice", nature="Professor of Practice") for i in range(1, 6)]
    m = Roster(fac, YEARS).metrics({("S", "CAY"): 200})   # required faculty 10 -> PoP counted up to 2
    assert m[("POP", "CAY")] == 2


def test_hod_and_professor_conditions():
    fac = [person(1, designation="Professor", qualification="Ph.D.", is_hod=1),
           person(2, designation="Associate Professor", qualification="Ph.D."),
           person(3, designation="Associate Professor", qualification="Ph.D.", nature="Contract")]
    m = Roster(fac, YEARS).metrics()
    assert m[("HOD_PHD", "CAY")] == 1
    assert (m[("PROF_PHD", "CAY")], m[("ASSOC_PHD", "CAY")]) == (1, 1)   # contract faculty not counted


def test_verified_only_mode():
    fac = [person(1, qualification="Ph.D."), person(2, qualification="Ph.D.")]
    status = {1: {"appointment": "verified", "degree": "verified"}, 2: {"appointment": "verified"}}
    m = Roster(fac, YEARS, status, verified_only=True).metrics()
    assert m[("F", "CAY")] == 2 and m[("PHD", "CAY")] == 1
    status[2] = {}
    assert Roster(fac, YEARS, status, verified_only=True).metrics()[("F", "CAY")] == 1
