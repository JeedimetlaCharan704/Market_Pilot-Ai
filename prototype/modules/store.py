"""Lead store: SQLite (stdlib). Every lead keeps a persisted status."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from .settings import runtime_dir

ROOT = runtime_dir()
DB_PATH = ROOT / "marketpilot.db"

# The status machine. Anything not listed is rejected.
STATUS_FLOW: dict[str, set[str]] = {
    "DISCOVERED": {"AUDITED", "NO_WEBSITE", "FAILED"},
    "NO_WEBSITE": {"AUDITED", "DEMO_GENERATED", "DRAFTED", "FAILED"},
    "AUDITED": {"DEMO_GENERATED", "DRAFTED", "FAILED"},
    "NO_GAP": {"DEMO_GENERATED", "DRAFTED", "FAILED"},
    "DEMO_GENERATED": {"DRAFTED", "CLAIMED", "FAILED"},
    "DRAFTED": {"SENT", "CLAIMED", "FAILED"},
    "SENT": {"REPLIED", "INTERESTED", "NEEDS_HUMAN", "OPTED_OUT", "CLAIMED"},
    "REPLIED": {"INTERESTED", "NEEDS_HUMAN", "OPTED_OUT", "CLAIMED"},
    "NEEDS_HUMAN": {"INTERESTED", "OPTED_OUT", "CLAIMED"},
    "INTERESTED": {"OPTED_OUT", "CLAIMED"},
    "CLAIMED": set(),
    "OPTED_OUT": set(),
    # A failed step is retriable
    "FAILED": {"DISCOVERED", "AUDITED", "DEMO_GENERATED", "DRAFTED"},
}
STATUSES = list(STATUS_FLOW)


class InvalidTransition(RuntimeError):
    pass


class ResetRefused(RuntimeError):
    """A rewind would break the one-message-per-business guarantee."""


# Everything except SENT may be wound back for a re-audit. SENT is terminal.
REWINDABLE = set(STATUS_FLOW) - {"SENT"}


def reset_lead(lead_id: int) -> dict:
    """Discard audit, evidence, demo and draft so the lead can be re-audited."""
    lead = get_lead(lead_id)
    if lead is None:
        raise KeyError(f"lead {lead_id} not found")
    if lead["status"] not in REWINDABLE:
        raise ResetRefused(
            f"{lead['status']} is terminal - a lead that has been sent to cannot be "
            "rewound and contacted a second time"
        )
    with connect() as conn:
        conn.execute(
            """
            UPDATE leads
            SET status='DISCOVERED', status_reason=NULL, audit=NULL, evidence=NULL,
                demo_slug=NULL, email_draft=NULL, updated_at=?
            WHERE id=?
            """,
            (_now(), lead_id),
        )
    return get_lead(lead_id)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                search        TEXT NOT NULL,
                source        TEXT NOT NULL,
                external_id   TEXT NOT NULL,
                name          TEXT NOT NULL,
                website       TEXT,
                phone         TEXT,
                address       TEXT,
                city          TEXT,
                email         TEXT,
                rating        REAL,
                review_count  INTEGER,
                status        TEXT NOT NULL DEFAULT 'DISCOVERED',
                status_reason TEXT,
                audit         TEXT,
                evidence      TEXT,
                demo_slug     TEXT,
                demo_html     TEXT,
                email_draft   TEXT,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                UNIQUE (source, external_id)
            )
            """
        )
        existing = {
            r["name"] for r in conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        if "service" not in existing:
            conn.execute("ALTER TABLE leads ADD COLUMN service TEXT")
        if "demo_html" not in existing:
            conn.execute("ALTER TABLE leads ADD COLUMN demo_html TEXT")
        if "claimed_at" not in existing:
            conn.execute("ALTER TABLE leads ADD COLUMN claimed_at TEXT")
        if "claimed_by" not in existing:
            conn.execute("ALTER TABLE leads ADD COLUMN claimed_by TEXT")
        if "claim_notes" not in existing:
            conn.execute("ALTER TABLE leads ADD COLUMN claim_notes TEXT")
        if "crm_synced_at" not in existing:
            conn.execute("ALTER TABLE leads ADD COLUMN crm_synced_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS opt_outs (
                email   TEXT PRIMARY KEY,
                lead_id INTEGER,
                at      TEXT NOT NULL,
                source  TEXT NOT NULL DEFAULT 'reply'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS replies (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id        INTEGER,
                sender         TEXT NOT NULL,
                subject        TEXT,
                classification TEXT NOT NULL,
                auto_replied   INTEGER NOT NULL DEFAULT 0,
                at             TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS auto_replies (
                lead_id INTEGER PRIMARY KEY,
                at      TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS polls (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                at        TEXT NOT NULL,
                processed INTEGER NOT NULL
            )
            """
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("audit", "evidence", "email_draft"):
        out[key] = json.loads(out[key]) if out.get(key) else None
    return out


COLUMNS = (
    "name", "website", "phone", "address", "city", "email", "rating",
    "review_count", "search", "service",
)


def upsert_lead(record: dict) -> dict:
    provided = [c for c in COLUMNS if c in record]
    with connect() as conn:
        existing = conn.execute(
            "SELECT id FROM leads WHERE source = ? AND external_id = ?",
            (record["source"], record["external_id"]),
        ).fetchone()
        if existing:
            sets = [f"{c}=?" for c in provided] or ["updated_at=updated_at"]
            params = [record[c] for c in provided]
            sets.append("updated_at=?")
            params.extend([_now(), existing["id"]])
            conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id=?", params)
            lead_id = existing["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO leads (search, service, source, external_id, name, website, phone,
                                   address, city, email, rating, review_count,
                                   status, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.get("search") or "", record.get("service"), record["source"],
                    record["external_id"],
                    record["name"], record.get("website"), record.get("phone"),
                    record.get("address"), record.get("city"), record.get("email"),
                    record.get("rating"), record.get("review_count"),
                    "DISCOVERED", _now(), _now(),
                ),
            )
            lead_id = cur.lastrowid
    return get_lead(lead_id)


def get_lead(lead_id: int) -> dict | None:
    with connect() as conn:
        return _row_to_dict(conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())


def list_leads(search: str | None = None) -> list[dict]:
    query = "SELECT * FROM leads"
    params: tuple = ()
    if search:
        query += " WHERE search LIKE ?"
        params = (f"%{search}%",)
    query += " ORDER BY id DESC"
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(query, params).fetchall()]


def set_status(lead_id: int, target: str, reason: str | None = None) -> dict:
    lead = get_lead(lead_id)
    if lead is None:
        raise KeyError(f"lead {lead_id} not found")
    if target not in STATUS_FLOW:
        raise InvalidTransition(f"unknown status {target!r}")
    current = lead["status"]
    if target != current and target not in STATUS_FLOW.get(current, set()):
        raise InvalidTransition(f"{current} -> {target} is not an allowed transition")
    with connect() as conn:
        conn.execute(
            "UPDATE leads SET status=?, status_reason=?, updated_at=? WHERE id=?",
            (target, reason, _now(), lead_id),
        )
    return get_lead(lead_id)


def save(lead_id: int, **fields) -> dict:
    allowed = {
        "audit", "evidence", "demo_slug", "demo_html", "email_draft", "status_reason",
        "website", "phone", "email", "name", "address", "service",
        "claimed_at", "claimed_by", "claim_notes", "crm_synced_at",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"unknown columns: {sorted(unknown)}")
    sets, params = [], []
    for key, value in fields.items():
        sets.append(f"{key}=?")
        params.append(json.dumps(value) if isinstance(value, (dict, list)) else value)
    sets.append("updated_at=?")
    params.extend([_now(), lead_id])
    with connect() as conn:
        conn.execute(f"UPDATE leads SET {', '.join(sets)} WHERE id=?", params)
    return get_lead(lead_id)


def claim_lead(slug: str, claimed_by: str, notes: str = "") -> dict | None:
    lead = get_lead_by_slug(slug)
    if not lead:
        return None
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            UPDATE leads
            SET status='CLAIMED', status_reason=?, claimed_at=?, claimed_by=?, claim_notes=?, updated_at=?
            WHERE id=?
            """,
            (f"Claimed by {claimed_by}", now, claimed_by, notes, now, lead["id"]),
        )
    return get_lead(lead["id"])


def counts() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM leads GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def sent_today() -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM leads "
            "WHERE status='SENT' AND substr(updated_at, 1, 10) = date('now')"
        ).fetchone()
    return row["n"]


# --- opt-out registry --------------------------------------------------------


def _norm(email: str) -> str:
    return (email or "").strip().lower()


def add_opt_out(email: str, lead_id: int | None = None, source: str = "reply") -> bool:
    address = _norm(email)
    if not address:
        return False
    with connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM opt_outs WHERE email=?", (address,)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            "INSERT INTO opt_outs (email, lead_id, at, source) VALUES (?,?,?,?)",
            (address, lead_id, _now(), source),
        )
    return True


def is_opted_out(email: str) -> bool:
    address = _norm(email)
    if not address:
        return False
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM opt_outs WHERE email=?", (address,)
        ).fetchone() is not None


