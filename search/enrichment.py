import json
import base64
import re
import socket
import ipaddress
from io import BytesIO
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qs

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from config import PROFILE_PATH, SEARXNG_URL
from database import connect, get_prospects
from search.jobs import find_target_locality

from email_validation import (
    PinnedHTTPSAdapter,
    validate_public_url,
    get_peer_ip,
    read_limited_response,
    MAX_REDIRECTS as PINNED_MAX_REDIRECTS,
    REQUEST_TIMEOUT as PINNED_REQUEST_TIMEOUT,
)


USER_AGENT = "BotBoss/1.0 (company website research)"
MAX_PAGES = 5
REQUEST_TIMEOUT = 12

CAREER_WORDS = (
    "recrutement", "recrutement", "carrieres", "carriere",
    "jobs", "job", "careers", "career", "nous-rejoindre",
    "nous-rejoindre", "emploi", "offres-emploi", "alternance",
    "apprentissage", "talents",
)

CONTACT_WORDS = (
    "contact", "nous-contacter", "coordonnees",
)

RELEVANT_WORDS = (
    "developpement", "logiciel", "informatique", "application",
    "applications", "web", "numerique", "digital", "saas",
    "logiciels", "solutions", "technologie", "technologies",
    "systeme", "systemes", "donnees", "cloud", "cybersecurite",
)


def normalize(value):
    import unicodedata

    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(c for c in value if not unicodedata.combining(c))
    return value.casefold()


def safe_official_url(value):
    """Accepte uniquement une URL HTTP(S) publique et correctement formée."""
    if not value:
        return None

    value = str(value).strip()
    if not value:
        return None

    if "://" not in value:
        value = "https://" + value

    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return None

        host = parsed.hostname.casefold().rstrip(".")
        if host in ("localhost",) or host.endswith(".localhost"):
            return None

        try:
            ip = ipaddress.ip_address(host)
            if not ip.is_global:
                return None
        except ValueError:
            pass

        # Refuse les identifiants intégrés à l'URL.
        if parsed.username or parsed.password:
            return None

        return urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.query,
            "",
        ))
    except (ValueError, TypeError):
        return None


def same_host(url, expected_host):
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme in ("http", "https")
            and parsed.hostname
            and parsed.hostname.casefold().rstrip(".") == expected_host
        )
    except ValueError:
        return False


def host_is_public(host):
    """Évite les noms locaux et les adresses IP privées."""
    if not host or host == "localhost" or host.endswith(".localhost"):
        return False

    try:
        addresses = socket.getaddrinfo(host, None)
    except OSError:
        return False

    for result in addresses:
        address = result[4][0].split("%")[0]
        try:
            if not ipaddress.ip_address(address).is_global:
                return False
        except ValueError:
            return False

    return bool(addresses)


def load_profile_skills():
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    skills = data.get("all_skills", [])
    if not isinstance(skills, list):
        return []

    return sorted({
        str(skill).strip()
        for skill in skills
        if str(skill).strip()
    })


def page_text(html):
    soup = BeautifulSoup(html, "html.parser")

    for element in soup(["script", "style", "noscript", "svg"]):
        element.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    description = ""
    meta = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    if meta:
        description = meta.get("content", "") or ""

    text = soup.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)

    return soup, title, description, text



def explicit_recruitment_links(soup, base_url, expected_host):
    """Repère les liens de recrutement grâce à leur libellé visible."""
    candidates = []

    for link in soup.find_all("a", href=True):
        label = normalize(link.get_text(" ", strip=True)).replace(" ", "-")

        if not any(word in label for word in CAREER_WORDS):
            continue

        url = safe_official_url(urljoin(base_url, link["href"]))
        if not url:
            continue

        parsed = urlsplit(url)

        if parsed.scheme != "https" or not same_host(url, expected_host):
            continue

        if parsed.path.lower().endswith((
            ".pdf", ".jpg", ".jpeg", ".png", ".webp",
            ".zip", ".doc", ".docx",
        )):
            continue

        clean_url = urlunsplit((
            parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""
        ))

        if clean_url not in candidates:
            candidates.append(clean_url)

    return candidates


def _has_recruitment_signal(text):
    """Détecte les termes indiquant une page dédiée au recrutement."""
    normalized = normalize(text).replace(" ", "-")
    extra_words = (
        "nous-rejoindre", "rejoindre", "recrute",
        "recrutement", "carriere", "carrieres",
        "emploi", "emplois", "alternance",
        "apprentissage", "offres-emploi",
        "jobs", "careers", "talents",
    )
    return any(word in normalized for word in CAREER_WORDS + extra_words)


def _has_job_offer_signal(text):
    """Vérifie que le contenu évoque concrètement des postes ou des offres."""
    normalized = normalize(text).replace(" ", "-")
    offer_words = (
        "offre-d-emploi", "offres-d-emploi",
        "offres-emploi", "poste-a-pourvoir",
        "postes-a-pourvoir", "alternance",
        "apprentissage", "job-opening",
        "job-opportunity", "vacancies",
        "candidater", "postuler",
    )
    return any(word in normalized for word in offer_words)


def _has_recruitment_location(url):
    """Une page doit être située sur un chemin ou sous-domaine carrière."""
    parsed = urlsplit(url)
    location = f"{parsed.hostname or ''} {parsed.path}"
    return _has_recruitment_signal(location)


