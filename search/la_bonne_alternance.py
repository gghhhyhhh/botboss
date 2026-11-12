import json
from datetime import datetime, timezone
import os
import re
import unicodedata
from urllib.parse import urlparse

import requests

from config import PROFILE_PATH, REQUEST_TIMEOUT, TARGET_LOCALITIES
from database import save_offer, save_prospect


API_URL = "https://api.apprentissage.beta.gouv.fr/api/job/v1/search"

# Seine-Maritime et Somme uniquement.
DEPARTMENTS = ["76", "80"]

ROME_CODES = ["M1805", "M1858"]

EXCLUDED_COMPANY_TERMS = [
    "cfa ",
    "école ",
    "ecole ",
    "organisme de formation",
    "centre de formation",
]

# Pertinence des activités selon le code NAF.
NAF_SCORES = {
    "62.01Z": 25,  # Programmation informatique
    "62.02A": 23,  # Conseil en systèmes et logiciels
    "62.02B": 21,  # Maintenance informatique
    "62.03Z": 18,  # Gestion d'installations informatiques
    "62.09Z": 18,  # Autres activités informatiques
    "63.11Z": 15,  # Traitement de données et hébergement
    "58.21Z": 15,  # Édition de jeux électroniques
    "26.20Z": 8,   # Fabrication d'ordinateurs
    "26.30Z": 8,   # Fabrication d'équipements de communication
    "26.40Z": 8,   # Fabrication de produits électroniques
}


# Pertinence NAF spécifique aux entreprises à prospecter.
# Maximum : 30 points.
PROSPECT_NAF_SCORES = {
    "62.01Z": 30,
    "62.02A": 30,
    "58.29A": 28,
    "58.29C": 28,
    "62.09Z": 25,
    "63.11Z": 22,
    "63.12Z": 22,
    "62.03Z": 20,
    "62.02B": 18,
    "26.20Z": 18,
    "26.30Z": 18,
    "26.40Z": 12,
}




def is_offer_available(offer, now=None):
    """
    Vérifie qu'une offre est active et n'a pas expiré.
    Une date d'expiration absente n'entraîne pas de rejet.
    """
    if not isinstance(offer, dict):
        return False

    status = str(offer.get("status") or "").strip().lower()

    if status != "active":
        return False

    publication = offer.get("publication")

    if publication is None:
        publication = {}

    if not isinstance(publication, dict):
        return False

    expiration = publication.get("expiration")

    if expiration is None or expiration == "":
        return True

    if not isinstance(expiration, str):
        return False

    try:
        expiration_date = datetime.fromisoformat(
            expiration.replace("Z", "+00:00")
        )

        if expiration_date.tzinfo is None:
            expiration_date = expiration_date.replace(
                tzinfo=timezone.utc
            )

        current_time = now or datetime.now(timezone.utc)

        if current_time.tzinfo is None:
            current_time = current_time.replace(
                tzinfo=timezone.utc
            )

        return expiration_date > current_time.astimezone(timezone.utc)

    except (ValueError, TypeError, AttributeError):
        # Une date invalide ne doit pas être considérée comme fiable.
        return False


def load_profile():
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}


def get_company(workplace):
    if not isinstance(workplace, dict):
        return ""

    return str(
        workplace.get("brand")
        or workplace.get("name")
        or workplace.get("legal_name")
        or ""
    ).strip()


def is_training_organization(company):
    normalized = company.lower().strip()

    return any(
        term in normalized
        for term in EXCLUDED_COMPANY_TERMS
    )


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


def get_location(workplace):
    location = workplace.get("location") or {}

    if isinstance(location, str):
        return location.strip()

    if not isinstance(location, dict):
        return ""

    return (
        location.get("city")
        or location.get("label")
        or location.get("address")
        or ""
    )


def get_target_locality(workplace):
    location = workplace.get("location") or {}

    if isinstance(location, str):
        return find_target_locality(location)

    if not isinstance(location, dict):
        return ""

    # La ville déclarée est prioritaire : ne pas la remplacer
    # par une autre commune trouvée dans l'adresse.
    city = str(location.get("city") or "").strip()
    if city:
        return find_target_locality(city)

    # Sans ville déclarée, utiliser le libellé puis l'adresse.
    for value in (location.get("label"), location.get("address")):
        value = str(value or "").strip()
        if value:
            return find_target_locality(value)

    return ""


