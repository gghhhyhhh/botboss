import sqlite3
from config import DB_PATH

def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                company TEXT DEFAULT '',
                url TEXT NOT NULL UNIQUE,
                snippet TEXT DEFAULT '',
                location TEXT DEFAULT '',
                source_domain TEXT DEFAULT '',
                source_type TEXT DEFAULT 'à vérifier',
                matched_skills TEXT DEFAULT '',
                score INTEGER DEFAULT 0,
                tailored_message TEXT DEFAULT '',
                contact_email TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                human_verified INTEGER NOT NULL DEFAULT 0,
                found_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT DEFAULT ''
            )
        """)

        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(offers)").fetchall()
        }
        if "human_verified" not in columns:
            conn.execute("""
                ALTER TABLE offers
                ADD COLUMN human_verified INTEGER NOT NULL DEFAULT 0
            """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS prospects (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                company TEXT DEFAULT '',
                url TEXT NOT NULL UNIQUE,
                snippet TEXT DEFAULT '',
                location TEXT DEFAULT '',
                source_domain TEXT DEFAULT '',
                source_type TEXT DEFAULT '',
                matched_skills TEXT DEFAULT '',
                score INTEGER DEFAULT 0,
                tailored_message TEXT DEFAULT '',
                contact_email TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                found_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT DEFAULT ''
            )
        """)

        prospect_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(prospects)").fetchall()
        }
        prospect_new_columns = {
            "website": "TEXT DEFAULT ''",
            "siret": "TEXT DEFAULT ''",
            "legal_name": "TEXT DEFAULT ''",
            "brand": "TEXT DEFAULT ''",
        }

        for column, definition in prospect_new_columns.items():
            if column not in prospect_columns:
                conn.execute(
                    f"ALTER TABLE prospects ADD COLUMN {column} {definition}"
                )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS prospect_enrichments (
                prospect_id INTEGER PRIMARY KEY,
                official_website TEXT DEFAULT '',
                activity_description TEXT DEFAULT '',
                technologies TEXT DEFAULT '[]',
                recruitment_url TEXT DEFAULT '',
                contact_url TEXT DEFAULT '',
                public_emails TEXT DEFAULT '[]',
                sources TEXT DEFAULT '[]',
                verification_status TEXT DEFAULT '',
                checked_at TEXT DEFAULT '',
                FOREIGN KEY(prospect_id) REFERENCES prospects(id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS spontaneous_applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER NOT NULL UNIQUE,
                company TEXT NOT NULL DEFAULT '',
                recipient_email TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                sent_at TEXT DEFAULT '',
                FOREIGN KEY(prospect_id) REFERENCES prospects(id)
            )
        """)

        application_columns = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(spontaneous_applications)"
            ).fetchall()
        }

        if "email_source_url" not in application_columns:
            conn.execute("""
                ALTER TABLE spontaneous_applications
                ADD COLUMN email_source_url TEXT DEFAULT ''
            """)

        if "channel" not in application_columns:
            conn.execute("""
                ALTER TABLE spontaneous_applications
                ADD COLUMN channel TEXT NOT NULL DEFAULT 'email'
            """)

        if "portal_url" not in application_columns:
            conn.execute("""
                ALTER TABLE spontaneous_applications
                ADD COLUMN portal_url TEXT DEFAULT ''
            """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS invalid_email_addresses (
                email TEXT PRIMARY KEY COLLATE NOCASE,
                company TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS profile_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                skills_count INTEGER DEFAULT 0,
                profile_file TEXT DEFAULT ''
            )
        """)

