import ipaddress
import re
import socket
import sys
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from requests.utils import select_proxy
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.exceptions import (
    ConnectTimeoutError,
    NameResolutionError,
    NewConnectionError,
)
from urllib3.util import connection as urllib3_connection


EMAIL_PATTERN = re.compile("^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")

MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = (5, 10)
USER_AGENT = "BotBoss-ContactVerifier/1.0"


class PinnedHTTPSConnection(HTTPSConnection):
    """Connexion HTTPS forcée vers une IP préalablement validée."""

    def __init__(self, *args, pinned_ip, **kwargs):
        ip = ipaddress.ip_address(pinned_ip)

        if not ip.is_global:
            raise ValueError("Adresse IP non publique interdite.")

        self._pinned_ip = str(ip)
        super().__init__(*args, **kwargs)

    def _new_conn(self):
        """Ouvre le socket vers l'IP épinglée, sans nouvelle résolution DNS."""
        try:
            sock = urllib3_connection.create_connection(
                (self._pinned_ip, self.port),
                self.timeout,
                source_address=self.source_address,
                socket_options=self.socket_options,
            )
        except socket.gaierror as exc:
            raise NameResolutionError(self.host, self, exc) from exc
        except socket.timeout as exc:
            raise ConnectTimeoutError(
                self,
                f"Connexion vers {self.host} expirée.",
            ) from exc
        except OSError as exc:
            raise NewConnectionError(
                self,
                f"Connexion vers l'IP épinglée impossible : {exc}",
            ) from exc

        sys.audit("http.client.connect", self, self.host, self.port)
        return sock

    def connect(self):
        """Établit TLS puis mémorise l'IP réellement connectée."""
        super().connect()

        if self.sock is None:
            raise ValueError("Socket HTTPS indisponible après connexion.")

        peer_ip = ipaddress.ip_address(
            self.sock.getpeername()[0].split("%")[0]
        )

        if peer_ip != ipaddress.ip_address(self._pinned_ip):
            raise ValueError("L'IP distante ne correspond pas à l'IP épinglée.")

        if not peer_ip.is_global:
            raise ValueError("Adresse IP distante non publique.")

        self._peer_ip = str(peer_ip)

        callback = getattr(self, "_peer_ip_callback", None)
        if callback is not None:
            callback(self._peer_ip)


class PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    """Pool HTTPS utilisant exclusivement PinnedHTTPSConnection."""

    ConnectionCls = PinnedHTTPSConnection

    def __init__(self, *args, **kwargs):
        self._last_peer_ip = None
        super().__init__(*args, **kwargs)

    def _record_peer_ip(self, peer_ip):
        self._last_peer_ip = peer_ip

    def _new_conn(self):
        connection = super()._new_conn()
        connection._peer_ip_callback = self._record_peer_ip
        return connection


class PinnedHTTPSAdapter(HTTPAdapter):
    """Adaptateur compatible avec Requests 2.31 et les versions récentes."""

    def __init__(self, pinned_ip):
        self.pinned_ip = str(ipaddress.ip_address(pinned_ip))

        if not ipaddress.ip_address(self.pinned_ip).is_global:
            raise ValueError("Adresse IP non publique interdite.")

        self._pinned_pools = []
        super().__init__(
            max_retries=0,
            pool_connections=1,
            pool_maxsize=1,
        )

    def _get_pinned_connection(
        self, request, proxies=None, verify=True, cert=None
    ):
        # Aucun proxy : il pourrait modifier la destination réelle.
        if select_proxy(request.url, proxies):
            raise ValueError(
                "Les proxys sont interdits pour cette validation."
            )

        if verify is False:
            raise ValueError(
                "La vérification des certificats TLS ne peut pas être désactivée."
            )

        parsed = urlsplit(request.url)

        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Connexion HTTPS obligatoire.")

        if parsed.username or parsed.password:
            raise ValueError("Les identifiants dans une URL sont interdits.")

        if parsed.port not in (None, 443):
            raise ValueError("Seul le port HTTPS 443 est autorisé.")

        # Requests construit les paramètres TLS officiels :
        # CA personnalisée, certificat client et vérification du certificat.
        host_params, pool_kwargs = (
            self.build_connection_pool_key_attributes(
                request, verify, cert
            )
        )

        hostname = host_params["host"]
        port = host_params.get("port") or 443

        pool = PinnedHTTPSConnectionPool(
            host=hostname,
            port=port,
            pinned_ip=self.pinned_ip,
            maxsize=1,
            block=True,
            retries=self.max_retries,
            **pool_kwargs,
        )

        self._pinned_pools.append(pool)
        return pool

    # Requests 2.31.0 appelle cette méthode depuis HTTPAdapter.send().
    def get_connection(self, url, proxies=None):
        request = requests.Request("GET", url).prepare()
        return self._get_pinned_connection(
            request, proxies, verify=True, cert=None
        )

    # Requests 2.32+ utilise cette méthode depuis HTTPAdapter.send().
    def get_connection_with_tls_context(
        self,
        request,
        verify,
        proxies=None,
        cert=None,
    ):
        return self._get_pinned_connection(
            request, proxies, verify, cert
        )

    def close(self):
        for pool in self._pinned_pools:
            pool.close()

        self._pinned_pools.clear()
        super().close()