def get_activity_info(workplace):
    domain = workplace.get("domain") or {}

    if not isinstance(domain, dict):
        return "", ""

    naf = domain.get("naf") or {}

    if not isinstance(naf, dict):
        return "", ""

    code = str(naf.get("code") or "").strip().upper()
    label = str(naf.get("label") or "").strip()

    return code, label


def get_activity_score(workplace):
    code, _ = get_activity_info(workplace)
    return NAF_SCORES.get(code, 0)


def get_skills(title, description, profile):
    text = f"{title} {description}".lower()
    skills = profile.get("all_skills", [])

    matched = []

    for skill in skills:
        skill = str(skill).strip()

        if not skill:
            continue

        # Évite les correspondances partielles dans des mots différents.
        pattern = (
            r"(?<!\w)"
            + re.escape(skill.lower())
            + r"(?!\w)"
        )

        if re.search(pattern, text):
            matched.append(skill)

    return matched


def get_technical_evidence_score(description):
    """
    Détecte les indices techniques dans une description réelle.
    Maximum : 15 points.
    """
    text = description.lower()

    categories = [
        (
            "développement logiciel",
            [
                "développement logiciel",
                "developpement logiciel",
                "programmation",
                "développeur",
                "developpeur",
                "software",
                "logiciel",
            ],
        ),
        (
            "web et api",
            [
                "application web",
                "site web",
                "frontend",
                "backend",
                "full stack",
                "api",
            ],
        ),
        (
            "infrastructure et données",
            [
                "base de données",
                "infrastructure",
                "cloud",
                "hébergement",
                "architecture logicielle",
            ],
        ),
    ]

    points = 0

    for _, terms in categories:
        if any(
            re.search(
                r"(?<!\w)" + re.escape(term) + r"(?!\w)",
                text,
            )
            for term in terms
        ):
            points += 5

    return min(points, 15)


def get_domain_match_score(title, workplace, is_job):
    """
    Évalue la correspondance avec le développement informatique.
    10 points : développement.
    5 points : métier informatique complémentaire.
    """
    if not is_job:
        naf_code, _ = get_activity_info(workplace)

        relevant_codes = {
            "62.01Z",
            "62.02A",
            "62.02B",
            "62.09Z",
        }

        return 10 if naf_code in relevant_codes else 0

    text = title.lower()

    developer_terms = (
        "développeur",
        "developpeur",
        "développement",
        "developpement",
        "programmation",
        "software",
        "logiciel",
    )

    if any(term in text for term in developer_terms):
        return 10

    related_it_terms = (
        "informatique",
        "technicien",
        "support",
        "systèmes d'information",
        "systemes d'information",
        "administrateur système",
        "administrateur systèmes",
    )

    if any(term in text for term in related_it_terms):
        return 5

    return 0




def get_prospect_activity_score(workplace):
    """Retourne la pertinence NAF d'un prospect sur 30 points."""
    code, _ = get_activity_info(workplace)
    return PROSPECT_NAF_SCORES.get(code, 0)


def get_prospect_technical_evidence_score(description):
    """
    Évalue les indices techniques présents dans une description réelle.
    Maximum : 20 points.
    """
    text = str(description or "").casefold()

    categories = [
        [
            "développement logiciel",
            "developpement logiciel",
            "programmation",
            "développeur",
            "developpeur",
            "software",
            "logiciel",
        ],
        [
            "application web",
            "site web",
            "frontend",
            "backend",
            "full stack",
            "api",
        ],
        [
            "base de données",
            "base de donnees",
            "postgresql",
            "sqlite",
            "mysql",
            "sql",
        ],
        [
            "infrastructure",
            "cloud",
            "hébergement",
            "hebergement",
            "architecture logicielle",
        ],
    ]

    points = 0

    for terms in categories:
        if any(
            re.search(
                r"(?<!\w)" + re.escape(term) + r"(?!\w)",
                text,
            )
            for term in terms
        ):
            points += 5

    return min(points, 20)


def is_valid_web_url(value):
    """Vérifie qu'une URL HTTP(S) est disponible."""
    value = str(value or "").strip()

    if not value:
        return False

    parsed = urlparse(value)

    return (
        parsed.scheme in ("http", "https")
        and bool(parsed.netloc)
    )


