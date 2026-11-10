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
    "linkedin.com", "apec.fr", "jobteaser.com", "meteojob.com"
]

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
            queries.append(f'"{term}" "{area}"')
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
                    locality = find_target_locality(
                        f"{combined} {url}"
                    )

                    # Sans commune identifiable, le résultat est écarté.
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

                    # Écarter les pages de recherche et de résultats.
                    parsed = urlparse(url)
                    path_lower = parsed.path.lower()
                    query_lower = parsed.query.lower()

                    search_markers = (
                        "/recherche", "/search", "/resultats",
                        "/results", "/jobs-search", "/job-search",
                    )

                    if any(marker in path_lower for marker in search_markers):
                        continue

                    if any(marker in query_lower for marker in (
                        "q=", "query=", "keywords=",
                    )):
                        continue

                    domain = urlparse(url).netloc.lower().removeprefix("www.")
                    source_type = "site d'emploi / intermédiaire" if any(
                        d in domain for d in JOB_BOARD_DOMAINS
                    ) else "site à vérifier (potentiellement entreprise)"
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
