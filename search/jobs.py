import json
import re
import unicodedata
from urllib.parse import urlparse

from ddgs import DDGS

from config import SEARCH_AREAS, TARGET_LOCALITIES, JOB_TERMS, EXCLUDED_TERMS, PROFILE_PATH
from database import save_offer

JOB_WORDS = ["alternance", "apprentissage", "apprenti", "apprentie", "contrat d'apprentissage"]
ROLE_WORDS = [
    "développeur", "developpeuse", "developpeur", "developpeuse",
    "developer", "web developer",
    "développement web", "developpement web",
    "développement logiciel", "developpement logiciel",
    "full stack", "full-stack", "frontend", "front-end",
    "backend", "back-end", "ingénieur logiciel",
    "ingenieur logiciel", "ingénieur développement",
    "ingenieur developpement", "programmeur", "programmeuse",
    "concepteur développeur", "concepteur developpeur",
    "intégrateur web", "integrateur web", "software engineer",
]
JOB_BOARD_DOMAINS = [
    "hellowork.com", "indeed.com", "francetravail.fr", "welcometothejungle.com",
    "linkedin.com", "apec.fr", "jobteaser.com", "meteojob.com",
    "jobsora.com"
]

TRAINING_DOMAINS = ["pigier.com"]

def load_skills():
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        return data.get("all_skills", [])
    except Exception:
        return []

def is_excluded(text):
    lowered = text.lower()
    return any(term in lowered for term in EXCLUDED_TERMS)

def normalize_locality_text(value):
    text = unicodedata.normalize(
        "NFKD", str(value)
    ).encode("ascii", "ignore").decode("ascii").casefold()

    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " " + " ".join(text.split()) + " "


def find_target_locality(value):
    normalized = normalize_locality_text(value)

    for locality in sorted(TARGET_LOCALITIES, key=len, reverse=True):
        target = normalize_locality_text(locality).strip()

        if target and f" {target} " in normalized:
            return locality

    return ""

