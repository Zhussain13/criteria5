"""Proof checks of appointment letters and degree certificates (free text rules, no AI)."""
from app.faculty_proofs import check_proof

F = {"name": "CSE Faculty 012", "designation": "Associate Professor", "designation_at_joining": "Assistant Professor",
     "joining_date": "2018-07-16", "qualification": "Ph.D.", "phd_date": "2021-03-20",
     "university": "Visvesvaraya Technological University", "designated_on": "2023-08-01"}
ALIASES = ["cmr institute of technology", "cmrit"]
LETTER = ("CMR Institute of Technology\nAPPOINTMENT ORDER\nTo: CSE Faculty 012\nYou are appointed as Assistant "
          "Professor in the Department of CSE.\nYou joined duty on 16-07-2018.")


def oks(res):
    return [c["ok"] for c in res["checks"]]


def test_good_appointment_letter():
    res = check_proof(F, "appointment", LETTER, ALIASES)
    assert res["status"] == "verified" and all(oks(res))


def test_wrong_person_date_or_institute():
    assert check_proof(F, "appointment", LETTER.replace("012", "021"), ALIASES)["status"] == "problem"
    assert check_proof(F, "appointment", LETTER.replace("16-07-2018", "16-08-2018"), ALIASES)["status"] == "problem"
    res = check_proof(F, "appointment", LETTER.replace("CMR Institute of Technology", "Some Other College"), ALIASES)
    assert res["status"] == "problem"
    assert any("Institute name" in c["text"] for c in res["checks"] if c["ok"] is False)


def test_degree_certificate():
    cert = ("Visvesvaraya Technological University\nThis is to certify that CSE Faculty 012 has been awarded the "
            "degree of Doctor of Philosophy (Ph.D.) on 20 March 2021.")
    assert check_proof(F, "degree", cert, ALIASES)["status"] == "verified"
    mtech = cert.replace("Doctor of Philosophy (Ph.D.)", "Master of Technology (M.Tech)")
    assert check_proof(F, "degree", mtech, ALIASES)["status"] == "problem"


def test_unreadable_file():
    assert check_proof(F, "appointment", "   ", ALIASES)["status"] == "problem"
