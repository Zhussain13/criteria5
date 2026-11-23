-- CMRSARathi Criterion 5 database schema (SQLite)

-- Colleges that subscribe to CMRSARathi. Each one sees only its own users, programs and data.
CREATE TABLE IF NOT EXISTS institutions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,                 -- "CMR Institute of Technology"
    short_name  TEXT NOT NULL DEFAULT '',      -- "CMRIT"
    code        TEXT NOT NULL UNIQUE,          -- used in the login link, e.g. "cmrit"
    plan        TEXT NOT NULL DEFAULT 'College Plan',
    valid_until TEXT,                          -- ISO date; empty = no end date
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Roles: superadmin (platform owner, no institution), principal, hod, crit_head (criteria head), member.
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    role           TEXT NOT NULL,
    institution_id INTEGER REFERENCES institutions(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS programs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,              -- e.g. "B.E. Computer Science and Engineering"
    department      TEXT NOT NULL,
    institute_name  TEXT NOT NULL,              -- used for affiliation and parent-institute checks
    tier            TEXT NOT NULL DEFAULT 'Tier I',
    sar_version     TEXT NOT NULL DEFAULT 'criterion5_tier1_2026',
    cay_start_year  INTEGER NOT NULL,           -- CAY 2026-27 -> 2026
    ay_start_month  INTEGER NOT NULL DEFAULT 7, -- academic year starts in this month (1-12)
    institution_id  INTEGER REFERENCES institutions(id) ON DELETE CASCADE,
    doc_settings    TEXT NOT NULL DEFAULT '{}', -- SAR document look: font, size, margins
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS program_members (
    program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    criteria   TEXT NOT NULL DEFAULT '',  -- comma-separated criteria a criteria head / member works on
    PRIMARY KEY (program_id, user_id)
);

-- Faculty list = NBA Table B2.A (Department + allied departments). Criterion 5 counts come from here.
CREATE TABLE IF NOT EXISTS faculty (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id   INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    designation  TEXT NOT NULL DEFAULT '',  -- present designation: Professor / Associate Professor / Assistant Professor / Professor of Practice
    designation_at_joining TEXT NOT NULL DEFAULT '',
    designated_on TEXT,                     -- date designated as Professor / Associate Professor (promotion), ISO
    joining_date TEXT,                      -- date of joining this institute, ISO
    leaving_date TEXT,                      -- ISO date or NULL if still working
    qualification TEXT NOT NULL DEFAULT '',  -- highest degree: Ph.D. / M.Tech / M.E. / ...
    phd_date     TEXT,                      -- date the Ph.D. was awarded (if any), ISO
    university   TEXT NOT NULL DEFAULT '',  -- university of the highest degree
    specialization TEXT NOT NULL DEFAULT '',
    department   TEXT NOT NULL DEFAULT '',  -- department name (allied departments are counted for SFR, cadre, FQI)
    allied       INTEGER NOT NULL DEFAULT 0, -- 1 = allied department, 0 = the program's own department
    nature       TEXT NOT NULL DEFAULT 'Regular', -- Regular / Contract / Ad hoc / Professor of Practice
    full_time    INTEGER NOT NULL DEFAULT 1,
    first_year   INTEGER NOT NULL DEFAULT 0, -- 1 = 100% first-year teaching load (FF)
    is_hod       INTEGER NOT NULL DEFAULT 0,
    apaar_id     TEXT NOT NULL DEFAULT '',
    experience_years TEXT NOT NULL DEFAULT '',
    teaching_areas TEXT NOT NULL DEFAULT '',
    research_areas TEXT NOT NULL DEFAULT '',
    publications INTEGER,
    profile_url TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'manual', -- manual / file / erp
    updated_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at   TEXT
);

-- Year-wise staff list read from the ERP: who was on the rolls in that academic year and as what.
-- `year` is the SAR year (CAY, CAYm1, ...) it was put into; `ay_label` the ERP academic year (e.g. 2023-24).
CREATE TABLE IF NOT EXISTS faculty_year (
    faculty_id    INTEGER NOT NULL REFERENCES faculty(id) ON DELETE CASCADE,
    year          TEXT NOT NULL,
    ay_label      TEXT NOT NULL,
    designation   TEXT,
    qualification TEXT,
    nature        TEXT,
    full_time     INTEGER,
    first_year    INTEGER,
    allied        INTEGER,
    PRIMARY KEY (faculty_id, year)
);

-- Each ERP import: which ERP academic year went into which SAR year, by whom.
CREATE TABLE IF NOT EXISTS erp_imports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id  INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    year        TEXT NOT NULL,
    ay_label    TEXT NOT NULL,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    summary     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Who changed what (shown to the criteria head, HoD and principal on the dashboard).
CREATE TABLE IF NOT EXISTS activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id  INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Proof files behind the faculty list (appointment letter, degree certificate, promotion letter).
CREATE TABLE IF NOT EXISTS faculty_proofs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    faculty_id    INTEGER NOT NULL REFERENCES faculty(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,             -- appointment / degree / promotion
    original_name TEXT NOT NULL,
    stored_name   TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    ocr_text      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'problem', -- verified / problem
    checks_json   TEXT NOT NULL DEFAULT '[]',
    uploaded_by   INTEGER REFERENCES users(id),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per claim made in the SAR (one FDP attended, one paper, one placement, ...).
CREATE TABLE IF NOT EXISTS claims (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id   INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    criterion    INTEGER NOT NULL CHECK (criterion BETWEEN 1 AND 9),
    item_code    TEXT NOT NULL,             -- e.g. "6.1.2.2"
    kind         TEXT NOT NULL DEFAULT '',  -- sub-type, e.g. patent 'granted' / 'published', paper 'journal'
    person_name  TEXT NOT NULL DEFAULT '',  -- faculty or student the claim is about
    title        TEXT NOT NULL DEFAULT '',
    organizer    TEXT NOT NULL DEFAULT '',
    start_date   TEXT,                      -- ISO date
    end_date     TEXT,                      -- ISO date
    amount       REAL,                      -- for grants, consultancy (in lakh)
    affiliation  TEXT NOT NULL DEFAULT '',
    doi          TEXT NOT NULL DEFAULT '',
    indexing     TEXT NOT NULL DEFAULT '',  -- e.g. Scopus, WoS
    sar_text     TEXT NOT NULL DEFAULT '',  -- what the SAR says, plain text (used by the audit)
    sar_html     TEXT NOT NULL DEFAULT '',  -- the same, formatted (Word-style editor)
    tables_json  TEXT NOT NULL DEFAULT '{}', -- SAR tables for this item: {table id: {fields: {}, rows: [[...]]}}
    created_by   INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_by   INTEGER REFERENCES users(id),
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS proofs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id       INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    original_name  TEXT NOT NULL,
    stored_name    TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    ocr_text       TEXT NOT NULL DEFAULT '',
    extracted_json TEXT NOT NULL DEFAULT '{}', -- fields read from the proof (dates, names, ...)
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_proofs_sha ON proofs(sha256);

-- Year-wise numbers for the SAR tables (Criterion 4, 5 and 9.1), e.g. key 'N1', year 'CAYm1'.
CREATE TABLE IF NOT EXISTS metrics (
    program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    year       TEXT NOT NULL,
    value      REAL NOT NULL,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (program_id, key, year)
);

CREATE TABLE IF NOT EXISTS audits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id   INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    run_by       INTEGER REFERENCES users(id),
    run_at       TEXT NOT NULL DEFAULT (datetime('now')),
    awarded      REAL NOT NULL,
    maximum      REAL NOT NULL,
    report_json  TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'rules',  -- rules / full_ai / update_ai / institute_ai
    run_role     TEXT NOT NULL DEFAULT '',
    scope        TEXT NOT NULL DEFAULT '',       -- criteria re-checked by AI (update / institute audits)
    counted      INTEGER NOT NULL DEFAULT 1      -- 0 after the admin resets the AI audit allowance
);

-- Cache of AI results so the same proof text is never sent (and paid for) twice.
CREATE TABLE IF NOT EXISTS ai_cache (
    key        TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Audits that run in the background (AI review can take minutes).
CREATE TABLE IF NOT EXISTS audit_jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id  INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    run_by      INTEGER REFERENCES users(id),
    status      TEXT NOT NULL DEFAULT 'running',   -- running / done / failed
    kind        TEXT NOT NULL DEFAULT 'full_ai',
    scope       TEXT NOT NULL DEFAULT '',
    audit_id    INTEGER,
    error       TEXT,
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
