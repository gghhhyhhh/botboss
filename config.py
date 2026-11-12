import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

PROJECT_NAME = "BotBoss"
SENDER_EMAIL = "heehicham2@gmail.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()

CV_URL = "https://gghhhyhhh.github.io/cv/"
PORTFOLIO_URL = "https://gghhhyhhh.github.io/portfolio-independant/index.html"
CV_PDF_PATH = os.getenv("CV_PDF_PATH", "").strip()

DB_PATH = BASE_DIR / "botboss.db"
PROFILE_PATH = BASE_DIR / "profile" / "profile.json"
DOWNLOADS_DIR = BASE_DIR / "profile"
REQUEST_TIMEOUT = 20
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8081").rstrip("/")

TARGET_LOCALITIES = [
    # Rouen et alentours
    "Rouen",
    "Bois-Guillaume",
    "Bihorel",
    "Mont-Saint-Aignan",
    "Déville-lès-Rouen",
    "Maromme",
    "Canteleu",
    "Petit-Quevilly",
    "Grand-Quevilly",
    "Sotteville-lès-Rouen",
    "Saint-Étienne-du-Rouvray",
    "Oissel",
    "Darnétal",
    "Bonsecours",
    "Le Mesnil-Esnard",
    "Franqueville-Saint-Pierre",
    "Isneauville",
    "Malaunay",
    "Le Houlme",
    "Barentin",
    "Elbeuf",
    "Boos",
    "Cléon",
    "Duclair",
    "Pont-de-l'Arche",

    # Amiens et alentours
    "Amiens",
    "Longueau",
    "Rivery",
    "Camon",
    "Saleux",
    "Salouël",
    "Dury",
    "Boves",
    "Villers-Bretonneux",
    "Corbie",
    "Ailly-sur-Somme",
    "Picquigny",
    "Flixecourt",
    "Moreuil",
    "Conty",
    "Albert",
    "Airaines",
]

SEARCH_AREAS = [
    "Rouen",
    "Amiens",
]

JOB_TERMS = [
    "alternance développeur web",
    "alternance développeur logiciel",
    "alternance développeur full stack",
    "alternance développeur front-end",
    "alternance développeur back-end",
    "alternance développeur application",
    "alternance informatique développeur",
]
EXCLUDED_TERMS = [
    "école", "centre de formation", "organisme de formation",
    "formation à distance", "inscription formation", "campus",
]
