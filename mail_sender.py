import mimetypes
import smtplib
from email.message import EmailMessage
from pathlib import Path

from config import (
    SENDER_EMAIL, SMTP_HOST, SMTP_PORT, SMTP_APP_PASSWORD,
    CV_PDF_PATH, DOWNLOADS_DIR, CV_URL, PORTFOLIO_URL
)
from database import (
    get_offer,
    update_offer,
    is_published_offer,
    is_email_blocked,
    transition_offer_status,
)
from email_validation import validate_email_source, valid_email_format

def find_cv():
    candidates = []
    if CV_PDF_PATH:
        candidates.append(Path(CV_PDF_PATH).expanduser())
    candidates.extend([DOWNLOADS_DIR / "cv.pdf", Path("CV.pdf"), Path("cv.pdf")])
    return next((p for p in candidates if p.is_file()), None)

def send_offer(offer_id):
    offer = get_offer(offer_id)

    if not offer:
        raise ValueError("Annonce introuvable.")

    if offer["status"] != "approved":
        raise ValueError("L'annonce doit être approuvée avant l'envoi.")

    if not is_published_offer(offer):
        raise ValueError(
            "Envoi bloqué : cette entrée n'est pas une offre "
            "d'alternance publiée avec un lien externe vérifiable."
        )

    recipient = (offer["contact_email"] or "").strip()

    if not recipient:
        raise ValueError("Ajoute d'abord l'adresse e-mail du recruteur.")

    if not valid_email_format(recipient):
        raise ValueError("Format de l'adresse e-mail invalide.")

    if is_email_blocked(recipient):
        raise ValueError(
            "ENVOI BLOQUÉ : cette adresse est enregistrée comme invalide."
        )

    if not SMTP_APP_PASSWORD:
        raise ValueError("GMAIL_APP_PASSWORD manque dans le fichier .env.")

    cv_path = find_cv()
    if not cv_path:
        raise ValueError(
            "CV PDF introuvable. Lance d'abord scan-profile "
            "ou configure CV_PDF_PATH."
        )

    msg = EmailMessage()
    msg["Subject"] = f"Candidature en alternance – {offer['title']}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = recipient

    body = (offer["tailored_message"] or "").rstrip()
    extra_links = []

    if CV_URL and CV_URL not in body:
        extra_links.append("Mon CV en ligne : " + CV_URL)

    if PORTFOLIO_URL and PORTFOLIO_URL not in body:
        extra_links.append("Mon portfolio : " + PORTFOLIO_URL)

    if extra_links:
        body += "\n\n" + "\n".join(extra_links)

    msg.set_content(body)

    content_type, _ = mimetypes.guess_type(str(cv_path))
    maintype, subtype = (
        (content_type or "application/pdf").split("/", 1)
    )

    msg.add_attachment(
        cv_path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=cv_path.name,
    )

    # Réservation atomique : une seule tentative peut passer approved -> sending.
    reserved = transition_offer_status(
        offer_id, "approved", "sending"
    )

    if not reserved:
        raise ValueError(
            "Envoi refusé : l'annonce n'est plus disponible "
            "au statut approved. Vérifie son statut avant toute nouvelle action."
        )

    transmission_started = False
    accepted_recorded = False

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(SENDER_EMAIL, SMTP_APP_PASSWORD)

            # Après ce point, une erreur ne permet plus de savoir
            # avec certitude si le serveur a reçu le message.
            transmission_started = True
            smtp.send_message(msg)

            if not transition_offer_status(
                offer_id, "sending", "smtp_accepted"
            ):
                raise RuntimeError(
                    "Le serveur SMTP a accepté le message, mais "
                    "le statut n'a pas pu être confirmé en base."
                )

            accepted_recorded = True

    except Exception as exc:
        # Le message a été accepté : une erreur de fermeture SMTP
        # ne doit surtout pas provoquer une nouvelle tentative.
        if accepted_recorded:
            print(
                "Le serveur SMTP a accepté le message. "
                "Une erreur est survenue après cette acceptation : "
                f"{exc}"
            )
            return

        target_status = (
            "delivery_unknown" if transmission_started else "failed"
        )

        try:
            updated = transition_offer_status(
                offer_id, "sending", target_status
            )
        except Exception as db_error:
            raise RuntimeError(
                "Erreur SMTP et impossible d'enregistrer le résultat "
                f"en base : {db_error}. Le statut peut rester sending. "
                "Ne relance pas automatiquement cet envoi."
            ) from exc

        if not updated:
            raise RuntimeError(
                "Impossible de mettre à jour le statut de l'annonce. "
                "Vérifie la base avant toute nouvelle tentative."
            ) from exc

        if transmission_started:
            raise RuntimeError(
                "Résultat SMTP incertain. Le statut est delivery_unknown. "
                "Ne relance pas automatiquement cet envoi."
            ) from exc

        raise RuntimeError(
            "Échec avant la transmission SMTP. "
            "Le statut est failed."
        ) from exc

    print(
        f"Le serveur SMTP a accepté le message destiné à {recipient} "
        f"pour l'annonce #{offer_id}. Cela ne garantit pas sa livraison."
    )


