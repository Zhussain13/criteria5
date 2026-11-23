"""Load NBA rule files (one YAML file per SAR format version)."""
from functools import lru_cache
from pathlib import Path

import yaml

RULES_DIR = Path(__file__).parent


def available_versions() -> dict[str, str]:
    out = {}
    for p in sorted(RULES_DIR.glob("*.yaml")):
        data = yaml.safe_load(p.read_text())
        out[p.stem] = data.get("name", p.stem)
    return out


@lru_cache(maxsize=8)
def load(version: str) -> dict:
    path = RULES_DIR / f"{version}.yaml"
    if not path.exists() or "/" in version or ".." in version:
        raise ValueError(f"Unknown SAR format version: {version}")
    data = yaml.safe_load(path.read_text())
    # YAML keys like 1, 2 come back as ints; item codes as strings
    data["criteria"] = {int(k): v for k, v in data["criteria"].items()}
    data["items"] = {str(k): v for k, v in data.get("items", {}).items()}
    data["tables"] = {str(k): [_norm_table(t) for t in v] for k, v in (data.get("tables") or {}).items()}
    return data


def _norm_table(t: dict) -> dict:
    cols = [c if isinstance(c, dict) else {"name": str(c)} for c in t.get("columns", [])]
    return {"id": str(t["id"]), "title": t["title"], "columns": cols, "rows": [str(r) for r in t.get("rows", [])],
            "fields": [str(f) for f in t.get("fields", [])], "note": t.get("note", "")}


def tables_for(version: str, code: str) -> list[dict]:
    """The tables the NBA SAR format asks for in this item (empty list if none)."""
    return load(version)["tables"].get(code, [])


def tables_text(tables: dict) -> str:
    """Filled tables as plain text (for the audit's evidence check and AI review)."""
    out = []
    for tid, t in (tables or {}).items():
        out.append(f"Table {tid}")
        out.extend(f"{k}: {v}" for k, v in (t.get("fields") or {}).items() if v)
        out.extend(" | ".join(c for c in row) for row in t.get("rows", []))
    return "\n".join(out)


def items_for(version: str, criterion: int) -> dict:
    return {c: i for c, i in load(version)["items"].items() if i["criterion"] == criterion}


def data_fields_for(version: str, criterion: int) -> list[dict]:
    return [f for f in load(version).get("data_fields", []) if f["criterion"] == criterion]


DATA_TYPES = {"enrolment_ratio", "success_rate", "api", "placement", "sfr", "fqi", "cadre", "adjunct",
              "retention", "fysfr", "improvement_api", "improvement_faculty",
              "success_index", "retention_pct", "fysfr_ratio", "fy_qualification", "improvement_data"}

KINDS = {
    "papers": [("journal", "Journal paper"), ("conference", "Conference paper"), ("book", "Book"),
               ("chapter", "Book chapter")],
    "seed_money": [("received", "Amount received"), ("utilized", "Amount utilised")],
}


def kinds_for(item: dict) -> list[tuple[str, str]]:
    if item.get("components") and item["type"] == "counts":
        return [(c["kind"], c["label"]) for c in item["components"]]
    return KINDS.get(item["type"], [])