def calculate_prospect_score(
    matched_skills,
    description,
    workplace,
    company,
    apply,
):
    """
    Calcule la pertinence technique d'un prospect sur 100 points.

    Ce score ne représente pas une probabilité de recrutement.
    """
    workplace = workplace or {}
    apply = apply or {}

    breakdown = {}

    # Activité NAF : 30 points maximum.
    breakdown["NAF"] = get_prospect_activity_score(workplace)

    # Compétences réellement détectées : 30 points maximum.
    breakdown["Compétences"] = min(
        30,
        len(matched_skills) * 6,
    )

    # Indices techniques : 20 points maximum.
    breakdown["Indices techniques"] = (
        get_prospect_technical_evidence_score(description)
    )

    # Complétude des informations : 20 points maximum.
    completeness = 0

    if str(company or "").strip():
        completeness += 5

    siret = re.sub(
        r"\D",
        "",
        str(workplace.get("siret") or ""),
    )

    if len(siret) == 14:
        completeness += 5

    if is_valid_web_url(workplace.get("website")):
        completeness += 5

    if is_valid_web_url(apply.get("url")):
        completeness += 5

    breakdown["Informations disponibles"] = completeness

    return min(sum(breakdown.values()), 100), breakdown


def calculate_score(
    matched_skills,
    title,
    description,
    activity_score=0,
    is_job=True,
    workplace=None,
):
    """
    Calcule un score de pertinence sur 100 points.
    """
    workplace = workplace or {}

    # Compétences réellement détectées : 40 points maximum.
    score = min(40, len(matched_skills) * 8)

    # Activité informatique selon le code NAF : 25 points maximum.
    score += min(25, max(0, activity_score))

    # Indices techniques présents dans la description : 15 points.
    score += get_technical_evidence_score(description)

    # Correspondance avec le développement informatique : 10 points.
    score += get_domain_match_score(title, workplace, is_job)

    # Bonus alternance réservé aux offres publiées : 10 points.
    if is_job:
        text = f"{title} {description}".lower()

        apprenticeship_terms = (
            "alternance",
            "apprentissage",
            "apprenti",
            "apprentie",
        )

        if any(term in text for term in apprenticeship_terms):
            score += 10

    return min(score, 100)


def build_message(company, title, matched_skills):
    skills_text = ", ".join(matched_skills[:6]) if matched_skills else (
        "développement informatique et réalisation de projets"
    )

    return (
        f"Bonjour,\n\n"
        f"Je vous contacte au sujet de votre opportunité "
        f"« {title} » au sein de {company}.\n\n"
        f"Je suis actuellement en formation de développeur informatique "
        f"à Zone01 Rouen et je recherche une alternance. "
        f"Mes compétences comprennent notamment : {skills_text}.\n\n"
        f"Vous pouvez consulter mon CV : "
        f"https://gghhhyhhh.github.io/cv/\n"
        f"Et découvrir mes projets : "
        f"https://gghhhyhhh.github.io/portfolio-independant/index.html\n\n"
        f"Je serais heureux d'échanger avec vous au sujet de cette opportunité.\n\n"
        f"Cordialement,\n"
        f"Hicham HEE\n"
        f"heehicham2@gmail.com"
    )


def make_internal_url(kind, identifier):
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(identifier))
    return f"botboss://la-bonne-alternance/{kind}/{safe_id}"



def deduplicate_records(records):
    """Supprime les doublons à partir des identifiants API."""
    unique = {}

    for record in records:
        if not isinstance(record, dict):
            continue

        identifier = record.get("identifier")
        if not isinstance(identifier, dict):
            identifier = {}

        record_id = (
            identifier.get("id")
            or identifier.get("partner_job_id")
        )

        if record_id:
            key = str(record_id)
        else:
            key = json.dumps(
                record,
                sort_keys=True,
                ensure_ascii=False,
            )

        if key not in unique:
            unique[key] = record

    return list(unique.values())