def _recruitment_search_candidate(url, title, snippet, tokens):
    """Filtre les résultats avant toute connexion au portail."""
    safe_url = safe_official_url(url)
    if not safe_url:
        return ""

    parsed = urlsplit(safe_url)
    host = (parsed.hostname or "").casefold().rstrip(".")

    if parsed.scheme != "https" or not host or ignored_domain(host):
        return ""

    evidence = f"{host} {title} {snippet} {parsed.path}"
    location_evidence = f"{host} {parsed.path} {title}"

    identity_matches = [
        token for token in tokens
        if len(normalize(token)) >= 4 and contains_identity(evidence, token)
    ]

    if (
        not identity_matches
        or not _has_recruitment_signal(location_evidence)
    ):
        return ""

    return safe_url


def discover_recruitment_url(
    company,
    locality,
    legal_name="",
    brand="",
    official_website="",
):
    """Recherche un portail de recrutement puis vérifie son contenu réel."""
    tokens = company_tokens(company, legal_name, brand)
    if not tokens:
        return ""

    names = list(dict.fromkeys(
        name.strip()
        for name in (brand, legal_name, company)
        if name and name.strip()
    ))

    queries = []
    for name in names:
        queries.extend((
            f'"{name}" "{locality}" recrutement',
            f'"{name}" carrières alternance',
            f'"{name}" "nous rejoindre"',
        ))

    queries = list(dict.fromkeys(queries))[:9]
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    candidates = []

    def add_candidate(url, title, snippet):
        candidate = _recruitment_search_candidate(
            url, title, snippet, tokens
        )
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    # 1. SearXNG en priorité.
    if SEARXNG_URL:
        for query in queries:
            try:
                response = requests.get(
                    f"{SEARXNG_URL}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "language": "fr-FR",
                    },
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()

                if not isinstance(data, dict):
                    continue

                results = data.get("results", [])
                if not isinstance(results, list):
                    continue

                for item in results:
                    if not isinstance(item, dict):
                        continue
                    add_candidate(
                        item.get("url", ""),
                        item.get("title", ""),
                        item.get("content", ""),
                    )

            except (requests.RequestException, ValueError) as error:
                print(
                    "Recherche recrutement SearXNG : "
                    f"{type(error).__name__}"
                )

        verified = _verify_recruitment_candidates(candidates, tokens)
        if verified:
            return verified

    # 2. Bing en secours si aucun portail SearXNG n'a été vérifié.
    for query in queries:
        try:
            response = requests.get(
                "https://www.bing.com/search",
                params={"q": query, "count": 10},
                headers={
                    **headers,
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) "
                        "AppleWebKit/537.36 Chrome/130.0.0.0 "
                        "Safari/537.36"
                    ),
                },
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            for block in soup.select("li.b_algo"):
                heading = block.select_one("h2")
                link = heading.find("a", href=True) if heading else None
                if not link:
                    continue

                url = resolve_bing_result_url(link.get("href", ""))
                snippet = (
                    block.select_one(".b_caption p")
                    or block.select_one(".b_snippet")
                )

                add_candidate(
                    url,
                    heading.get_text(" ", strip=True),
                    snippet.get_text(" ", strip=True) if snippet else "",
                )

        except requests.RequestException as error:
            print(f"Recherche recrutement Bing : {type(error).__name__}")

    return _verify_recruitment_candidates(candidates, tokens)


def _verify_recruitment_candidates(candidates, tokens):
    """Accepte uniquement un portail HTTPS public dont le contenu confirme l'entreprise."""
    for candidate in candidates[:10]:
        host = urlsplit(candidate).hostname or ""
        print(f"Vérification du portail de recrutement : {candidate}")

        try:
            final_url, content_type, html = _fetch_pinned_page(
                candidate, host
            )

            if "text/html" not in content_type:
                continue

            soup, title, description, text = page_text(html)
            evidence = f"{title} {description} {text}"

            has_identity = any(
                len(normalize(token)) >= 4
                and contains_identity(evidence, token)
                for token in tokens
            )

            recruitment_location = _has_recruitment_location(final_url)
            has_job_offers = _has_job_offer_signal(
                f"{title} {description} {text}"
            )

            if not has_identity or not recruitment_location or not has_job_offers:
                print(
                    "Portail rejeté : identité, page carrière "
                    "ou offres non confirmées."
                )
                continue

            print(f"Portail de recrutement vérifié : {final_url}")
            return final_url

        except (requests.RequestException, ValueError, OSError) as error:
            print(
                "Portail non vérifié : "
                f"{type(error).__name__}"
            )

    return ""


def relevant_links(soup, base_url, expected_host):
    candidates = []

    for link in soup.find_all("a", href=True):
        href = urljoin(base_url, link["href"])
        parsed = urlsplit(href)

        if not same_host(href, expected_host):
            continue

        if parsed.path.lower().endswith((
            ".pdf", ".jpg", ".jpeg", ".png", ".webp",
            ".zip", ".doc", ".docx",
        )):
            continue

        label = normalize(
            link.get_text(" ", strip=True) + " " + parsed.path
        )

        is_relevant = any(word in label for word in (
            CAREER_WORDS + CONTACT_WORDS + RELEVANT_WORDS
        ))

        if is_relevant:
            clean_url = urlunsplit((
                parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""
            ))
            if clean_url not in candidates:
                candidates.append(clean_url)

    return candidates


def extract_public_emails(text):
    found = set(re.findall(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        text,
        flags=re.I,
    ))

    # Exclut les adresses manifestement techniques ou fictives.
    return sorted(
        email for email in found
        if not email.lower().endswith((
            "@example.com", "@example.org", "@domain.com",
        ))
    )



def _normalized_host(host):
    return (host or "").casefold().rstrip(".").removeprefix("www.")