def send_spontaneous_application(application_id):
    from database import (
        get_application,
        transition_application_status,
    )

    application = get_application(application_id)

    if not application:
        raise ValueError("Candidature spontanée introuvable.")

    channel = application["channel"] if "channel" in application.keys() else "email"
    if channel != "email":
        raise ValueError(
            "Envoi SMTP refusé : cette candidature utilise un portail externe."
        )

    if application["status"] != "draft":
        raise ValueError("La candidature doit être au statut draft.")

    if not application["recipient_email"]:
        raise ValueError("Aucune adresse e-mail de destinataire.")

    if is_email_blocked(application["recipient_email"]):
        raise ValueError(
            "ENVOI BLOQUÉ : cette adresse est enregistrée comme invalide."
        )

    validate_email_source(
        application["recipient_email"],
        application["email_source_url"],
        application["official_website"],
        application["verification_status"],
    )

    if not SMTP_APP_PASSWORD:
        raise ValueError("GMAIL_APP_PASSWORD manque dans le fichier .env.")

    cv_path = find_cv()
    if not cv_path:
        raise ValueError(
            "CV PDF introuvable. Vérifie profile/cv.pdf ou CV_PDF_PATH."
        )

    msg = EmailMessage()
    msg["Subject"] = application["subject"]
    msg["From"] = SENDER_EMAIL
    msg["To"] = application["recipient_email"]

    body = application["message"].rstrip()
    extra_links = []

    if CV_URL and CV_URL not in body:
        extra_links.append("Mon CV en ligne : " + CV_URL)

    if PORTFOLIO_URL and PORTFOLIO_URL not in body:
        extra_links.append("Mon portfolio : " + PORTFOLIO_URL)

    if extra_links:
        body += "\n\n" + "\n".join(extra_links)

    msg.set_content(body)

    content_type, _ = mimetypes.guess_type(str(cv_path))
    maintype, subtype = (
        (content_type or "application/pdf").split("/", 1)
    )

    msg.add_attachment(
        cv_path.read_bytes(),
        maintype=maintype,
        subtype=subtype,
        filename=cv_path.name,
    )

    # Réservation atomique : un seul processus peut commencer l'envoi.
    reserved = transition_application_status(
        application_id, "draft", "sending"
    )

    if not reserved:
        raise ValueError(
            "Envoi refusé : la candidature n'est plus disponible "
            "au statut draft."
        )

    transmission_started = False
    accepted_recorded = False

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(SENDER_EMAIL, SMTP_APP_PASSWORD)

            # À partir de cet instant, le résultat peut devenir incertain.
            transmission_started = True
            smtp.send_message(msg)

            if not transition_application_status(
                application_id, "sending", "smtp_accepted"
            ):
                raise RuntimeError(
                    "Le serveur SMTP a accepté le message, mais "
                    "le statut n'a pas pu être confirmé en base."
                )

            accepted_recorded = True

    except Exception as exc:
        if accepted_recorded:
            print(
                "Le serveur SMTP a accepté le message. "
                "Une erreur est survenue pendant la fermeture "
                "de la connexion ; aucun nouvel envoi ne sera effectué."
            )
            return

        target_status = (
            "delivery_unknown"
            if transmission_started
            else "failed"
        )

        try:
            transition_application_status(
                application_id, "sending", target_status
            )
        except Exception as db_error:
            raise RuntimeError(
                "Erreur SMTP et impossible d'enregistrer le résultat. "
                "La candidature reste protégée : vérifie son statut "
                "avant toute intervention."
            ) from db_error

        if transmission_started:
            raise RuntimeError(
                "Résultat SMTP incertain. Statut delivery_unknown : "
                "ne relance pas cette candidature sans vérification."
            ) from exc

        raise RuntimeError(
            "Échec avant transmission SMTP. "
            "La candidature est enregistrée en failed."
        ) from exc

    print(
        f"Serveur SMTP ayant accepté la candidature pour "
        f"{application['recipient_email']}."
    )
