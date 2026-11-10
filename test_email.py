import mimetypes
import smtplib
from email.message import EmailMessage

from config import SENDER_EMAIL, SMTP_HOST, SMTP_PORT, SMTP_APP_PASSWORD
from mail_sender import find_cv

DESTINATAIRE = "heehicham2@gmail.com"

cv = find_cv()

if not SMTP_APP_PASSWORD:
    raise SystemExit("Mot de passe Gmail manquant.")

if not cv:
    raise SystemExit("CV PDF introuvable.")

msg = EmailMessage()
msg["Subject"] = "Test BotBoss – vérification de l'envoi"
msg["From"] = SENDER_EMAIL
msg["To"] = DESTINATAIRE

msg.set_content(
    "Bonjour,\n\n"
    "Ceci est un véritable e-mail de test envoyé par BotBoss.\n"
    "Le serveur SMTP Gmail et la pièce jointe PDF sont testés.\n\n"
    "Aucune candidature n'a été envoyée à une entreprise.\n\n"
    "BotBoss"
)

content_type, _ = mimetypes.guess_type(str(cv))
maintype, subtype = (content_type or "application/pdf").split("/", 1)

msg.add_attachment(
    cv.read_bytes(),
    maintype=maintype,
    subtype=subtype,
    filename=cv.name
)

print("Destinataire :", DESTINATAIRE)
print("CV :", cv.name)

confirmation = input("Tape ENVOYER puis Entrée pour confirmer : ")

if confirmation != "ENVOYER":
    raise SystemExit("Envoi annulé.")

with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
    smtp.starttls()
    smtp.login(SENDER_EMAIL, SMTP_APP_PASSWORD)
    smtp.send_message(msg)

print("SUCCÈS : e-mail de test envoyé.")