def save_offer(item):
    with connect() as conn:
        conn.execute("""
            INSERT INTO offers
            (title, company, url, snippet, location, source_domain, source_type,
             matched_skills, score, tailored_message, status, human_verified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'to_verify', 0)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                snippet = excluded.snippet,
                location = excluded.location,
                source_domain = excluded.source_domain,
                source_type = excluded.source_type,
                matched_skills = excluded.matched_skills,
                score = excluded.score,
                tailored_message = excluded.tailored_message
            WHERE offers.status IN ('to_verify', 'pending')
              AND offers.human_verified = 0
        """, (
            item["title"],
            item.get("company", ""),
            item["url"],
            item.get("snippet", ""),
            item.get("location", ""),
            item.get("source_domain", ""),
            item.get("source_type", "à vérifier"),
            ", ".join(item.get("matched_skills", [])),
            item.get("score", 0),
            item.get("tailored_message", "")
        ))


def is_published_offer(offer):
    """Vérifie qu'une entrée correspond à une annonce individuelle de développement."""
    from urllib.parse import urlsplit

    if not offer:
        return False

    if 'human_verified' in offer.keys() and offer['human_verified'] == 1:
        return True

    source_type = str(offer["source_type"] or "").casefold()
    title = str(offer["title"] or "").casefold()
    snippet = str(offer["snippet"] or "").casefold()
    url = str(offer["url"] or "").strip()

    if any(term in source_type for term in (
        "entreprise à potentiel",
        "candidature spontanée",
        "potentiellement entreprise",
    )):
        return False

    parsed = urlsplit(url)
    domain = parsed.netloc.casefold().removeprefix("www.")
    url_path = parsed.path.casefold()

    if parsed.scheme not in ("http", "https"):
        return False

    # Bloquer les pages de résultats Indeed.
    if domain == "fr.indeed.com" and url_path.startswith("/q-"):
        return False

    # Bloquer les catalogues HelloWork par métier et ville.
    if domain.endswith("hellowork.com") and "/metier_" in url_path:
        return False

    search_markers = (
        "/recherche", "/search", "/resultats", "/results",
        "/jobs-search", "/job-search",
    )

    if any(marker in url_path for marker in search_markers):
        return False

    if any(term in (title + " " + snippet) for term in (
        "consultez les dernières offres",
        "consultez les dernieres offres",
        "vous êtes à la recherche d'un emploi",
        "vous etes a la recherche d'un emploi",
        "emplois : alternance",
        "voir les ...",
    )):
        return False

    apprenticeship_terms = (
        "alternance", "apprenti", "apprentie",
        "contrat d'apprentissage", "contrat d’apprentissage",
        "en apprentissage",
    )

    if not any(term in title + " " + snippet for term in apprenticeship_terms):
        return False

    development_terms = (
        "développeur", "developpeur", "développeuse",
        "developpeuse", "developer", "web developer",
        "développement web", "developpement web",
        "développement logiciel", "developpement logiciel",
        "full stack", "full-stack", "frontend", "front-end",
        "backend", "back-end", "ingénieur logiciel",
        "ingenieur logiciel", "ingénieur développement",
        "ingenieur developpement", "programmeur",
        "programmeuse", "concepteur développeur",
        "concepteur developpeur", "intégrateur web",
        "integrateur web", "software engineer",
    )

    return any(term in title for term in development_terms)


def get_offers(status=None):
    with connect() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM offers WHERE status=? ORDER BY score DESC, found_at DESC",
                (status,)
            ).fetchall()
        return conn.execute(
            "SELECT * FROM offers ORDER BY score DESC, found_at DESC"
        ).fetchall()

