import argparse
from database import (init_db, get_offers, get_offer, update_offer, is_published_offer, get_prospects, get_prospect, get_prospect_enrichment, create_application, get_application, get_applications)
from profile.analyzer import scan_profile
from search.jobs import search_jobs
from search.la_bonne_alternance import search_lba
from mail_sender import send_offer, send_spontaneous_application
from email_validation import validate_email_source
from search.enrichment import enrich_prospects, find_target_locality

def show_offers(status=None):
    rows = get_offers(status)
    if not rows:
        print("Aucune annonce enregistrée.")
        return
    for row in rows:
        print(f"\n#{row['id']} | score {row['score']}/100 | {row['status']}")
        print(f"{row['title']}")
        print(f"Entreprise : {row['company'] or 'à identifier'}")
        print(f"Lieu : {row['location']} | Source : {row['source_type']} ({row['source_domain']})")
        print(f"Compétences repérées : {row['matched_skills'] or 'aucune'}")
        print(f"URL : {row['url']}")


def show_prospects():
    rows = get_prospects()

    if not rows:
        print("Aucun prospect enregistré.")
        return

    print(f"\n========== PROSPECTS ({len(rows)}) ==========")

    for row in rows:
        print(f"\n#{row['id']} | score {row['score']}/100 | {row['status']}")
        print(f"Entreprise : {row['company'] or 'à identifier'}")
        print(f"Activité : {row['title']}")
        print(f"Lieu : {row['location']}")
        print(f"Compétences : {row['matched_skills'] or 'aucune'}")
        print(f"URL : {row['url']}")


def show_spontaneous(limit=None):
    prospects = get_prospects()
    selected = []

    for prospect in prospects:
        locality = find_target_locality(prospect["location"] or "")
        if locality:
            selected.append((prospect, locality))

    if limit is not None:
        selected = selected[:limit]

    if not selected:
        print("Aucune entreprise dans les zones autorisées.")
        return

    print(f"\n========== ENTREPRISES À CONTACTER ({len(selected)}) ==========")

    for prospect, locality in selected:
        enrichment = get_prospect_enrichment(prospect["id"])
        website = prospect["website"] or ""
        recruitment_url = ""
        contact_url = ""
        verification = "non analysé"
        emails = []

        if enrichment:
            website = enrichment["official_website"] or website
            recruitment_url = enrichment["recruitment_url"] or ""
            contact_url = enrichment["contact_url"] or ""
            verification = enrichment["verification_status"] or "non vérifié"

            import json
            try:
                emails = json.loads(enrichment["public_emails"] or "[]")
            except (ValueError, TypeError):
                emails = []

        print(f"\n#{prospect['id']} — {prospect['company']}")
        print(f"Localité : {locality}")
        print(f"Site : {website or 'non trouvé'}")
        print(f"Vérification du site : {verification}")
        print(f"Recrutement : {recruitment_url or 'non repéré'}")
        print(f"Contact : {contact_url or 'non repéré'}")
        print(f"E-mails publics : {', '.join(emails) or 'aucun'}")
        print("Attention : un e-mail public n'est pas automatiquement un e-mail de recrutement.")


def show_applications():
    applications = get_applications()

    if not applications:
        print("Aucune candidature spontanée préparée.")
        return

    print(f"\n========== CANDIDATURES SPONTANÉES ({len(applications)}) ==========")

    for application in applications:
        print(
            f"\n#{application['id']} | {application['status']} | "
            f"{application['company']}"
        )
        print(f"Canal : {application['channel']}")
        if application["channel"] == "portal":
            print(f"Portail : {application['portal_url']}")
        else:
            print(f"Destinataire : {application['recipient_email']}")
        print(f"Lieu : {application['location']}")
        print(f"Objet : {application['subject']}")