def _fetch_pinned_page(url, expected_host):
    """Télécharge une page HTTPS publique avec IP épinglée et limites strictes."""
    current_url = url

    for redirect_count in range(PINNED_MAX_REDIRECTS + 1):
        parsed, host, addresses = validate_public_url(current_url)

        if _normalized_host(host) != _normalized_host(expected_host):
            raise ValueError("Domaine différent du site attendu.")

        if not addresses:
            raise ValueError("Aucune adresse IP publique.")

        pinned_ip = addresses[0]
        response = None

        with requests.Session() as session:
            session.trust_env = False
            session.headers.update({"User-Agent": USER_AGENT})

            adapter = PinnedHTTPSAdapter(pinned_ip)
            session.mount("https://", adapter)

            try:
                response = session.get(
                    current_url,
                    timeout=PINNED_REQUEST_TIMEOUT,
                    allow_redirects=False,
                    stream=True,
                    verify=True,
                )

                peer_ip = ipaddress.ip_address(get_peer_ip(response))
                expected_ip = ipaddress.ip_address(pinned_ip)

                if peer_ip != expected_ip or not peer_ip.is_global:
                    raise ValueError("Adresse IP distante inattendue.")

                if response.is_redirect or response.is_permanent_redirect:
                    if redirect_count >= PINNED_MAX_REDIRECTS:
                        raise ValueError("Trop de redirections.")

                    location = response.headers.get("Location", "")
                    if not location:
                        raise ValueError("Redirection sans destination.")

                    target = safe_official_url(
                        urljoin(current_url, location)
                    )
                    if not target:
                        raise ValueError("Destination de redirection invalide.")

                    target_parsed = urlsplit(target)
                    if target_parsed.scheme != "https":
                        raise ValueError("Redirection HTTPS obligatoire.")

                    if _normalized_host(target_parsed.hostname) != _normalized_host(expected_host):
                        raise ValueError("Redirection vers un autre domaine.")

                    current_url = target
                    continue

                if response.status_code != 200:
                    raise ValueError(f"Réponse HTTP {response.status_code}.")

                content_type = response.headers.get("Content-Type", "").lower()
                if "application/pdf" in content_type:
                    body = read_limited_response(response, decode=False)
                else:
                    body = read_limited_response(response)

                return current_url, content_type, body

            finally:
                if response is not None:
                    response.close()
                adapter.close()


def _has_exact_siret(text, siret):
    digits = re.sub(r"\D", "", str(siret or ""))
    if len(digits) != 14:
        return False

    # Autorise plusieurs espaces, points ou tirets entre les chiffres.
    normalized_text = re.sub(
        r"(?<=\d)[ \t.\-]+(?=\d)",
        "",
        text or "",
    )
    return re.search(
        r"(?<!\d)" + re.escape(digits) + r"(?!\d)",
        normalized_text,
    ) is not None


def _legal_notice_links(soup, base_url, expected_host):
    links = []

    for link in soup.find_all("a", href=True):
        target = urljoin(base_url, link["href"])
        parsed = urlsplit(target)

        if parsed.scheme != "https":
            continue

        if _normalized_host(parsed.hostname) != _normalized_host(expected_host):
            continue

        label = normalize(
            link.get_text(" ", strip=True) + " " + parsed.path
        )

        if any(word in label for word in (
            "mentions-legales", "mentions legales",
            "legal-notice", "legal notice", "impressum",
            "politique-legale",
        )):
            clean = urlunsplit((
                parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""
            ))
            if clean not in links:
                links.append(clean)

    return links[:3]



def _extract_pdf_evidence(body):
    """Extrait prudemment le texte d’un PDF utilisé comme preuve complémentaire."""
    if not isinstance(body, bytes):
        return ""
    if not body.startswith(b"%PDF-") or len(body) > 2 * 1024 * 1024:
        return ""

    try:
        reader = PdfReader(BytesIO(body), strict=False)
        if reader.is_encrypted:
            return ""

        extracted = []
        total = 0
        for page in reader.pages[:20]:
            remaining = 200000 - total
            if remaining <= 0:
                break
            page_text_value = page.extract_text() or ""
            page_text_value = page_text_value[:remaining]
            if page_text_value:
                extracted.append(page_text_value)
                total += len(page_text_value)

        return "\\n".join(extracted)
    except Exception:
        return ""


def _identity_evidence_links(soup, base_url, expected_host, limit=4):
    """Sélectionne des pages internes et documents institutionnels."""
    priorities = (
        "mentions legales", "mentions-legales", "legal notice",
        "bilan", "rapport annuel", "annual report", "rapport",
        "responsabilite", "societe", "company", "corporate",
        "about", "a propos", "qui sommes nous", "qui-sommes-nous",
        "groupe", "group", "event", "evenement", "actualite",
        "news", "press", "presse", "our company",
        "contract", "contrat", "cga", "conditions generales",
        "sous traitance", "supplier", "fournisseur",
    )

    candidates = []

    for link in soup.find_all("a", href=True):
        href = urljoin(base_url, link["href"])
        parsed = urlsplit(href)

        if parsed.scheme != "https":
            continue

        if not same_host(href, expected_host):
            continue

        path_lower = parsed.path.lower()
        is_pdf = path_lower.endswith(".pdf")

        if path_lower.endswith((
            ".jpg", ".jpeg", ".png", ".webp",
            ".zip", ".doc", ".docx", ".svg",
        )):
            continue

        label = normalize(
            link.get_text(" ", strip=True) + " " + parsed.path
        )

        # Un PDF n'est retenu que s'il est présenté comme document
        # institutionnel (bilan, rapport, mentions légales, société...).
        if is_pdf and not any(
            normalize(word) in label
            for word in (
                "mentions legales", "legal notice", "bilan",
                "rapport", "annual report", "responsabilite",
                "societe", "company", "identite",
                "contract", "contrat", "cga",
                "conditions generales", "sous traitance",
                "supplier", "fournisseur",
            )
        ):
            continue

        priority = next(
            (
                index for index, word in enumerate(priorities)
                if normalize(word) in label
            ),
            None,
        )

        if priority is None:
            continue

        clean_url = urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.query,
            "",
        ))

        if not any(item[1] == clean_url for item in candidates):
            candidates.append((priority, clean_url))

    candidates.sort(key=lambda item: item[0])

    return [url for _, url in candidates[:limit]]