def get_offer(offer_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()

def update_offer(offer_id, **fields):
    allowed = {"company", "contact_email", "tailored_message", "status", "human_verified"}
    values = [(key, value) for key, value in fields.items() if key in allowed]
    if not values:
        return
    clause = ", ".join(f"{key}=?" for key, _ in values)
    with connect() as conn:
        conn.execute(f"UPDATE offers SET {clause} WHERE id=?",
                     [value for _, value in values] + [offer_id])


def transition_offer_status(offer_id, expected_status, new_status):
    """Effectue une transition de statut atomique pour une annonce."""
    allowed_transitions = {
        "approved": {"sending"},
        "sending": {"smtp_accepted", "delivery_unknown", "failed"},
        "smtp_accepted": {"bounced"},
        "delivery_unknown": {"bounced"},
    }

    if new_status not in allowed_transitions.get(expected_status, set()):
        raise ValueError(
            f"Transition d'annonce interdite : "
            f"{expected_status} -> {new_status}"
        )

    with connect() as conn:
        cursor = conn.execute("""
            UPDATE offers
            SET status = ?,
                sent_at = CASE
                    WHEN ? = 'smtp_accepted' THEN CURRENT_TIMESTAMP
                    ELSE sent_at
                END
            WHERE id = ? AND status = ?
        """, (new_status, new_status, offer_id, expected_status))

        return cursor.rowcount == 1


def save_prospect(item):
    skills = item.get("matched_skills", [])
    if isinstance(skills, list):
        skills = ", ".join(skills)

    with connect() as conn:
        conn.execute("""
            INSERT INTO prospects
            (title, company, url, snippet, location, source_domain,
             source_type, matched_skills, score, tailored_message, website,
             siret, legal_name, brand)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                company = excluded.company,
                snippet = excluded.snippet,
                location = excluded.location,
                source_domain = excluded.source_domain,
                source_type = excluded.source_type,
                matched_skills = excluded.matched_skills,
                score = excluded.score,
                tailored_message = excluded.tailored_message,
                website = excluded.website,
                siret = excluded.siret,
                legal_name = excluded.legal_name,
                brand = excluded.brand
            WHERE prospects.status = 'pending'
        """, (
            item["title"],
            item.get("company", ""),
            item["url"],
            item.get("snippet", ""),
            item.get("location", ""),
            item.get("source_domain", ""),
            item.get("source_type", ""),
            skills,
            item.get("score", 0),
            item.get("tailored_message", ""),
            item.get("website", ""),
            item.get("siret", ""),
            item.get("legal_name", ""),
            item.get("brand", "")
        ))


def get_prospects():
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM prospects ORDER BY score DESC, found_at DESC"
        ).fetchall()


def get_prospect(prospect_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM prospects WHERE id=?",
            (prospect_id,)
        ).fetchone()


def get_prospect_enrichment(prospect_id):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM prospect_enrichments WHERE prospect_id=?",
            (prospect_id,)
        ).fetchone()


def is_email_blocked(email):
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM invalid_email_addresses WHERE email=? COLLATE NOCASE",
            (email.strip(),)
        ).fetchone()
    return row is not None


def mark_email_invalid(email, company="", reason=""):
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Adresse e-mail incorrecte.")

    with connect() as conn:
        conn.execute("""
            INSERT INTO invalid_email_addresses (email, company, reason)
            VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                company=excluded.company,
                reason=excluded.reason,
                recorded_at=CURRENT_TIMESTAMP
        """, (email, company, reason))