def valid_email_format(email):
    if not isinstance(email, str):
        return False

    # Refuse les caractères de contrôle, notamment CR, LF et TAB.
    if any(ord(char) < 32 or ord(char) == 127 for char in email):
        return False

    email = email.strip()

    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        return False

    if ".." in email:
        return False

    return True


def normalize_host(host):
    return (host or "").lower().rstrip(".").removeprefix("www.")


def validate_public_url(url):
    """Valide une URL HTTPS publique et son adresse DNS."""

    try:
        parsed = urlsplit(url)

        if parsed.scheme != "https":
            raise ValueError("Seules les URL HTTPS sont autorisées.")

        if not parsed.hostname:
            raise ValueError("Nom de domaine manquant.")

        if parsed.username or parsed.password:
            raise ValueError("Les identifiants intégrés dans une URL sont interdits.")

        if parsed.port not in (None, 443):
            raise ValueError("Seul le port HTTPS standard 443 est autorisé.")

        host = parsed.hostname.encode("idna").decode("ascii").lower()

        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("Les adresses IP directes sont interdites.")

        records = socket.getaddrinfo(
            host,
            443,
            type=socket.SOCK_STREAM,
        )

        if not records:
            raise ValueError("Aucune adresse DNS trouvée.")

        addresses = set()

        for record in records:
            address = record[4][0].split("%")[0]
            ip = ipaddress.ip_address(address)

            if not ip.is_global:
                raise ValueError(
                    "Adresse DNS non publique ou réservée : accès bloqué."
                )

            addresses.add(str(ip))

        if not addresses:
            raise ValueError("Aucune adresse IP publique valide.")

        return parsed, host, tuple(sorted(addresses))

    except ValueError:
        raise
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"Impossible de vérifier le domaine ou son DNS : {exc}"
        ) from exc


def is_official_domain(host, official_host):
    host = normalize_host(host)
    official_host = normalize_host(official_host)

    # Seul le domaine officiel exact est autorisé.
    # www est déjà normalisé par normalize_host().
    return host == official_host


def get_peer_ip(response):
    """Récupère l'IP vérifiée du socket ou celle mémorisée par le pool."""

    connection = getattr(response.raw, "_connection", None)
    sock = getattr(connection, "sock", None)

    if sock is not None:
        try:
            return sock.getpeername()[0].split("%")[0]
        except (AttributeError, OSError, IndexError):
            pass

    # urllib3 peut libérer immédiatement le socket sur une réponse
    # sans corps (par exemple une redirection 301 avec Content-Length: 0).
    # Le pool conserve alors l'IP relevée après l'établissement TLS.
    pool = getattr(response.raw, "_pool", None)
    peer_ip = getattr(pool, "_last_peer_ip", None)

    if peer_ip:
        return peer_ip

    raise ValueError(
        "Impossible de vérifier l'adresse IP réelle de la connexion."
    )


