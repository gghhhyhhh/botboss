# BotBoss

BotBoss recherche des annonces d'alternance en développement et suit les candidatures. **Aucun e-mail n'est envoyé automatiquement.** Chaque annonce doit être vérifiée, approuvée, puis l'envoi demande une deuxième confirmation.

## 1. Installation sur Ubuntu

```bash
cd ~/botboss
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python main.py init
```

## 2. Analyser ton CV et ton portfolio

```bash
python main.py scan-profile
```

BotBoss consulte le CV et le portfolio configurés dans `config.py`, tente de récupérer le PDF du CV et extrait les compétences détectables. Vérifie `profile/profile.json` : une page dynamique ou un PDF protégé peut ne pas être analysé complètement.

## 3. Rechercher des annonces

```bash
python main.py search
```

La recherche utilise un moteur de recherche tiers (DDGS) avec des requêtes ciblant Rouen, la Normandie, Amiens, les Hauts-de-France et les offres à distance en France. Les résultats sont enregistrés dans `botboss.db`. Les doublons d'URL sont ignorés.

```bash
python main.py offers
```

Les résultats du moteur peuvent inclure des plateformes d'emploi. BotBoss indique le domaine source, mais il faut vérifier que l'annonce est bien publiée par l'entreprise qui recrute et que l'offre est toujours ouverte.

## 4. Approuver une annonce

Après avoir vérifié l'annonce et trouvé une adresse de recrutement publiée officiellement par l'entreprise :

```bash
python main.py approve 1 --company "Nom exact de l'entreprise" --email "recrutement@entreprise.fr"
```

Remplace `1`, le nom et l'adresse par les vraies informations. N'utilise pas une adresse devinée.

## 5. Configurer Gmail (facultatif)

Pour l'envoi SMTP, active la validation en deux étapes de ton compte Google puis crée un **mot de passe d'application** Google. Ne donne jamais ton mot de passe Gmail normal à BotBoss.

Ouvre `.env` et ajoute le mot de passe d'application :

```env
GMAIL_APP_PASSWORD=ton_mot_de_passe_application
```

Si le CV PDF n'a pas été téléchargé automatiquement, place-le dans `profile/cv.pdf`, ou configure son chemin dans `.env` :

```env
CV_PDF_PATH=/chemin/vers/mon-cv.pdf
```

## 6. Envoyer une candidature validée

```bash
python main.py send 1
```

BotBoss affiche le destinataire et le message, puis demande de taper exactement `ENVOYER`. Sans cette confirmation, aucun e-mail n'est envoyé.

## Commandes utiles

```bash
python main.py offers --status pending
python main.py offers --status approved
python main.py offers --status sent
python main.py reject 1
```

## Limites de cette première version

- La recherche web est une première version exploratoire : les moteurs peuvent changer, limiter ou bloquer les résultats.
- Le score est indicatif et repose sur les compétences repérées dans le titre et l'extrait de l'annonce.
- L'entreprise et le contact ne sont pas devinés : ils doivent être vérifiés manuellement.
- Le message est un brouillon personnalisé de base, à relire avant envoi.
- BotBoss ne contourne aucun formulaire, compte ou contrôle d'accès de site de recrutement.
- Le CV est joint en PDF ; le portfolio est inclus sous forme de lien dans le message.