def analyse_website(website, skills):
    original_url = safe_official_url(website)

    result = {
        "official_website": original_url or "",
        "activity_description": "",
        "technologies": [],
        "recruitment_url": "",
        "contact_url": "",
        "public_emails": [],
        "sources": [],
        "verification_status": "",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

    if not original_url:
        result["verification_status"] = "website_missing"
        return result

    host = urlsplit(original_url).hostname.casefold().rstrip(".")

    if not host_is_public(host):
        result["verification_status"] = "unreachable"
        return result

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    queue = [original_url]
    visited = set()
    collected_text = []
    page_count = 0
    errors = 0
    recruitment_candidates = set()

    try:
        while queue and page_count < MAX_PAGES:
            current_url = queue.pop(0)

            if current_url in visited:
                continue

            visited.add(current_url)

            try:
                final_url, content_type, html = _fetch_pinned_page(
                    current_url, host
                )

                if "text/html" not in content_type:
                    continue

                soup, title, description, text = page_text(html)

                page_count += 1
                collected_text.append(
                    f"{title} {description} {text}"
                )

                result["sources"].append(final_url)

                normalized_url = normalize(final_url)
                if (
                    not result["recruitment_url"]
                    and (
                        any(word in normalized_url for word in CAREER_WORDS)
                        or current_url in recruitment_candidates
                        or final_url in recruitment_candidates
                    )
                ):
                    result["recruitment_url"] = final_url

                if (
                    not result["contact_url"]
                    and any(word in normalized_url for word in CONTACT_WORDS)
                ):
                    result["contact_url"] = final_url

                if current_url == original_url:
                    explicit_links = explicit_recruitment_links(
                        soup, final_url, host
                    )
                    recruitment_candidates.update(explicit_links)

                    page_links = list(dict.fromkeys(
                        relevant_links(soup, final_url, host)
                        + explicit_links
                    ))

                    for candidate in page_links:
                        if candidate not in visited and candidate not in queue:
                            queue.append(candidate)

                # Les liens trouvés sur les pages secondaires ne sont
                # pas ajoutés à la file : nombre de pages strictement limité.

            except (requests.RequestException, ValueError, OSError):
                errors += 1

    finally:
        session.close()

    combined_text = " ".join(collected_text)
    normalized_text = normalize(combined_text)

    matched = []
    for skill in skills:
        needle = normalize(skill)
        if len(needle) < 2:
            continue

        pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
        if re.search(pattern, normalized_text):
            matched.append(skill)

    result["technologies"] = matched
    result["public_emails"] = extract_public_emails(combined_text)

    if combined_text:
        result["activity_description"] = combined_text[:2500]
        result["verification_status"] = (
            "enriched" if page_count else "limited_content"
        )
    else:
        result["verification_status"] = (
            "unreachable" if errors else "limited_content"
        )

    return result



IGNORED_DOMAINS = (
    "hellowork.com", "indeed.com", "francetravail.fr",
    "welcometothejungle.com", "linkedin.com", "facebook.com",
    "instagram.com", "twitter.com", "youtube.com",
    "societe.com", "pappers.fr", "verif.com",
    "infogreffe.fr", "pagesjaunes.fr", "manageo.fr",
    "annuaire-entreprises.data.gouv.fr", "data.gouv.fr",
    "metropole-rouen-normandie.fr",
    "kompass.com", "infonet.fr", "allbiz.fr",
    "lesentreprises.com", "bt-africa.com",
    "infopass.fr", "legl.fr",
    "lejournaldesentreprises.com",
)



def company_tokens(*names):
    """Construit des identifiants à partir des noms de l'entreprise."""
    ignored = {
        "sas", "sasu", "sarl", "eurl", "groupe",
        "france", "entreprise", "societe", "les", "des",
        "rouen", "amiens", "net", "up", "in",
    }

    result = []

    for name in names:
        words = re.findall(r"[a-z0-9]+", normalize(name))

        meaningful = [
            word for word in words
            if len(word) >= 3 and word not in ignored
        ]

        result.extend(meaningful)

        # Exemple : NET IN UP ROUEN devient NETINUP.
        compact_words = [
            word for word in words
            if word not in {
                "rouen", "amiens", "sas", "sasu",
                "sarl", "eurl", "france",
            }
        ]

        compact = "".join(compact_words)

        if len(compact) >= 5:
            result.append(compact)

    return list(dict.fromkeys(result))


def contains_identity(text, token):
    """Recherche un nom distinctif, y compris sous forme compacte."""
    normalized = normalize(text)
    normalized_token = normalize(token)

    if not normalized_token:
        return False

    pattern = (
        r"(?<![a-z0-9])"
        + re.escape(normalized_token)
        + r"(?![a-z0-9])"
    )

    if re.search(pattern, normalized):
        return True

    compact_text = re.sub(r"[^a-z0-9]", "", normalized)
    compact_token = re.sub(r"[^a-z0-9]", "", normalized_token)

    return (
        len(compact_token) >= 5
        and compact_token in compact_text
    )


def ignored_domain(host):
    host = (host or "").casefold().rstrip(".")

    return any(
        host == domain or host.endswith("." + domain)
        for domain in IGNORED_DOMAINS
    )




def _has_exact_siren(text, siren):
    """Recherche un SIREN exact, avec ou sans espaces ou séparateurs."""
    digits = re.sub(r"\D", "", str(siren or ""))

    if len(digits) != 9:
        return False

    separator = r"[\s.\-]*"
    pattern = (
        r"(?<!\d)"
        + separator.join(re.escape(char) for char in digits)
        + r"(?!\d)"
    )

    return re.search(pattern, str(text or "")) is not None


def verify_siret_in_public_registry(siret, locality):
    """Vérifie le SIRET exact, l'activité et la commune via l'API publique."""
    siret_digits = re.sub(r"\D", "", str(siret or ""))

    if len(siret_digits) != 14 or not locality:
        return False

    try:
        response = requests.get(
            "https://recherche-entreprises.api.gouv.fr/search",
            params={"q": siret_digits},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            return False

        expected_siren = siret_digits[:9]
        expected_locality = re.sub(
            r"[^a-z0-9]",
            "",
            normalize(locality),
        )

        for company_result in payload.get("results", []):
            if not isinstance(company_result, dict):
                continue

            result_siren = re.sub(
                r"\D",
                "",
                str(company_result.get("siren", "")),
            )

            if result_siren != expected_siren:
                continue

            establishments = company_result.get(
                "matching_etablissements",
                [],
            )

            for establishment in establishments:
                if not isinstance(establishment, dict):
                    continue

                establishment_siret = re.sub(
                    r"\D",
                    "",
                    str(establishment.get("siret", "")),
                )

                if establishment_siret != siret_digits:
                    continue

                if establishment.get("etat_administratif") != "A":
                    continue

                if establishment.get("date_fermeture"):
                    continue

                actual_locality = re.sub(
                    r"[^a-z0-9]",
                    "",
                    normalize(establishment.get("libelle_commune", "")),
                )

                if actual_locality != expected_locality:
                    continue

                return True

        return False

    except (
        requests.RequestException,
        ValueError,
        TypeError,
        KeyError,
    ):
        return False

def _evidence_url_priority(url):
    """Classe les preuves officielles avant leur téléchargement."""
    path = normalize(urlsplit(url).path)
    path = path.replace("-", " ").replace("_", " ")
    segments = [part.strip() for part in path.split("/") if part.strip()]

    legal_terms = (
        "mentions legales", "legal notice", "impressum",
        "politique legale",
    )
    contract_terms = (
        "contract", "contrat", "cga", "conditions generales",
        "sous traitance", "supplier", "fournisseur",
    )
    institutional_terms = (
        "identite", "societe", "corporate",
        "about", "rapport", "bilan", "annual report",
        "responsabilite",
    )

    if any(term in path for term in legal_terms):
        return 0
    if any(term in path for term in contract_terms):
        return 1
    if any(term in path for term in institutional_terms):
        return 2
    if "company" in segments:
        return 2
    return 3


def verify_company_website(
    url,
    company,
    legal_name="",
    brand="",
    siret="",
    locality="",
    evidence_urls=None,
):
    """Vérifie le site, les mentions légales et l'établissement public."""
    url = safe_official_url(url)

    if not url:
        return False

    host = (urlsplit(url).hostname or "").casefold().rstrip(".")

    if ignored_domain(host):
        return False

    tokens = company_tokens(company, legal_name, brand)
    if not tokens:
        return False

    try:
        homepage_url, content_type, html = _fetch_pinned_page(url, host)

        if "text/html" not in content_type:
            return False

        soup, title, description, text = page_text(html)
        page = title + " " + description + " " + text[:12000]
        identity = host + " " + title

        # Le registre confirme le SIRET exact et la commune.
        # Les mentions légales confirment le SIREN et l'identité du site.
        if siret:
            if not verify_siret_in_public_registry(siret, locality):
                return False

            expected_siren = re.sub(r"\D", "", str(siret))[:9]

            legal_urls = list(
                _legal_notice_links(soup, homepage_url, host)
            )

            # Les moteurs peuvent découvrir une page officielle contenant
            # les mentions légales, même si elle n'est pas liée depuis l'accueil.
            for evidence_url in evidence_urls or []:
                safe_url = safe_official_url(evidence_url)
                if not safe_url:
                    continue

                evidence_host = (
                    urlsplit(safe_url).hostname or ""
                ).casefold().rstrip(".")

                if (
                    _normalized_host(evidence_host) == _normalized_host(host)
                    and safe_url not in legal_urls
                ):
                    legal_urls.append(safe_url)

            # Ajoute d'abord les pages institutionnelles liées depuis l'accueil.
            internal_urls = _identity_evidence_links(
                soup,
                homepage_url,
                host,
                limit=MAX_PAGES - 1,
            )

            for internal_url in internal_urls:
                safe_url = safe_official_url(internal_url)
                if not safe_url:
                    continue

                internal_host = (
                    urlsplit(safe_url).hostname or ""
                ).casefold().rstrip(".")

                if (
                    _normalized_host(internal_host)
                    == _normalized_host(host)
                    and safe_url not in legal_urls
                ):
                    legal_urls.append(safe_url)

            # Priorise les preuves juridiques et contractuelles,
            # quelle que soit leur provenance (Bing ou liens internes).
            # L'accueil compte déjà : maximum MAX_PAGES - 1 téléchargements.
            legal_urls = sorted(
                dict.fromkeys(legal_urls),
                key=_evidence_url_priority,
            )
            visited_evidence = set()
            evidence_page_count = 0

            while (
                legal_urls
                and evidence_page_count < MAX_PAGES - 1
            ):
                legal_url = legal_urls.pop(0)

                if legal_url in visited_evidence:
                    continue

                visited_evidence.add(legal_url)

                try:
                    final_legal_url, legal_content_type, legal_content = (
                        _fetch_pinned_page(legal_url, host)
                    )
                    evidence_page_count += 1

                    if "application/pdf" in legal_content_type:
                        legal_text = _extract_pdf_evidence(legal_content)

                    elif "text/html" in legal_content_type:
                        legal_soup, _, _, legal_text = page_text(
                            legal_content
                        )

                        # Une page institutionnelle peut contenir
                        # un rapport PDF ou une autre preuve pertinente.
                        remaining_pages = (
                            MAX_PAGES - 1 - evidence_page_count
                        )

                        if remaining_pages > 0:
                            nested_urls = _identity_evidence_links(
                                legal_soup,
                                final_legal_url,
                                host,
                                limit=remaining_pages,
                            )

                            # Les liens découverts sont traités en priorité.
                            for nested_url in reversed(nested_urls):
                                safe_nested_url = safe_official_url(
                                    nested_url
                                )

                                if not safe_nested_url:
                                    continue

                                nested_host = (
                                    urlsplit(safe_nested_url).hostname or ""
                                ).casefold().rstrip(".")

                                if (
                                    _normalized_host(nested_host)
                                    == _normalized_host(host)
                                    and safe_nested_url
                                    not in visited_evidence
                                    and safe_nested_url not in legal_urls
                                ):
                                    legal_urls.insert(0, safe_nested_url)
                    else:
                        continue

                    if not legal_text:
                        continue

                    legal_identity = any(
                        contains_identity(legal_text, token)
                        for token in tokens
                    )

                    if (
                        _has_exact_siren(legal_text, expected_siren)
                        and legal_identity
                    ):
                        return True

                except (
                    requests.RequestException,
                    ValueError,
                    OSError,
                ):
                    continue

            return False

        # Sans SIRET, on conserve une vérification textuelle stricte.
        matched = sum(
            contains_identity(page, token)
            for token in tokens
        )
        identity_matched = any(
            contains_identity(identity, token)
            for token in tokens
        )

        required = min(2, len(tokens))
        return matched >= required and identity_matched

    except (requests.RequestException, ValueError, OSError):
        return False


def resolve_bing_result_url(value):
    """Décode une URL de résultat Bing, y compris les redirections /ck/a."""
    if not value:
        return ""

    value = urljoin("https://www.bing.com", str(value).strip())

    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").casefold()

        if host == "bing.com" or host.endswith(".bing.com"):
            params = parse_qs(parsed.query)
            encoded = params.get("u", [""])[0]

            if not encoded:
                return ""

            # Les URL Bing utilisent souvent le préfixe a1
            # suivi d'une chaîne encodée en Base64 URL-safe.
            if encoded.startswith("a1"):
                encoded = encoded[2:]

            padding = "=" * (-len(encoded) % 4)

            try:
                decoded = base64.urlsafe_b64decode(
                    encoded + padding
                ).decode("utf-8", errors="strict")
            except (ValueError, UnicodeDecodeError):
                return ""

            if decoded.startswith(("http://", "https://")):
                return decoded

            return ""

        return value if parsed.scheme in ("http", "https") else ""

    except (ValueError, TypeError):
        return ""





def _collect_bing_evidence_links(result_block, expected_host, search_pages):
    """Collecte les liens Bing du domaine officiel uniquement."""
    if not expected_host or ignored_domain(expected_host):
        return

    pages = search_pages.setdefault(expected_host, [])

    for anchor in result_block.select("a[href]"):
        resolved_url = resolve_bing_result_url(anchor.get("href", ""))
        safe_url = safe_official_url(resolved_url)

        if not safe_url:
            continue

        result_host = (
            urlsplit(safe_url).hostname or ""
        ).casefold().rstrip(".")

        if (
            _normalized_host(result_host) != _normalized_host(expected_host)
            or ignored_domain(result_host)
        ):
            continue

        if safe_url not in pages and len(pages) < 30:
            pages.append(safe_url)

def _add_search_candidate(
    candidates,
    raw_url,
    title,
    body,
    tokens,
    siret_digits,
    query,
    search_pages=None,
):
    """Ajoute un candidat et conserve les pages découvertes par domaine."""
    url = safe_official_url(raw_url)

    if not url:
        return

    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold().rstrip(".")

    if not host or ignored_domain(host):
        return

    if search_pages is not None:
        pages = search_pages.setdefault(host, [])
        if url not in pages:
            pages.append(url)

    title = normalize(title)
    body = normalize(body)

    host_matches = sum(
        contains_identity(host, token)
        for token in tokens
    )
    title_matches = sum(
        contains_identity(title, token)
        for token in tokens
    )
    body_matches = sum(
        contains_identity(body, token)
        for token in tokens
    )

    score = (
        host_matches * 4
        + title_matches * 3
        + body_matches
    )

    strong_identity = (
        any(contains_identity(host, token) for token in tokens)
        or any(contains_identity(title, token) for token in tokens)
    )

    siret_query = bool(
        len(siret_digits) == 14
        and siret_digits in re.sub(r"\D", "", query)
    )

    siret_in_result = bool(
        len(siret_digits) == 14
        and _has_exact_siret(
            title + " " + body,
            siret_digits,
        )
    )

    # Un SIRET seul ne prouve pas qu'un domaine appartient
    # à l'entreprise. Une identité forte reste obligatoire.
    if score >= 5 and strong_identity:
        if siret_query and siret_in_result:
            score = max(score, 10)

        candidates[url] = max(candidates.get(url, 0), score)


def discover_official_website(
    company,
    locality,
    legal_name="",
    brand="",
    siret="",
):
    """SearXNG prioritaire, Bing en secours, puis vérification stricte."""
    candidates = {}
    search_pages = {}
    successful_search = False

    tokens = company_tokens(company, legal_name, brand)

    if not tokens:
        return "", "site_not_found", ""

    siret_digits = re.sub(r"\D", "", str(siret or ""))

    names = list(dict.fromkeys(
        name.strip()
        for name in (brand, legal_name, company)
        if name and name.strip()
    ))

    queries = []

    for name in names:
        queries.extend([
            f'"{name}" "{locality}"',
            f'"{name}" site officiel',
        ])

    if len(siret_digits) == 14:
        siren_digits = siret_digits[:9]

        queries.extend([
            f'"{siret_digits}"',
            f'"{siret_digits}" "{brand or company}"',
            f'"{siren_digits}" "mentions légales"',
            f'"{siren_digits}" "{legal_name or company}"',
        ])

    queries = list(dict.fromkeys(queries))

    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }

    # 1. SearXNG : moteur prioritaire.
    if SEARXNG_URL:
        for query in queries:
            try:
                response = requests.get(
                    f"{SEARXNG_URL}/search",
                    params={
                        "q": query,
                        "format": "json",
                        "language": "fr-FR",
                    },
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()

                data = response.json()
                if not isinstance(data, dict):
                    raise ValueError("Réponse JSON SearXNG invalide.")

                results = data.get("results", [])
                if not isinstance(results, list):
                    raise ValueError("Liste de résultats SearXNG invalide.")

                successful_search = True

                print(
                    f"SearXNG : {len(results)} résultats "
                    f"pour {query!r}"
                )

                for result in results:
                    if not isinstance(result, dict):
                        continue

                    _add_search_candidate(
                        candidates,
                        result.get("url", ""),
                        result.get("title", ""),
                        result.get("content", ""),
                        tokens,
                        siret_digits,
                        query,
                        search_pages,
                    )

            except (requests.RequestException, ValueError) as error:
                print(
                    "SearXNG indisponible ou réponse invalide : "
                    f"{type(error).__name__}"
                )

    def verify_candidates():
        ranked = sorted(
            candidates.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        print(f"Sites candidats retenus : {len(ranked)}")

        for candidate, score in ranked[:5]:
            print(f"Vérification : {candidate} (score {score})")

            if verify_company_website(
                candidate,
                company,
                legal_name,
                brand,
                siret,
                locality=locality,
                evidence_urls=search_pages.get(
                    (urlsplit(candidate).hostname or "").casefold().rstrip("."),
                    [],
                ),
            ):
                return candidate

        return ""

    # Vérifier les candidats SearXNG avant de solliciter le secours.
    verified = verify_candidates()

    if verified:
        return verified, "website_verified", verified

    # 2. Bing : secours si SearXNG n'a pas permis de vérifier un site.
    for query in queries:
        try:
            response = requests.get(
                "https://www.bing.com/search",
                params={"q": query, "count": 10},
                headers={
                    **headers,
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64) "
                        "AppleWebKit/537.36 Chrome/130.0.0.0 "
                        "Safari/537.36"
                    ),
                },
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()
            successful_search = True

            soup = BeautifulSoup(response.text, "html.parser")
            result_blocks = soup.select("li.b_algo")

            print(
                f"Bing (secours) : {len(result_blocks)} résultats "
                f"pour {query!r}"
            )

            for block in result_blocks:
                heading = block.select_one("h2")
                link = heading.find("a", href=True) if heading else None

                if not link:
                    continue

                resolved_url = resolve_bing_result_url(
                    link.get("href", "")
                )

                snippet_element = (
                    block.select_one(".b_caption p")
                    or block.select_one(".b_snippet")
                )

                body = (
                    snippet_element.get_text(" ", strip=True)
                    if snippet_element else ""
                )

                _add_search_candidate(
                    candidates,
                    resolved_url,
                    heading.get_text(" ", strip=True),
                    body,
                    tokens,
                    siret_digits,
                    query,
                    search_pages,
                )

                primary_url = safe_official_url(resolved_url)
                if primary_url:
                    primary_host = (
                        urlsplit(primary_url).hostname or ""
                    ).casefold().rstrip(".")

                    _collect_bing_evidence_links(
                        block,
                        primary_host,
                        search_pages,
                    )

        except requests.RequestException as error:
            print(
                "Bing indisponible : "
                f"{type(error).__name__}"
            )

    # 3. Recherche documentaire ciblée par SIREN.
    if len(siret_digits) == 14:
        siren_digits = siret_digits[:9]
        formatted_siren = (
            f"{siren_digits[:3]} {siren_digits[3:6]} "
            f"{siren_digits[6:]}"
        )

        candidate_hosts = list(dict.fromkeys(
            (urlsplit(candidate).hostname or "").casefold().rstrip(".")
            for candidate in candidates
        ))

        for candidate_host in candidate_hosts[:5]:
            if not candidate_host or ignored_domain(candidate_host):
                continue

            targeted_queries = [
                f'site:{candidate_host} "{siren_digits}"',
                f'site:{candidate_host} "{formatted_siren}"',
                f'site:{candidate_host} filetype:pdf "{siren_digits}"',
                (
                    f'site:{candidate_host} '
                    f'("{brand or legal_name or company}") '
                    '(mentions légales OR legal notice OR contrat OR contract)'
                ),
            ]

            for targeted_query in targeted_queries:
                try:
                    response = requests.get(
                        "https://www.bing.com/search",
                        params={"q": targeted_query, "count": 10},
                        headers={
                            **headers,
                            "User-Agent": (
                                "Mozilla/5.0 (X11; Linux x86_64) "
                                "AppleWebKit/537.36 Chrome/130.0.0.0 "
                                "Safari/537.36"
                            ),
                        },
                        timeout=REQUEST_TIMEOUT,
                    )

                    response.raise_for_status()
                    successful_search = True

                    soup = BeautifulSoup(response.text, "html.parser")
                    result_blocks = soup.select("li.b_algo")

                    print(
                        f"Bing (preuves officielles) : "
                        f"{len(result_blocks)} résultats "
                        f"pour {targeted_query!r}"
                    )

                    for block in result_blocks:
                        _collect_bing_evidence_links(
                            block,
                            candidate_host,
                            search_pages,
                        )

                except (requests.RequestException, ValueError) as error:
                    print(
                        "Recherche documentaire Bing indisponible : "
                        f"{type(error).__name__}"
                    )

    if not successful_search:
        return "", "search_unavailable", ""

    verified = verify_candidates()

    if verified:
        return verified, "website_verified", verified

    ranked = sorted(
        candidates.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    if ranked:
        return "", "candidate_to_verify", ranked[0][0]

    return "", "site_not_found", ""

def save_enrichment(prospect_id, data):
    with connect() as conn:
        conn.execute("""
            INSERT INTO prospect_enrichments (
                prospect_id, official_website, activity_description,
                technologies, recruitment_url, contact_url,
                public_emails, sources, verification_status, checked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(prospect_id) DO UPDATE SET
                official_website = excluded.official_website,
                activity_description = excluded.activity_description,
                technologies = excluded.technologies,
                recruitment_url = excluded.recruitment_url,
                contact_url = excluded.contact_url,
                public_emails = excluded.public_emails,
                sources = excluded.sources,
                verification_status = excluded.verification_status,
                checked_at = excluded.checked_at
        """, (
            prospect_id,
            data["official_website"],
            data["activity_description"],
            json.dumps(data["technologies"], ensure_ascii=False),
            data["recruitment_url"],
            data["contact_url"],
            json.dumps(data["public_emails"], ensure_ascii=False),
            json.dumps(data["sources"], ensure_ascii=False),
            data["verification_status"],
            data["checked_at"],
        ))


def enrich_prospects(limit=None, dry_run=False, prospect_id=None):
    all_prospects = get_prospects()
    prospects = []

    if prospect_id is not None:
        all_prospects = [
            prospect for prospect in all_prospects
            if prospect["id"] == prospect_id
        ]

        if not all_prospects:
            print(f"Prospect #{prospect_id} introuvable.")
            return

    # Filtrage géographique avant application de --limit.
    for prospect in all_prospects:
        locality = find_target_locality(prospect["location"] or "")

        if locality:
            prospects.append((prospect, locality))

    total_in_zone = len(prospects)
    skipped = len(all_prospects) - total_in_zone

    if limit is not None:
        prospects = prospects[:limit]

    skills = load_profile_skills()

    if not prospects:
        print("Aucun prospect dans les zones autorisées.")
        return

    print(f"Prospects présents : {len(all_prospects)}")
    print(f"Prospects dans les zones autorisées : {total_in_zone}")
    print(f"Prospects ignorés hors zone : {skipped}")
    print(f"Prospects à analyser : {len(prospects)}")
    print(f"Compétences chargées : {len(skills)}")
    print("Mode : aperçu sans écriture" if dry_run else "Mode : enregistrement")
    print("\nAucune candidature ne sera envoyée.")

    for prospect, locality in prospects:
        website = prospect["website"] if "website" in prospect.keys() else ""
        discovery_status = ""
        candidate = ""

        print("\n" + "=" * 60)
        print(f"Entreprise : {prospect['company']}")
        print(f"Localité : {locality}")
        print(f"Adresse : {prospect['location']}")

        if not website:
            print("Recherche du site officiel...")

            website, discovery_status, candidate = (
                discover_official_website(
                    prospect["company"],
                    locality,
                    legal_name=prospect["legal_name"],
                    brand=prospect["brand"],
                    siret=prospect["siret"],
                )
            )

            if website:
                print(f"Site trouvé et vérifié : {website}")
            elif candidate:
                print(f"Candidat à vérifier manuellement : {candidate}")
            else:
                print("Aucun site officiel fiable trouvé.")

        else:
            print(f"Site fourni par LBA : {website}")

        if website:
            data = analyse_website(website, skills)

            if discovery_status == "website_verified":
                data["verification_status"] = "website_verified"
        else:
            data = analyse_website("", skills)
            data["verification_status"] = discovery_status

        if not data["recruitment_url"]:
            print("Recherche d'un portail de recrutement externe...")
            data["recruitment_url"] = discover_recruitment_url(
                prospect["company"],
                locality,
                legal_name=prospect["legal_name"],
                brand=prospect["brand"],
                official_website=data["official_website"],
            )

        print(f"État : {data['verification_status']}")
        print(f"Site officiel : {data['official_website'] or 'non confirmé'}")
        print(f"Technologies : {', '.join(data['technologies']) or 'aucune'}")
        print(f"Recrutement : {data['recruitment_url'] or 'non repéré'}")
        print(f"Contact : {data['contact_url'] or 'non repéré'}")
        print(f"E-mails publics : {', '.join(data['public_emails']) or 'aucun'}")
        print(f"Pages analysées : {len(data['sources'])}")

        if not dry_run:
            save_enrichment(prospect["id"], data)

    print("\n========== FIN DE L'ENRICHISSEMENT ==========")

    if dry_run:
        print("Aucun résultat enregistré.")
    else:
        print("Enrichissements enregistrés séparément des prospects.")

    print("Aucune candidature n'a été envoyée.")