def find_job_locality(title, snippet):
    # Une localisation explicite dans le titre est prioritaire.
    title_pattern = re.compile(
        r"\b(?:à|a)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’ -]*?)"
        r"(?=\s*(?:\(|$|[-|–—]))",
        re.IGNORECASE,
    )

    # Dans la description, on exige un contexte qui indique
    # réellement le lieu du poste.
    description_pattern = re.compile(
        r"\b(?:poste|emploi|lieu de travail|localisation|site de travail)"
        r"\b.{0,50}?\b(?:situé|basé|localisé)?\s*(?:à|a|:)\s*"
        r"([^.;,\n]+)",
        re.IGNORECASE,
    )

    for pattern, text in (
        (title_pattern, title),
        (description_pattern, snippet),
    ):
        match = pattern.search(text)

        if not match:
            continue

        candidate = re.split(
            r"\s+(?:et|ou)\s+",
            match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip(" -–—()[]")

        locality = find_target_locality(candidate)

        # Une localisation explicite hors de notre zone
        # entraîne le rejet de l'offre.
        return locality or None

    # Aucune localisation explicite : on ne prend pas de risque.
    return None

def parse_job_url(url):
    if not isinstance(url, str) or not url or url != url.strip():
        return None

    if any(char.isspace() for char in url):
        return None

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        parsed.port  # Vérifie aussi que le port est valide.
    except ValueError:
        return None

    if parsed.scheme.lower() not in ("http", "https"):
        return None

    if not parsed.netloc or not hostname:
        return None

    # Refuser les identifiants intégrés dans l'URL.
    if parsed.username is not None or parsed.password is not None:
        return None

    return parsed, hostname.lower().rstrip(".")

def is_job_board_domain(hostname):
    hostname = hostname.lower().rstrip(".").removeprefix("www.")

    return any(
        hostname == domain or hostname.endswith("." + domain)
        for domain in JOB_BOARD_DOMAINS
    )


def is_training_domain(hostname):
    """Identifie les domaines connus d'organismes de formation."""
    hostname = hostname.lower().rstrip(".").removeprefix("www.")
    return any(
        hostname == domain or hostname.endswith("." + domain)
        for domain in TRAINING_DOMAINS
    )


def is_search_or_listing_url(parsed, hostname):
    """Détecte les pages de résultats et de catégories, pas les offres individuelles."""
    path = parsed.path.lower()
    query = parsed.query.lower()
    host = hostname.lower().removeprefix("www.")

    # Pages génériques de recherche et de résultats.
    search_markers = (
        "/recherche", "/search", "/resultats",
        "/results", "/jobs-search", "/job-search",
    )

    if any(marker in path for marker in search_markers):
        return True

    if re.search(r"(?:^|&)(?:q|query|keywords)=", query):
        return True

    # Bing : liens publicitaires ou pages de recherche.
    if host == "bing.com" or host.endswith(".bing.com"):
        if path.startswith("/aclick") or path.startswith("/search"):
            return True

    # Indeed : pages de listes d'emplois.
    if host == "indeed.com" or host.endswith(".indeed.com"):
        if (
            re.match(r"^/q-[^/]*-emplois\.html$", path)
            or re.match(r"^/[^/]+-\(\d+\)-emplois(?:-|$)", path)
        ):
            return True

    # Jooble : pages de recherche par métier et localisation.
    if (
        host == "jooble.org" or host.endswith(".jooble.org")
        or host == "jobsora.com" or host.endswith(".jobsora.com")
    ):
        if path.startswith(("/emploi-", "/emplois-")):
            return True

    # HelloWork : pages de catégories et de filtres.
    if host == "hellowork.com" or host.endswith(".hellowork.com"):
        if (
            path.startswith("/fr-fr/alternance/")
            and any(marker in path for marker in (
                "metier_", "domaine_", "ville_",
            ))
        ):
            return True

    return False


def build_message(title, company, matched):
    company_name = company.strip() or "votre entreprise"
    skills = ", ".join(matched[:5]) if matched else "mes compétences en développement et ma capacité à apprendre"
    return (
        f"Bonjour,\n\n"
        f"Je vous contacte au sujet de votre offre « {title} » en alternance. "
        f"Je souhaite rejoindre {company_name} afin de développer mes compétences "
        f"et contribuer à vos projets. Mon profil comprend notamment : {skills}.\n\n"
        f"Vous pouvez consulter mon CV : https://gghhhyhhh.github.io/cv/\n"
        f"Mon portfolio : https://gghhhyhhh.github.io/portfolio-independant/index.html\n\n"
        f"Je serais heureux d'échanger avec vous au sujet de cette opportunité.\n\n"
        f"Cordialement,\nHicham"
    )

def search_jobs(max_per_query=8, dry_run=False, max_queries=None):
    skills = load_skills()
    queries = []
    for area in SEARCH_AREAS:
        for term in JOB_TERMS:
            queries.append(f"{term} {area}")
    if max_queries is not None:
        queries = queries[:max_queries]

    total = 0
    seen = set()

    with DDGS() as ddgs:
        for query in queries:
            print(f"Recherche : {query}")
            try:
                results = ddgs.text(query, max_results=max_per_query)
                for result in results:
                    title = result.get("title", "").strip()
                    url = result.get("href", "").strip()
                    snippet = result.get("body", "").strip()
                    if not title or not url or url in seen:
                        continue
                    seen.add(url)
                    combined = f"{title} {snippet}"

                    locality = find_job_locality(title, snippet)

                    # Sans localisation explicite dans notre zone, on écarte l'offre.
                    if not locality:
                        continue

                    if is_excluded(combined):
                        continue
                    lower = combined.lower()
                    if not any(word in lower for word in JOB_WORDS):
                        continue
                    # Le métier doit apparaître dans le titre.
                    title_lower = title.lower()
                    if not any(word in title_lower for word in ROLE_WORDS):
                        continue

                    # Vérifier que l'URL est valide et sécurisée.
                    url_data = parse_job_url(url)
                    if not url_data:
                        continue

                    parsed, hostname = url_data

                    # Écarter les pages de recherche et les listes d'annonces.
                    if is_search_or_listing_url(parsed, hostname):
                        continue

                    domain = hostname.removeprefix("www.")

                    if is_training_domain(domain):
                        continue

                    source_type = (
                        "site d'emploi / intermédiaire"
                        if is_job_board_domain(domain)
                        else "site à vérifier (potentiellement entreprise)"
                    )
                    matched = [skill for skill in skills if skill.lower() in lower]
                    score = min(100, 20 + len(matched) * 12)
                    location = locality
                    item = {
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "location": location,
                        "source_domain": domain,
                        "source_type": source_type,
                        "matched_skills": matched,
                        "score": score,
                        "tailored_message": build_message(title, "", matched),
                    }

                    if dry_run:
                        print("\n[APERÇU - NON ENREGISTRÉ]")
                        print(f"Poste : {title}")
                        print(f"Lieu : {location}")
                        print(f"Source : {source_type} ({domain})")
                        print(f"Score : {score}/100")
                        print(f"URL : {url}")
                    else:
                        save_offer(item)

                    total += 1
            except Exception as exc:
                print(f"Recherche ignorée ({query}) : {exc}")
    if dry_run:
        print(f"\nAperçu terminé : {total} résultat(s) affiché(s), aucun enregistrement.")
    else:
        print(f"\nRecherche terminée. {total} résultat(s) candidat(s) enregistrés (doublons ignorés).")
    print("Attention : les résultats doivent être vérifiés manuellement avant toute candidature.")