def create_application(
    prospect_id, company, recipient_email, subject, message,
    email_source_url="", channel="email", portal_url=""
):
    recipient_email = recipient_email.strip()

    if channel not in ("email", "portal"):
        raise ValueError("Canal de candidature invalide.")

    if channel == "portal":
        from urllib.parse import urlsplit

        parsed = urlsplit(portal_url or "")
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.cgi.com"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not parsed.path.startswith("/france/fr-fr/recrute")
        ):
            raise ValueError("URL du portail CGI non autorisée.")

        if recipient_email:
            raise ValueError(
                "Une candidature via portail ne doit pas avoir de destinataire e-mail."
            )
    elif not recipient_email:
        raise ValueError("Une adresse e-mail est obligatoire pour ce canal.")

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        blocked = conn.execute(
            "SELECT 1 FROM invalid_email_addresses WHERE email=? COLLATE NOCASE",
            (recipient_email,)
        ).fetchone()

        if blocked:
            raise ValueError(
                "Envoi bloqué : cette adresse est enregistrée comme invalide."
            )

        existing = conn.execute("""
            SELECT id, status, recipient_email
            FROM spontaneous_applications
            WHERE prospect_id=?
        """, (prospect_id,)).fetchone()

        if existing:
            if existing["status"] in ("sent", "smtp_accepted", "sending", "delivery_unknown"):
                raise ValueError(
                    "Une candidature existe déjà dans un état protégé "
                    "(en cours, acceptée ou résultat incertain)."
                )

            if (
                existing["status"] == "bounced"
                and existing["recipient_email"].strip().lower()
                == recipient_email.lower()
            ):
                raise ValueError(
                    "Cette adresse a déjà échoué. Fournis une autre adresse."
                )

        conn.execute("""
            INSERT INTO spontaneous_applications
                (prospect_id, company, recipient_email, subject,
                 message, status, email_source_url, sent_at,
                 channel, portal_url)
            VALUES (?, ?, ?, ?, ?, 'draft', ?, '', ?, ?)
            ON CONFLICT(prospect_id) DO UPDATE SET
                company=excluded.company,
                recipient_email=excluded.recipient_email,
                subject=excluded.subject,
                message=excluded.message,
                status='draft',
                email_source_url=excluded.email_source_url,
                sent_at='',
                channel=excluded.channel,
                portal_url=excluded.portal_url
        """, (
            prospect_id, company, recipient_email, subject,
            message, email_source_url, channel, portal_url
        ))

        row = conn.execute(
            "SELECT id FROM spontaneous_applications WHERE prospect_id=?",
            (prospect_id,)
        ).fetchone()

        return row["id"]


def get_application(application_id):
    with connect() as conn:
        return conn.execute("""
            SELECT
                a.*,
                p.location,
                p.url AS prospect_url,
                e.official_website,
                e.verification_status
            FROM spontaneous_applications a
            JOIN prospects p ON p.id = a.prospect_id
            LEFT JOIN prospect_enrichments e
                ON e.prospect_id = p.id
            WHERE a.id=?
        """, (application_id,)).fetchone()


def get_applications():
    with connect() as conn:
        return conn.execute("""
            SELECT a.*, p.location
            FROM spontaneous_applications a
            JOIN prospects p ON p.id = a.prospect_id
            ORDER BY a.created_at DESC
        """).fetchall()


def update_application(application_id, **fields):
    if "status" in fields:
        raise ValueError(
            "Modification directe du statut interdite. "
            "Utilise transition_application_status()."
        )

    allowed = {"recipient_email", "subject", "message", "email_source_url"}
    values = [(key, value) for key, value in fields.items() if key in allowed]

    if not values:
        return

    clause = ", ".join(f"{key}=?" for key, _ in values)

    with connect() as conn:
        conn.execute(
            f"UPDATE spontaneous_applications SET {clause} WHERE id=?",
            [value for _, value in values] + [application_id]
        )

def transition_application_status(application_id, expected_status, new_status):
    """
    Effectue une transition atomique uniquement si elle est autorisée.
    Les statuts incertains ou terminaux ne sont jamais réinitialisés.
    """
    allowed_transitions = {
        "draft": {"sending"},
        "sending": {"smtp_accepted", "delivery_unknown", "failed"},
        "smtp_accepted": {"bounced"},
        "delivery_unknown": {"bounced"},
    }

    if new_status not in allowed_transitions.get(expected_status, set()):
        raise ValueError(
            f"Transition de statut interdite : "
            f"{expected_status} -> {new_status}."
        )

    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE spontaneous_applications
            SET status = ?,
                sent_at = CASE
                    WHEN ? = 'smtp_accepted' THEN CURRENT_TIMESTAMP
                    ELSE sent_at
                END
            WHERE id = ? AND status = ?
            """,
            (new_status, new_status, application_id, expected_status)
        )

        return cursor.rowcount == 1