def diagnose_applications():
    applications = get_applications()

    print("\\n========== DIAGNOSTIC DES CANDIDATURES ==========")

    if not applications:
        print("Aucune candidature enregistrée.")
        return

    counts = {}
    for application in applications:
        status = application["status"]
        counts[status] = counts.get(status, 0) + 1

    print("\\nRépartition des statuts :")
    for status, count in sorted(counts.items()):
        print(f"- {status} : {count}")

    suspicious = [
        app for app in applications
        if app["status"] in ("sending", "delivery_unknown")
    ]

    print("\\n========== ENVOIS À EXAMINER ==========")

    if not suspicious:
        print("Aucune candidature en sending ou delivery_unknown.")
        return

    for app in suspicious:
        print(f"\\nCandidature #{app['id']} — {app['company']}")
        print(f"Statut : {app['status']}")
        print(f"Destinataire : {app['recipient_email']}")
        print(f"Créée le : {app['created_at']}")
        print(f"Date SMTP : {app['sent_at'] or 'non renseignée'}")

    print(
        "\\nATTENTION : ne relance pas ces envois automatiquement. "
        "Un statut delivery_unknown signifie que la transmission "
        "a pu avoir lieu."
    )


def main():
    parser = argparse.ArgumentParser(description="BotBoss - recherche et suivi de candidatures en alternance")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialiser la base SQLite")
    sub.add_parser("scan-profile", help="Analyser le CV et le portfolio")
    search = sub.add_parser("search", help="Rechercher des annonces")
    search.add_argument("--max-per-query", type=int, default=8)
    search.add_argument("--max-queries", type=int, default=None,
                        help="Limiter le nombre de requêtes")
    search.add_argument("--dry-run", action="store_true",
                        help="Afficher sans enregistrer")

    lba = sub.add_parser("lba-search", help="Rechercher avec La Bonne Alternance")
    lba.add_argument("--without-recruiters", action="store_true",
                     help="Ignorer les entreprises à potentiel")
    lba.add_argument("--dry-run", action="store_true",
                     help="Afficher sans enregistrer")
    enrich = sub.add_parser("enrich", help="Enrichir les informations des entreprises")
    enrich.add_argument("--limit", type=int, default=None,
                        help="Limiter le nombre de prospects analysés")
    enrich.add_argument("--dry-run", action="store_true",
                        help="Analyser sans enregistrer les résultats")

    spontaneous = sub.add_parser(
        "spontaneous",
        help="Afficher les entreprises pour une candidature spontanée"
    )
    spontaneous.add_argument("--limit", type=int, default=None)

    sub.add_parser(
        "applications",
        help="Afficher les candidatures spontanées préparées"
    )

    sub.add_parser(
        "diagnose-applications",
        help="Diagnostiquer les candidatures sans modifier la base"
    )

    prepare = sub.add_parser(
        "prepare-application",
        help="Préparer une candidature spontanée"
    )
    prepare.add_argument("id", type=int, help="Identifiant du prospect")
    prepare.add_argument(
        "--email",
        required=True,
        help="Adresse e-mail publiée par l'entreprise"
    )
    prepare.add_argument(
        "--source-url",
        required=True,
        help="URL HTTPS officielle où l'adresse est publiée"
    )

    portal_application = sub.add_parser(
        "prepare-portal-application",
        help="Préparer une candidature via le portail officiel CGI"
    )
    portal_application.add_argument("id", type=int)
    portal_application.add_argument(
        "--portal-url",
        default="https://www.cgi.com/france/fr-fr/recrute/amiens",
        help="URL officielle du portail de recrutement CGI"
    )
    portal_application.add_argument(
        "--dry-run",
        action="store_true",
        help="Afficher sans enregistrer le brouillon"
    )

    send_application = sub.add_parser(
        "send-application",
        help="Envoyer une candidature spontanée préparée"
    )
    send_application.add_argument("id", type=int)

    sub.add_parser("prospects", help="Afficher les entreprises à contacter")
    offers = sub.add_parser("offers", help="Afficher les annonces")
    offers.add_argument(
        "--status",
        choices=[
            "pending",
            "to_verify",
            "approved",
            "sending",
            "smtp_accepted",
            "delivery_unknown",
            "failed",
            "bounced",
            "sent",
            "rejected",
        ],
    )
    approve = sub.add_parser("approve", help="Valider une annonce et préparer son e-mail")
    approve.add_argument("id", type=int)
    approve.add_argument("--company", required=True, help="Nom exact de l'entreprise")
    approve.add_argument("--email", required=True, help="Adresse e-mail du recruteur vérifiée")
    reject = sub.add_parser("reject", help="Refuser une annonce")
    reject.add_argument("id", type=int)
    verify = sub.add_parser("verify", help="Vérifier une annonce mise de côté")
    verify.add_argument("id", type=int)
    send = sub.add_parser("send", help="Envoyer une candidature après validation")
    send.add_argument("id", type=int)

    args = parser.parse_args()

    is_dry_run = (
        (args.command in ("search", "lba-search", "enrich") and args.dry_run)
        or (args.command == "prepare-portal-application" and args.dry_run)
    )

    if not is_dry_run:
        init_db()

    if args.command == "init":
        print("Base BotBoss initialisée.")
    elif args.command == "scan-profile":
        scan_profile()
    elif args.command == "search":
        if args.max_per_query < 1:
            parser.error("--max-per-query doit être supérieur ou égal à 1")
        if args.max_queries is not None and args.max_queries < 1:
            parser.error("--max-queries doit être supérieur ou égal à 1")

        search_jobs(
            args.max_per_query,
            dry_run=args.dry_run,
            max_queries=args.max_queries,
        )
    elif args.command == "lba-search":
        search_lba(
            include_recruiters=not args.without_recruiters,
            dry_run=args.dry_run,
        )
    elif args.command == "enrich":
        if args.limit is not None and args.limit < 1:
            parser.error("--limit doit être supérieur ou égal à 1")

        enrich_prospects(
            limit=args.limit,
            dry_run=args.dry_run,
        )
    elif args.command == "offers":
        show_offers(args.status)
    elif args.command == "prospects":
        show_prospects()
    elif args.command == "spontaneous":
        if args.limit is not None and args.limit < 1:
            parser.error("--limit doit être supérieur ou égal à 1")
        show_spontaneous(args.limit)

    elif args.command == "applications":
        show_applications()

    elif args.command == "diagnose-applications":
        diagnose_applications()

    elif args.command == "prepare-application":
        prospect = get_prospect(args.id)

        if not prospect:
            raise SystemExit("Prospect introuvable.")

        locality = find_target_locality(prospect["location"] or "")
        if not locality:
            raise SystemExit(
                "Entreprise hors des zones autorisées (Rouen ou Amiens et alentours)."
            )

        email = args.email.strip()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise SystemExit("Adresse e-mail invalide.")

        enrichment = get_prospect_enrichment(args.id)
        website = prospect["website"] or ""
        recruitment_url = ""
        contact_url = ""
        verification = "non vérifié"

        if enrichment:
            website = enrichment["official_website"] or website
            recruitment_url = enrichment["recruitment_url"] or ""
            contact_url = enrichment["contact_url"] or ""
            verification = enrichment["verification_status"] or "non vérifié"

        try:
            validate_email_source(
                email,
                args.source_url,
                website,
                verification,
            )
        except ValueError as exc:
            raise SystemExit(str(exc))

        print("\n========== VÉRIFICATION AVANT PRÉPARATION ==========")
        print(f"Entreprise : {prospect['company']}")
        print(f"Localité : {locality}")
        print(f"Site : {website or 'non trouvé'}")
        print(f"État du site : {verification}")
        print(f"Recrutement : {recruitment_url or 'non repéré'}")
        print(f"Contact : {contact_url or 'non repéré'}")
        print(f"Adresse saisie : {email}")

        confirmation = input(
            "\nAs-tu vérifié l'entreprise et confirmé que cette adresse "
            "lui appartient ? (oui/non) "
        ).strip().lower()

        if confirmation != "oui":
            print("Préparation annulée.")
        else:
            company = prospect["company"] or "votre entreprise"
            subject = "Candidature spontanée – Alternance en développement web"

            message = f"""Bonjour,

Je suis actuellement en première année de développement web à l'école Zone01 de Rouen et je suis à la recherche d'une entreprise afin de poursuivre mon parcours dans le cadre d'une alternance.

Passionné par le développement informatique, je travaille sur différents projets qui me permettent de développer mes compétences en programmation, notamment en Go, Python, JavaScript, HTML, CSS et Rust.

Je m'intéresse particulièrement au développement web, à la création d'applications et à la conception de solutions informatiques. Je souhaite aujourd'hui mettre mes compétences en pratique au sein de votre entreprise, tout en continuant à apprendre auprès d'une équipe de développeurs.

Je me permets donc de vous contacter afin de savoir si {company} serait susceptible d'accueillir un alternant en développement web.

Vous trouverez ci-dessous mon CV et mon portfolio, qui vous permettront de découvrir mon parcours, mes compétences et les projets que j'ai réalisés.

Mon CV : https://gghhhyhhh.github.io/cv/
Mon portfolio : https://gghhhyhhh.github.io/portfolio-independant/index.html

Je reste à votre disposition pour échanger avec vous et vous remercie pour l'attention portée à ma candidature.

Cordialement,
"""

            application_id = create_application(
                args.id,
                company,
                email,
                subject,
                message,
                email_source_url=args.source_url,
            )

            print(f"\nBrouillon créé : candidature #{application_id}")
            print("Aucun e-mail n'a été envoyé.")
            print("Le CV PDF sera joint lors de l'envoi.")

    elif args.command == "prepare-portal-application":
        from urllib.parse import urlsplit

        prospect = get_prospect(args.id)
        if not prospect:
            raise SystemExit("Prospect introuvable.")

        company = prospect["company"] or ""
        if "cgi" not in company.lower():
            raise SystemExit(
                "Cette commande est réservée à un prospect CGI identifié."
            )

        locality = find_target_locality(prospect["location"] or "")
        if not locality:
            raise SystemExit(
                "Entreprise hors des zones autorisées (Rouen ou Amiens et alentours)."
            )

        portal_url = args.portal_url.strip()
        parsed = urlsplit(portal_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.cgi.com"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or not parsed.path.startswith("/france/fr-fr/recrute")
            or parsed.query
            or parsed.fragment
        ):
            raise SystemExit(
                "URL refusée : utilise le portail officiel de recrutement CGI."
            )

        subject = "Candidature spontanée – Alternance en développement informatique"
        message = f"""Bonjour,

Je suis actuellement une formation de développeur informatique à Zone01 Rouen et je recherche une entreprise pour effectuer mon parcours en alternance.

Au cours de ma formation et de mes projets, je développe des compétences en programmation et en conception d'applications, notamment avec Go, Python, JavaScript, HTML, CSS et Rust. Je m'intéresse au développement logiciel, au web et à la résolution de problèmes techniques.

Je souhaiterais pouvoir mettre ces compétences en pratique au sein de CGI, contribuer à des projets concrets et continuer à progresser auprès de vos équipes. Ma démarche concerne une candidature spontanée pour une alternance à Amiens ; je serais heureux d'échanger avec vos équipes recrutement afin de savoir si une opportunité correspondant à mon profil peut être envisagée.

CV : https://gghhhyhhh.github.io/cv/
Portfolio : https://gghhhyhhh.github.io/portfolio-independant/index.html
GitHub : https://github.com/gghhhyhhh

Je vous remercie pour l'attention portée à ma démarche et reste à votre disposition pour tout complément d'information.

Cordialement,
"""

        print("\n========== CANDIDATURE CGI VIA PORTAIL ==========")
        print(f"Entreprise : {company}")
        print(f"Localité : {locality}")
        print("Type : candidature spontanée, sans offre précise identifiée")
        print(f"Portail officiel : {portal_url}")
        print(f"Objet : {subject}")
        print("\nMESSAGE PROPOSÉ :\n")
        print(message)

        if args.dry_run:
            print("\nDRY-RUN : aucun brouillon enregistré, aucune donnée modifiée.")
        else:
            application_id = create_application(
                args.id,
                company,
                "",
                subject,
                message,
                channel="portal",
                portal_url=portal_url,
            )
            print(f"\nBrouillon portail créé : candidature #{application_id}")
            print("Aucun e-mail envoyé et aucune candidature déposée sur le portail.")

    elif args.command == "send-application":
        application = get_application(args.id)

        if not application:
            raise SystemExit("Candidature spontanée introuvable.")

        if application["channel"] != "email":
            raise SystemExit(
                "Envoi SMTP refusé : cette candidature doit passer par son portail officiel."
            )

        if application["status"] != "draft":
            raise SystemExit("Cette candidature n'est pas au statut draft.")

        print("\n========== APERÇU DE LA CANDIDATURE ==========")
        print(f"Entreprise : {application['company']}")
        print(f"Destinataire : {application['recipient_email']}")
        print(f"Objet : {application['subject']}")
        print(f"Pièce jointe : CV PDF")
        print("\nMESSAGE :\n")
        print(application["message"])

        confirm = input(
            "\nPour envoyer réellement cet e-mail avec le CV PDF, "
            "tape exactement ENVOYER : "
        )

        if confirm == "ENVOYER":
            send_spontaneous_application(args.id)
        else:
            print("Envoi annulé. Le brouillon est conservé.")

    elif args.command == "verify":
        from urllib.parse import urlsplit

        offer = get_offer(args.id)
        if not offer:
            raise SystemExit("Annonce introuvable.")

        if offer["status"] != "to_verify":
            raise SystemExit("Cette annonce n'est pas dans le statut to_verify.")

        parsed_url = urlsplit(offer["url"])
        if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
            raise SystemExit("Vérification refusée : le lien externe n'est pas valide.")

        print("\\nVÉRIFICATION MANUELLE DE L'ANNONCE")
        print(f"Titre : {offer['title']}")
        print(f"Entreprise : {offer['company'] or 'Non renseignée'}")
        print(f"Lien : {offer['url']}")

        questions = [
            "Le lien ouvre-t-il une annonce individuelle (et non une page de recherche) ?",
            "L'entreprise et le poste correspondent-ils bien à l'annonce ?",
            "S'agit-il bien d'une alternance dans le développement informatique ?",
        ]

        for question in questions:
            answer = input(f"{question} (oui/non) ").strip().lower()
            if answer != "oui":
                print("Vérification annulée. L'annonce reste à vérifier.")
                break
        else:
            update_offer(args.id, human_verified=1, status="pending")
            print("Annonce vérifiée manuellement : elle est maintenant en attente de validation.")

    elif args.command == "approve":
        offer = get_offer(args.id)
        if not offer:
            raise SystemExit("Annonce introuvable.")

        if not is_published_offer(offer):
            raise SystemExit(
                "Action bloquée : cette entrée n'est pas une offre "
                "d'alternance publiée avec un lien externe vérifiable."
            )
        print("\nMessage qui sera envoyé :\n")
        print(offer["tailored_message"].replace("votre entreprise", args.company))
        print("\nCette commande prépare la validation. Aucun e-mail n'est envoyé.")
        confirmation = input("Confirmer la validation de cette annonce ? (oui/non) ").strip().lower()
        if confirmation == "oui":
            message = offer["tailored_message"].replace("votre entreprise", args.company)
            update_offer(args.id, company=args.company, contact_email=args.email,
                         tailored_message=message, status="approved")
            print("Annonce approuvée. L'envoi reste une action séparée.")
        else:
            print("Validation annulée.")
    elif args.command == "reject":
        update_offer(args.id, status="rejected")
        print("Annonce refusée.")
    elif args.command == "send":
        offer = get_offer(args.id)
        if not offer:
            raise SystemExit("Annonce introuvable.")

        if not is_published_offer(offer):
            raise SystemExit(
                "Action bloquée : cette entrée n'est pas une offre "
                "d'alternance publiée avec un lien externe vérifiable."
            )
        print(f"DESTINATAIRE : {offer['contact_email']}")
        print(f"OBJET : Candidature en alternance – {offer['title']}")
        print("\nMESSAGE :\n" + offer["tailored_message"])
        confirm = input("\nEnvoyer cet e-mail avec le CV PDF en pièce jointe ? Tape exactement ENVOYER : ")
        if confirm == "ENVOYER":
            send_offer(args.id)
        else:
            print("Envoi annulé.")

if __name__ == "__main__":
    main()