def list_opt_outs() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM opt_outs ORDER BY at DESC").fetchall()
        return [dict(r) for r in rows]


def remove_opt_out(email: str) -> bool:
    address = _norm(email)
    with connect() as conn:
        cur = conn.execute("DELETE FROM opt_outs WHERE email=?", (address,))
        return cur.rowcount > 0


# --- replies -----------------------------------------------------------------


def record_reply(
    lead_id: int | None,
    sender: str,
    subject: str,
    classification: str,
    auto_replied: bool = False,
) -> dict:
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO replies (lead_id, sender, subject, classification, auto_replied, at)
            VALUES (?,?,?,?,?,?)
            """,
            (
                lead_id, _norm(sender), (subject or "")[:200], classification,
                1 if auto_replied else 0, _now(),
            ),
        )
        row = conn.execute("SELECT * FROM replies WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def get_lead_by_email(email: str) -> dict | None:
    address = _norm(email)
    if not address:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE lower(email)=? ORDER BY id DESC LIMIT 1", (address,)
        ).fetchone()
    return _row_to_dict(row)


def get_lead_by_slug(slug: str) -> dict | None:
    s = (slug or "").strip()
    if not s:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE demo_slug=? ORDER BY id DESC LIMIT 1", (s,)
        ).fetchone()
    return _row_to_dict(row)


def find_lead_by_name(query: str) -> dict | None:
    q = (query or "").strip().lower()
    if not q or len(q) < 3:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE lower(name)=? OR lower(name) LIKE ? ORDER BY id DESC LIMIT 1",
            (q, f"%{q}%"),
        ).fetchone()
    return _row_to_dict(row)


def list_replies(lead_id: int | None = None) -> list[dict]:
    query = "SELECT * FROM replies"
    params: tuple = ()
    if lead_id is not None:
        query += " WHERE lead_id=?"
        params = (lead_id,)
    query += " ORDER BY id DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def can_auto_reply(lead_id: int) -> bool:
    with connect() as conn:
        return conn.execute(
            "SELECT 1 FROM auto_replies WHERE lead_id=?", (lead_id,)
        ).fetchone() is None


def mark_auto_replied(lead_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO auto_replies (lead_id, at) VALUES (?,?)",
            (lead_id, _now()),
        )


def record_poll(at: str, processed: int) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO polls (at, processed) VALUES (?,?)", (at, processed))


def last_poll() -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM polls ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def idea_performance() -> list[dict]:
    """Track which pitch categories and ideas get the highest reply rate (Bonus Track 2 criterion)."""
    with connect() as conn:
        leads = [_row_to_dict(r) for r in conn.execute("SELECT id, status, evidence, audit FROM leads").fetchall()]
        replies = [dict(r) for r in conn.execute("SELECT lead_id, classification FROM replies").fetchall()]

    stats: dict[str, dict] = {}
    for lead_row in leads:
        cards = lead_row.get("evidence") or []
        primary_gap = cards[0]["gap"] if cards else "unverified"
        if primary_gap not in stats:
            stats[primary_gap] = {"gap": primary_gap, "sent": 0, "replies": 0, "interested": 0}
        if lead_row["status"] == "SENT":
            stats[primary_gap]["sent"] += 1

    for r in replies:
        lid = r.get("lead_id")
        lead = next((item for item in leads if item["id"] == lid), None)
        cards = lead.get("evidence") or [] if lead else []
        primary_gap = cards[0]["gap"] if cards else "general"
        if primary_gap in stats:
            stats[primary_gap]["replies"] += 1
            if r["classification"] == "interested":
                stats[primary_gap]["interested"] += 1

    out = []
    for gap, data in stats.items():
        sent = data["sent"]
        replies_cnt = data["replies"]
        rate = round((replies_cnt / sent * 100), 1) if sent > 0 else 0.0
        out.append({
            "gap": gap,
            "pitches_sent": sent,
            "replies_received": replies_cnt,
            "interested_count": data["interested"],
            "reply_rate_pct": rate,
        })
    out.sort(key=lambda x: (-x["reply_rate_pct"], -x["pitches_sent"]))
    return out