def search_lba(include_recruiters=True, dry_run=False):
    api_key = os.getenv("LBA_API_KEY", "").strip()

    if not api_key:
        print("Erreur : LBA_API_KEY est absente du fichier .env.")
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    print("Recherche auprès de La Bonne Alternance...")
    print("Départements :", ", ".join(DEPARTMENTS))
    print("Métiers ROME :", ", ".join(ROME_CODES))

    all_jobs = []
    all_recruiters = []

    try:
        for rome_code in ROME_CODES:
            params = [("romes", rome_code)]
            params.extend(
                ("departements", department)
                for department in DEPARTMENTS
            )

            print(f"Recherche du code ROME {rome_code}...")

            response = requests.get(
                API_URL,
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 401:
                print("Erreur 401 : clé API absente, invalide ou expirée.")
                return

            if response.status_code == 403:
                print("Erreur 403 : accès refusé pour cette clé API.")
                return

            if response.status_code == 419:
                print("Erreur 419 : limite de requêtes atteinte.")
                return

            response.raise_for_status()
            data = response.json()

            if not isinstance(data, dict):
                print("Erreur : format de réponse API invalide.")
                return

            jobs = data.get("jobs", [])
            recruiters = data.get("recruiters", [])

            if not isinstance(jobs, list):
                print("Erreur : la liste des offres API est invalide.")
                return

            if not isinstance(recruiters, list):
                print("Erreur : la liste des recruteurs API est invalide.")
                return

            all_jobs.extend(jobs)
            all_recruiters.extend(recruiters)

        data = {
            "jobs": deduplicate_records(all_jobs),
            "recruiters": deduplicate_records(all_recruiters),
        }

        print("Offres uniques trouvées :", len(data["jobs"]))
        print("Recruteurs uniques trouvés :", len(data["recruiters"]))

    except requests.Timeout:
        print("Erreur : délai dépassé pendant la connexion à l'API.")
        return

    except requests.exceptions.JSONDecodeError:
        print("Erreur : la réponse de l'API n'est pas un JSON valide.")
        return

    except requests.RequestException as error:
        print(f"Erreur API La Bonne Alternance : {error}")
        return

    except ValueError:
        print("Erreur : la réponse de l'API n'est pas un JSON valide.")
        return

    profile = load_profile()

    saved_jobs = 0
    saved_recruiters = 0
    skipped = 0

    # --------------------------------------------------
    # OFFRES PUBLIÉES
    # --------------------------------------------------

    for job in data.get("jobs", []):
        if not isinstance(job, dict):
            skipped += 1
            continue

        workplace = job.get("workplace")
        offer = job.get("offer")
        identifier = job.get("identifier")
        apply = job.get("apply")

        if not all(
            isinstance(value, dict)
            for value in (workplace, offer, identifier, apply)
        ):
            skipped += 1
            continue

        # Refuser les offres inactives ou expirées.
        if not is_offer_available(offer):
            skipped += 1
            continue

        company = get_company(workplace)
        locality = get_target_locality(workplace)

        # Refuser toute offre hors des communes autorisées.
        if not locality:
            skipped += 1
            continue

        # Une offre sans employeur identifiable n'est pas qualifiée.
        if job.get("is_delegated") is True or not company:
            skipped += 1
            continue

        if is_training_organization(company):
            skipped += 1
            continue

        title = str(offer.get("title") or "").strip()

        # Refuser les annonces sans véritable intitulé de poste.
        if not title:
            skipped += 1
            continue

        description = (
            offer.get("description")
            or workplace.get("description")
            or ""
        )

        matched = get_skills(title, description, profile)

        activity_score = get_activity_score(workplace)

        score = calculate_score(
            matched,
            title,
            description,
            activity_score=activity_score,
            is_job=True,
            workplace=workplace,
        )

        partner = identifier.get("partner_label") or (
            "La Bonne Alternance"
        )

        partner_id = (
            identifier.get("partner_job_id")
            or identifier.get("id")
        )

        application_url = apply.get("url") or ""

        if not is_valid_web_url(application_url):
            skipped += 1
            continue

        domain = (
            urlparse(application_url).netloc
            or "identifiant-interne"
        )

        item = {
            "title": title,
            "company": company,
            "url": application_url,
            "snippet": description[:1500],
            "location": get_location(workplace) or locality,
            "source_domain": domain,
            "source_type": f"La Bonne Alternance – {partner}",
            "matched_skills": matched,
            "score": score,
            "tailored_message": build_message(
                company,
                title,
                matched,
            ),
        }

        if dry_run:
            print("\n[APERÇU - OFFRE PUBLIÉE - NON ENREGISTRÉE]")
            print(f"Entreprise : {company}")
            print(f"Poste : {title}")
            print(f"Lieu : {item['location']}")
            print(f"Score : {score}/100")
            print(f"Source : {partner}")
            print(f"URL : {application_url}")
        else:
            save_offer(item)

        saved_jobs += 1

    # --------------------------------------------------
    # ENTREPRISES À POTENTIEL
    # --------------------------------------------------

    if include_recruiters:
        for recruiter in data.get("recruiters", []):
            if not isinstance(recruiter, dict):
                skipped += 1
                continue

            workplace = recruiter.get("workplace")
            identifier = recruiter.get("identifier")
            apply = recruiter.get("apply")

            if not all(
                isinstance(value, dict)
                for value in (workplace, identifier, apply)
            ):
                skipped += 1
                continue

            company = get_company(workplace)
            recruiter_id = identifier.get("id")
            locality = get_target_locality(workplace)

            if not locality:
                skipped += 1
                continue

            if not company or not recruiter_id:
                skipped += 1
                continue

            if is_training_organization(company):
                skipped += 1
                continue

            activity_score = get_prospect_activity_score(workplace)

            # Sans activité informatique identifiée,
            # l'entreprise n'est pas ajoutée comme prospect qualifié.
            if activity_score == 0:
                skipped += 1
                continue

            description = workplace.get("description") or ""

            naf_code, naf_label = get_activity_info(workplace)

            title = (
                "Candidature spontanée – Développement informatique"
            )

            # Les compétences doivent apparaître dans la description réelle.
            matched = get_skills(
                "",
                description,
                profile,
            )

            score, score_details = calculate_prospect_score(
                matched,
                description,
                workplace,
                company,
                apply,
            )

            domain = workplace.get("domain") or {}
            opco = domain.get("opco") or "Non renseigné"
            size = workplace.get("size") or "Non renseignée" 

            snippet = (
                "Entreprise identifiée par La Bonne Alternance "
                "comme ayant un potentiel de recrutement en apprentissage. "
                "Aucune offre précise n'est publiée dans ce résultat.\n"
                f"Activité NAF : {naf_code} – {naf_label}\n"
                f"Taille : {size}\n"
                f"OPCO : {opco}\n"
                f"Description : {description}"
            )

            item = {
                "title": title,
                "company": company,
                "website": workplace.get("website") or "",
                "siret": re.sub(
                    r"\D",
                    "",
                    str(workplace.get("siret") or ""),
                ),
                "legal_name": str(
                    workplace.get("legal_name") or ""
                ).strip(),
                "brand": str(
                    workplace.get("brand") or ""
                ).strip(),
                "url": make_internal_url(
                    "recruiter",
                    recruiter_id,
                ),
                "snippet": snippet[:1500],
                "location": get_location(workplace) or locality,
                "source_domain": "La Bonne Alternance",
                "source_type": (
                    "Entreprise à potentiel – candidature spontanée"
                ),
                "matched_skills": matched,
                "score": score,
                "tailored_message": build_message(
                    company,
                    title,
                    matched,
                ),
            }

            if dry_run:
                print("\n[APERÇU - ENTREPRISE À POTENTIEL - NON ENREGISTRÉE]")
                print(f"Entreprise : {company}")
                print(f"Nom commercial : {item['brand']}")
                print(f"Nom légal : {item['legal_name']}")
                print(f"SIRET : {item['siret']}")
                print(f"Lieu : {item['location']}")
                print(f"Activité : {naf_code} – {naf_label}")
                print(f"Taille : {size}")
                print(f"OPCO : {opco}")
                print("\nDétail du score :")

                for criterion, points in score_details.items():
                    print(f"  {criterion} : {points}")

                print(f"Score technique : {score}/100")
                print("Statut : Recruteur référencé par LBA")
                print("Aucune offre précise publiée.")
            else:
                save_prospect(item)

            saved_recruiters += 1

    print("\n========== RÉSULTAT ==========")
    if dry_run:
        print(f"Offres publiées affichées en aperçu : {saved_jobs}")
        print(f"Entreprises affichées en aperçu : {saved_recruiters}")
        print("Aucun résultat n'a été enregistré.")
    else:
        print(f"Offres publiées traitées : {saved_jobs}")
        print(f"Entreprises informatiques qualifiées : {saved_recruiters}")

    print(f"Résultats ignorés : {skipped}")
    print("Aucune candidature n'a été envoyée.")
