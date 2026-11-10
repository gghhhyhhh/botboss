import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from config import CV_URL, PORTFOLIO_URL, PROFILE_PATH, DOWNLOADS_DIR, REQUEST_TIMEOUT
from database import connect

SKILLS = {
    "Langages": ["JavaScript", "TypeScript", "Python", "Go", "Golang", "Rust",
                 "Dart", "C++", "Java", "PHP", "HTML", "CSS", "SQL"],
    "Frameworks et web": ["Flutter", "React", "Vue", "Angular", "Node.js", "Express",
                          "Symfony", "Laravel", "Django", "Flask", "Actix",
                          "Full Stack", "Front-end", "Back-end", "API REST"],
    "Bases de données": ["SQLite", "PostgreSQL", "MySQL", "MongoDB", "Redis"],
    "Outils": ["Git", "GitHub", "Docker", "Linux", "Ubuntu", "CMake", "VS Code",
               "CI/CD", "Scrum", "Agile"],
    "Autres": ["intelligence artificielle", "algorithmique", "sécurité", "IA"],
}

def fetch(url):
    response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
        "User-Agent": "BotBoss/1.0 (job-search assistant)"
    })
    response.raise_for_status()
    return response.text

def visible_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return " ".join(soup.stripped_strings), soup

def download_cv_pdf():
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    html = fetch(CV_URL)
    _, soup = visible_text(html)
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if ".pdf" in href.lower():
            pdf_url = urljoin(CV_URL, href)
            response = requests.get(pdf_url, timeout=REQUEST_TIMEOUT, headers={
                "User-Agent": "BotBoss/1.0"
            })
            response.raise_for_status()
            path = DOWNLOADS_DIR / "cv.pdf"
            path.write_bytes(response.content)
            return path
    return None

def extract_pdf(path):
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        print(f"Impossible de lire le PDF {path}: {exc}")
        return ""

def scan_profile():
    texts, visited = [], []
    for url in [CV_URL, PORTFOLIO_URL]:
        try:
            html = fetch(url)
            text, soup = visible_text(html)
            texts.append(text)
            visited.append(url)
            if url == CV_URL:
                for a in soup.find_all("a", href=True):
                    if ".pdf" in a["href"].lower():
                        pdf_url = urljoin(url, a["href"])
                        try:
                            r = requests.get(pdf_url, timeout=REQUEST_TIMEOUT)
                            r.raise_for_status()
                            pdf_path = DOWNLOADS_DIR / "cv.pdf"
                            DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
                            pdf_path.write_bytes(r.content)
                            texts.append(extract_pdf(pdf_path))
                        except Exception as exc:
                            print(f"PDF non récupéré : {exc}")
                        break
        except Exception as exc:
            print(f"Page ignorée ({url}) : {exc}")

    combined = "\n".join(texts)
    found = {}
    for category, terms in SKILLS.items():
        matches = []
        for term in terms:
            pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
            if re.search(pattern, combined, re.IGNORECASE):
                matches.append(term)
        if matches:
            found[category] = sorted(set(matches), key=str.lower)

    profile = {
        "cv_url": CV_URL,
        "portfolio_url": PORTFOLIO_URL,
        "skills": found,
        "all_skills": sorted({s for group in found.values() for s in group}, key=str.lower),
        "pages_visited": visited,
        "text_excerpt": combined[:5000],
    }
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    with connect() as conn:
        conn.execute("INSERT INTO profile_runs (skills_count, profile_file) VALUES (?, ?)",
                     (len(profile["all_skills"]), str(PROFILE_PATH)))
    print(f"Profil analysé : {len(profile['all_skills'])} compétences détectées.")
    print("Compétences :", ", ".join(profile["all_skills"]) or "aucune détectée")
    print(f"Fichier créé : {PROFILE_PATH}")
    return profile