def read_limited_response(response, decode=True):
    """Lit une réponse HTTP limitée à MAX_RESPONSE_BYTES.

    Par défaut, renvoie du texte pour préserver les usages existants.
    Avec decode=False, renvoie les octets d'origine (notamment pour les PDF).
    """

    content_length = response.headers.get("Content-Length")

    if content_length is not None:
        try:
            declared_size = int(content_length)
        except (TypeError, ValueError) as exc:
            raise ValueError("En-tête Content-Length invalide.") from exc

        if declared_size < 0 or declared_size > MAX_RESPONSE_BYTES:
            raise ValueError("Page refusée : taille supérieure à 2 Mo.")

    content = bytearray()

    for chunk in response.iter_content(chunk_size=16384):
        if not chunk:
            continue

        content.extend(chunk)

        if len(content) > MAX_RESPONSE_BYTES:
            raise ValueError("Page refusée : taille supérieure à 2 Mo.")

    if not decode:
        return bytes(content)

    encoding = response.encoding or "utf-8"
    return content.decode(encoding, errors="replace")


def validate_email_source(email, source_url, official_website, verification_status):
    if not isinstance(email, str):
        raise ValueError("Adresse e-mail invalide.")

    email = email.strip()

    if not valid_email_format(email):
        raise ValueError("Adresse e-mail invalide.")

    if verification_status != "website_verified":
        raise ValueError(
            "Envoi bloqué : le site officiel de l'entreprise n'est pas vérifié."
        )

    try:
        official = urlsplit(official_website or "")
        official_port = official.port
    except (TypeError, ValueError) as exc:
        raise ValueError("Site officiel invalide.") from exc

    if (
        official.scheme != "https"
        or not official.hostname
        or official.username
        or official.password
        or official_port not in (None, 443)
    ):
        raise ValueError(
            "Le site officiel doit être une URL HTTPS valide."
        )

    official_host = normalize_host(official.hostname)

    try:
        current_url = source_url

        for redirect_count in range(MAX_REDIRECTS + 1):
            parsed, current_host, addresses = validate_public_url(current_url)

            if not is_official_domain(current_host, official_host):
                raise ValueError(
                    "Source refusée : elle ne dépend pas du domaine officiel."
                )

            # Une seule IP est choisie parmi les IP publiques validées.
            # Le socket sera forcé vers cette adresse, sans résolution DNS
            # supplémentaire au moment de la connexion.
            pinned_ip = addresses[0]

            with requests.Session() as session:
                session.trust_env = False
                adapter = PinnedHTTPSAdapter(pinned_ip)
                session.mount("https://", adapter)

                response = session.get(
                    current_url,
                    timeout=REQUEST_TIMEOUT,
                    headers={"User-Agent": USER_AGENT},
                    allow_redirects=False,
                    stream=True,
                    verify=True,
                )

                try:
                    peer_ip = ipaddress.ip_address(get_peer_ip(response))

                    if peer_ip != ipaddress.ip_address(pinned_ip):
                        raise ValueError(
                            "Connexion refusée : l'IP distante ne correspond "
                            "pas à l'IP épinglée."
                        )

                    if not peer_ip.is_global:
                        raise ValueError(
                            "Connexion refusée : le serveur utilise une IP "
                            "non publique."
                        )

                    status = response.status_code

                    if status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")

                        if not location:
                            raise ValueError("Redirection sans destination.")

                        if redirect_count >= MAX_REDIRECTS:
                            raise ValueError(
                                "Nombre maximal de redirections dépassé."
                            )

                        # La destination sera résolue et vérifiée au début
                        # de la prochaine itération, avant toute connexion.
                        current_url = urljoin(current_url, location)
                        continue

                    response.raise_for_status()

                    if status != 200:
                        raise ValueError(
                            f"Réponse HTTP inattendue : {status}."
                        )

                    content = read_limited_response(response)

                    if email.casefold() not in content.casefold():
                        raise ValueError(
                            "Adresse introuvable dans le contenu de la page source."
                        )

                    return True

                finally:
                    response.close()

    except ValueError:
        raise
    except requests.RequestException as exc:
        raise ValueError(
            f"Impossible de consulter la page source : {exc}"
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"Impossible de vérifier la source : {exc}"
        ) from exc

    raise ValueError("Validation de la source impossible.")
