import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

import search.enrichment as enrichment


class TestContainsIdentity(unittest.TestCase):

    def test_uppercase_company_name_is_accepted(self):
        self.assertTrue(
            enrichment.contains_identity(
                "Document officiel CGI France SAS",
                "CGI",
            )
        )

    def test_company_name_is_case_insensitive(self):
        self.assertTrue(
            enrichment.contains_identity(
                "Document officiel CGI France SAS",
                "cgi",
            )
        )

    def test_unrelated_company_name_is_rejected(self):
        self.assertFalse(
            enrichment.contains_identity(
                "Document officiel d'une autre société",
                "CGI",
            )
        )


class TestSiretValidation(unittest.TestCase):

    def test_exact_siret_is_accepted(self):
        self.assertTrue(
            enrichment._has_exact_siret(
                "SIRET : 80142803800047",
                "80142803800047",
            )
        )

    def test_formatted_siret_is_accepted(self):
        self.assertTrue(
            enrichment._has_exact_siret(
                "SIRET : 801 428 038 00047",
                "80142803800047",
            )
        )

    def test_multiple_spaces_between_siret_groups_are_accepted(self):
        self.assertTrue(
            enrichment._has_exact_siret(
                "SIRET : 702042755  00307",
                "70204275500307",
            )
        )

    def test_multiple_allowed_separators_are_accepted(self):
        self.assertTrue(
            enrichment._has_exact_siret(
                "SIRET : 702.042.755- 00307",
                "70204275500307",
            )
        )

    def test_different_siret_is_rejected(self):
        self.assertFalse(
            enrichment._has_exact_siret(
                "SIRET : 80142803800048",
                "80142803800047",
            )
        )

    def test_partial_siret_is_rejected(self):
        self.assertFalse(
            enrichment._has_exact_siret(
                "SIRET : 801428038000",
                "80142803800047",
            )
        )

    def test_invalid_siret_length_is_rejected(self):
        self.assertFalse(
            enrichment._has_exact_siret(
                "80142803800047",
                "801428038",
            )
        )


class TestLegalNoticeLinks(unittest.TestCase):

    def test_same_domain_https_legal_link_is_accepted(self):
        soup = BeautifulSoup(
            """
            <a href="/mentions-legales">Mentions légales</a>
            """,
            "html.parser",
        )

        links = enrichment._legal_notice_links(
            soup,
            "https://example.com/",
            "example.com",
        )

        self.assertEqual(
            links,
            ["https://example.com/mentions-legales"],
        )

    def test_external_domain_is_rejected(self):
        soup = BeautifulSoup(
            """
            <a href="https://evil.example/mentions-legales">
                Mentions légales
            </a>
            """,
            "html.parser",
        )

        links = enrichment._legal_notice_links(
            soup,
            "https://example.com/",
            "example.com",
        )

        self.assertEqual(links, [])

    def test_http_legal_link_is_rejected(self):
        soup = BeautifulSoup(
            """
            <a href="http://example.com/mentions-legales">
                Mentions légales
            </a>
            """,
            "html.parser",
        )

        links = enrichment._legal_notice_links(
            soup,
            "https://example.com/",
            "example.com",
        )

        self.assertEqual(links, [])

    def test_www_and_root_domain_are_equivalent(self):
        soup = BeautifulSoup(
            """
            <a href="https://www.example.com/mentions-legales">
                Mentions légales
            </a>
            """,
            "html.parser",
        )

        links = enrichment._legal_notice_links(
            soup,
            "https://example.com/",
            "example.com",
        )

        self.assertEqual(
            links,
            ["https://www.example.com/mentions-legales"],
        )


class FakeResponse:

    def __init__(
        self,
        status_code=200,
        headers=None,
        is_redirect=False,
        is_permanent_redirect=False,
    ):
        self.status_code = status_code
        self.headers = headers or {
            "Content-Type": "text/html; charset=utf-8"
        }
        self.is_redirect = is_redirect
        self.is_permanent_redirect = is_permanent_redirect
        self.closed = False

    def close(self):
        self.closed = True


class FakeSession:

    def __init__(self, response):
        self.response = response
        self.headers = {}
        self.trust_env = True
        self.mounted = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def mount(self, prefix, adapter):
        self.mounted[prefix] = adapter

    def get(self, *args, **kwargs):
        return self.response


class TestPinnedHTTPSDownload(unittest.TestCase):

    def setUp(self):
        self.url = "https://example.com/"
        self.response = FakeResponse()
        self.session = FakeSession(self.response)

    @patch("search.enrichment.read_limited_response",
           return_value="<html>OK</html>")
    @patch("search.enrichment.get_peer_ip",
           return_value="93.184.216.34")
    @patch("search.enrichment.PinnedHTTPSAdapter")
    @patch("search.enrichment.requests.Session")
    @patch(
        "search.enrichment.validate_public_url",
        return_value=(
            urlsplit("https://example.com/"),
            "example.com",
            ("93.184.216.34",),
        ),
    )
    def test_public_pinned_download_succeeds(
        self,
        mock_validation,
        mock_session,
        mock_adapter,
        mock_peer,
        mock_reader,
    ):
        mock_session.return_value = self.session

        final_url, content_type, body = (
            enrichment._fetch_pinned_page(
                self.url,
                "example.com",
            )
        )

        self.assertEqual(final_url, self.url)
        self.assertIn("text/html", content_type)
        self.assertEqual(body, "<html>OK</html>")

        mock_adapter.assert_called_once_with("93.184.216.34")
        mock_reader.assert_called_once_with(self.response)
        self.assertFalse(self.session.trust_env)
        self.assertTrue(self.response.closed)

    @patch("search.enrichment.get_peer_ip",
           return_value="1.1.1.1")
    @patch("search.enrichment.PinnedHTTPSAdapter")
    @patch("search.enrichment.requests.Session")
    @patch(
        "search.enrichment.validate_public_url",
        return_value=(
            urlsplit("https://example.com/"),
            "example.com",
            ("93.184.216.34",),
        ),
    )
    def test_unexpected_peer_ip_is_rejected(
        self,
        mock_validation,
        mock_session,
        mock_adapter,
        mock_peer,
    ):
        mock_session.return_value = self.session

        with self.assertRaises(ValueError):
            enrichment._fetch_pinned_page(
                self.url,
                "example.com",
            )

        self.assertTrue(self.response.closed)

    @patch("search.enrichment.get_peer_ip",
           return_value="93.184.216.34")
    @patch("search.enrichment.safe_official_url",
           return_value="https://evil.example/steal")
    @patch("search.enrichment.PinnedHTTPSAdapter")
    @patch("search.enrichment.requests.Session")
    @patch(
        "search.enrichment.validate_public_url",
        return_value=(
            urlsplit("https://example.com/"),
            "example.com",
            ("93.184.216.34",),
        ),
    )
    def test_external_redirect_is_rejected(
        self,
        mock_validation,
        mock_session,
        mock_adapter,
        mock_safe_url,
        mock_peer,
    ):
        self.response = FakeResponse(
            status_code=302,
            headers={
                "Location": "https://evil.example/steal"
            },
            is_redirect=True,
        )
        self.session = FakeSession(self.response)
        mock_session.return_value = self.session

        with self.assertRaises(ValueError):
            enrichment._fetch_pinned_page(
                self.url,
                "example.com",
            )

        self.assertTrue(self.response.closed)


class TestWebsiteAnalysisErrors(unittest.TestCase):

    @patch("search.enrichment.requests.Session")
    @patch("search.enrichment._fetch_pinned_page",
           side_effect=ValueError("URL refusée"))
    @patch("search.enrichment.host_is_public",
           return_value=True)
    @patch("search.enrichment.safe_official_url",
           return_value="https://example.com/")
    def test_validation_error_does_not_crash_analysis(
        self,
        mock_safe_url,
        mock_public_host,
        mock_fetch,
        mock_session,
    ):
        result = enrichment.analyse_website(
            "https://example.com/",
            ["Python", "Go"],
        )

        self.assertEqual(
            result["verification_status"],
            "unreachable",
        )
        self.assertEqual(result["public_emails"], [])
        mock_fetch.assert_called_once()
        mock_session.return_value.close.assert_called_once()


class TestWebsiteIdentity(unittest.TestCase):

    @patch("search.enrichment.verify_siret_in_public_registry",
           return_value=True)
    @patch("search.enrichment._has_exact_siren",
           return_value=True)
    @patch("search.enrichment._legal_notice_links",
           return_value=[
               "https://example.com/mentions-legales"
           ])
    @patch("search.enrichment.page_text")
    @patch("search.enrichment._fetch_pinned_page")
    @patch("search.enrichment.company_tokens",
           return_value=["acme"])
    @patch("search.enrichment.ignored_domain",
           return_value=False)
    @patch("search.enrichment.safe_official_url",
           return_value="https://example.com/")
    def test_company_is_verified_with_siret(
        self,
        mock_safe_url,
        mock_ignored,
        mock_tokens,
        mock_fetch,
        mock_page_text,
        mock_legal_links,
        mock_siren,
        mock_registry,
    ):
        soup = BeautifulSoup(
            "<html><body>ACME</body></html>",
            "html.parser",
        )

        mock_fetch.side_effect = [
            (
                "https://example.com/",
                "text/html",
                "<html>Accueil</html>",
            ),
            (
                "https://example.com/mentions-legales",
                "text/html",
                "<html>Mentions légales</html>",
            ),
        ]

        mock_page_text.side_effect = [
            (soup, "ACME", "", "Accueil"),
            (soup, "Mentions légales", "", "ACME SIRET"),
        ]

        verified = enrichment.verify_company_website(
            "https://example.com/",
            "ACME",
            siret="12345678901234",
            locality="Amiens",
        )

        self.assertTrue(verified)
        mock_siren.assert_called_once_with(
            "ACME SIRET",
            "123456789",
        )
        mock_registry.assert_called_once_with(
            "12345678901234",
            "Amiens",
        )


    @patch("search.enrichment.verify_siret_in_public_registry", return_value=True)
    @patch("search.enrichment._has_exact_siren", return_value=True)
    @patch("search.enrichment._legal_notice_links", return_value=[])
    @patch("search.enrichment.page_text")
    @patch("search.enrichment._fetch_pinned_page")
    @patch("search.enrichment.company_tokens", return_value=["cgi"])
    @patch("search.enrichment.ignored_domain", return_value=False)
    @patch("search.enrichment.safe_official_url")
    def test_company_is_verified_with_external_evidence_url(
        self,
        mock_safe_url,
        mock_ignored,
        mock_tokens,
        mock_fetch,
        mock_page_text,
        mock_legal_links,
        mock_siren,
        mock_registry,
    ):
        homepage = "https://www.cgi.com/france/fr-fr"
        evidence = "https://www.cgi.com/france/fr-fr/event/games-on-web-2026"

        mock_safe_url.side_effect = lambda value: value

        soup = BeautifulSoup(
            "<html><body>CGI</body></html>",
            "html.parser",
        )

        mock_fetch.side_effect = [
            (
                homepage,
                "text/html",
                "<html>Accueil CGI</html>",
            ),
            (
                evidence,
                "text/html",
                "<html>CGI France SAS - 702 042 755</html>",
            ),
        ]

        mock_page_text.side_effect = [
            (soup, "CGI FR", "", "Accueil CGI"),
            (
                soup,
                "Games On Web 2026",
                "",
                "CGI France SAS immatriculée sous le numéro 702 042 755",
            ),
        ]

        verified = enrichment.verify_company_website(
            homepage,
            "CGI",
            legal_name="CGI FRANCE",
            brand="CGI",
            siret="70204275500307",
            locality="Amiens",
            evidence_urls=[evidence],
        )

        self.assertTrue(verified)
        mock_registry.assert_called_once_with(
            "70204275500307",
            "Amiens",
        )
        mock_siren.assert_called_once_with(
            "CGI France SAS immatriculée sous le numéro 702 042 755",
            "702042755",
        )
        self.assertEqual(mock_fetch.call_count, 2)


class TestEvidenceUrlPriority(unittest.TestCase):

    def test_legal_documents_are_prioritized(self):
        urls = [
            "https://www.example.com/news/company-event",
            "https://www.example.com/documents/contract_cga.pdf",
            "https://www.example.com/legal/mentions-legales",
            "https://www.example.com/about/company",
        ]

        ordered = sorted(urls, key=enrichment._evidence_url_priority)

        self.assertEqual(
            ordered,
            [
                "https://www.example.com/legal/mentions-legales",
                "https://www.example.com/documents/contract_cga.pdf",
                "https://www.example.com/about/company",
                "https://www.example.com/news/company-event",
            ],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestSearXNGDiscovery(unittest.TestCase):

    def test_company_domain_is_accepted_as_candidate(self):
        candidates = {}

        enrichment._add_search_candidate(
            candidates,
            "https://skeewai.fr/",
            "SKEEWAI - Site officiel",
            "Présentation de SKEEWAI",
            enrichment.company_tokens("SKEEWAI"),
            "",
            '"SKEEWAI" site officiel',
        )

        self.assertIn("https://skeewai.fr/", candidates)

    def test_business_directory_is_rejected(self):
        candidates = {}

        enrichment._add_search_candidate(
            candidates,
            "https://www.pappers.fr/entreprise/skeewai",
            "SKEEWAI - fiche entreprise",
            "Informations sur SKEEWAI",
            enrichment.company_tokens("SKEEWAI"),
            "",
            '"SKEEWAI" site officiel',
        )

        self.assertEqual(candidates, {})

    def test_known_business_directories_are_rejected(self):
        domains = [
            "lesentreprises.com",
            "bt-africa.com",
            "infopass.fr",
            "legl.fr",
            "lejournaldesentreprises.com",
        ]

        for domain in domains:
            with self.subTest(domain=domain):
                candidates = {}

                enrichment._add_search_candidate(
                    candidates,
                    f"https://www.{domain}/skeewai",
                    "SKEEWAI - fiche entreprise",
                    "Informations sur SKEEWAI",
                    enrichment.company_tokens("SKEEWAI"),
                    "89862852400024",
                    '"89862852400024"',
                )

                self.assertEqual(candidates, {})

    def test_siret_alone_does_not_accept_unrelated_domain(self):
        candidates = {}

        enrichment._add_search_candidate(
            candidates,
            "https://example.com/entreprise/89862852400024",
            "Fiche entreprise",
            "SIRET : 89862852400024",
            enrichment.company_tokens("SKEEWAI"),
            "89862852400024",
            '"89862852400024"',
        )

        self.assertEqual(candidates, {})

    @patch("search.enrichment.verify_company_website", return_value=True)
    @patch("search.enrichment.requests.get")
    def test_searxng_is_used_before_bing(
        self,
        mock_get,
        mock_verify,
    ):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [
                {
                    "url": "https://skeewai.fr/",
                    "title": "SKEEWAI - Site officiel",
                    "content": "Présentation de SKEEWAI",
                }
            ]
        }
        mock_get.return_value = response

        result = enrichment.discover_official_website(
            "SKEEWAI",
            "Petit-Quevilly",
            legal_name="SKEEWAI",
            brand="SKEEWAI",
            siret="89862852400024",
        )

        self.assertEqual(
            result,
            (
                "https://skeewai.fr/",
                "website_verified",
                "https://skeewai.fr/",
            ),
        )

        self.assertTrue(mock_get.called)
        self.assertTrue(
            all(
                call.args[0].startswith(enrichment.SEARXNG_URL)
                for call in mock_get.call_args_list
            )
        )
        mock_verify.assert_called_once()


class TestBingFallback(unittest.TestCase):

    @patch("search.enrichment.verify_company_website", return_value=True)
    @patch("search.enrichment.requests.get")
    def test_bing_is_used_when_searxng_has_no_candidates(
        self,
        mock_get,
        mock_verify,
    ):
        searx_response = MagicMock()
        searx_response.raise_for_status.return_value = None
        searx_response.json.return_value = {"results": []}

        bing_response = MagicMock()
        bing_response.raise_for_status.return_value = None
        bing_response.text = """
        <html>
          <body>
            <li class="b_algo">
              <h2>
                <a href="https://skeewai.fr/">
                  SKEEWAI - Site officiel
                </a>
              </h2>
              <div class="b_caption">
                <p>Présentation de SKEEWAI</p>
              </div>
            </li>
          </body>
        </html>
        """

        def mock_search_response(url, *args, **kwargs):
            if url.startswith(enrichment.SEARXNG_URL):
                return searx_response
            if url.startswith("https://www.bing.com/"):
                return bing_response
            raise AssertionError(f"URL inattendue dans le test : {url}")

        mock_get.side_effect = mock_search_response

        result = enrichment.discover_official_website(
            "SKEEWAI",
            "Petit-Quevilly",
            legal_name="SKEEWAI",
            brand="SKEEWAI",
            siret="89862852400024",
        )

        self.assertEqual(result[1], "website_verified")
        self.assertEqual(result[0], "https://skeewai.fr/")

        called_urls = [
            call.args[0]
            for call in mock_get.call_args_list
        ]

        self.assertTrue(
            any(url.startswith("https://www.bing.com/") for url in called_urls)
        )

        targeted_queries = [
            call.kwargs.get("params", {}).get("q", "")
            for call in mock_get.call_args_list
            if call.args[0].startswith("https://www.bing.com/")
        ]

        self.assertTrue(
            any(
                'site:skeewai.fr "898628524"' in query
                for query in targeted_queries
            ),
            "La recherche ciblée par SIREN n'a pas été effectuée.",
        )

        mock_verify.assert_called_once()




class TestBingEvidenceCollection(unittest.TestCase):

    def test_collects_secondary_pdf_link_on_exact_official_host(self):
        html = """
        <li class="b_algo">
          <h2><a href="https://www.cgi.com/france/fr-fr">CGI</a></h2>
          <div>
            <a href="https://www.cgi.com/docs/mentions-legales.pdf">
              Mentions légales PDF
            </a>
          </div>
        </li>
        """
        soup = BeautifulSoup(html, "html.parser")
        pages = {}

        enrichment._collect_bing_evidence_links(
            soup, "www.cgi.com", pages
        )

        self.assertIn(
            "https://www.cgi.com/docs/mentions-legales.pdf",
            pages["www.cgi.com"],
        )

    def test_rejects_external_domain_links(self):
        html = """
        <li class="b_algo">
          <a href="https://www.cgi.com/">CGI</a>
          <a href="https://fake-example.com/cgi-proof.pdf">PDF</a>
        </li>
        """
        soup = BeautifulSoup(html, "html.parser")
        pages = {}

        enrichment._collect_bing_evidence_links(
            soup, "www.cgi.com", pages
        )

        self.assertEqual(
            pages.get("www.cgi.com", []),
            ["https://www.cgi.com/"],
        )

    def test_rejects_similar_but_different_domain(self):
        html = """
        <li class="b_algo">
          <a href="https://www.cgi.com.evil.example/proof.pdf">PDF</a>
        </li>
        """
        soup = BeautifulSoup(html, "html.parser")
        pages = {}

        enrichment._collect_bing_evidence_links(
            soup, "www.cgi.com", pages
        )

        self.assertEqual(pages.get("www.cgi.com", []), [])


class TestPublicRegistrySiret(unittest.TestCase):

    SIRET = "42332925900075"

    def make_response(self, establishment=None):
        response = MagicMock()
        response.raise_for_status.return_value = None

        response.json.return_value = {
            "results": [
                {
                    "siren": "423329259",
                    "matching_etablissements": [
                        establishment or {
                            "siret": self.SIRET,
                            "etat_administratif": "A",
                            "date_fermeture": None,
                            "libelle_commune": "AMIENS",
                        }
                    ],
                }
            ]
        }

        return response

    @patch("search.enrichment.requests.get")
    def test_active_establishment_in_expected_city_is_accepted(
        self, mock_get
    ):
        mock_get.return_value = self.make_response()

        result = enrichment.verify_siret_in_public_registry(
            self.SIRET,
            "Amiens",
        )

        self.assertTrue(result)
        mock_get.assert_called_once()

        args = mock_get.call_args
        self.assertEqual(
            args.args[0],
            "https://recherche-entreprises.api.gouv.fr/search",
        )
        self.assertEqual(
            args.kwargs["params"],
            {"q": self.SIRET},
        )

    @patch("search.enrichment.requests.get")
    def test_different_siret_is_rejected(self, mock_get):
        mock_get.return_value = self.make_response({
            "siret": "42332925900059",
            "etat_administratif": "A",
            "date_fermeture": None,
            "libelle_commune": "AMIENS",
        })

        self.assertFalse(
            enrichment.verify_siret_in_public_registry(
                self.SIRET,
                "Amiens",
            )
        )

    @patch("search.enrichment.requests.get")
    def test_closed_establishment_is_rejected(self, mock_get):
        mock_get.return_value = self.make_response({
            "siret": self.SIRET,
            "etat_administratif": "F",
            "date_fermeture": "2025-01-01",
            "libelle_commune": "AMIENS",
        })

        self.assertFalse(
            enrichment.verify_siret_in_public_registry(
                self.SIRET,
                "Amiens",
            )
        )

    @patch("search.enrichment.requests.get")
    def test_wrong_city_is_rejected(self, mock_get):
        mock_get.return_value = self.make_response({
            "siret": self.SIRET,
            "etat_administratif": "A",
            "date_fermeture": None,
            "libelle_commune": "ROUEN",
        })

        self.assertFalse(
            enrichment.verify_siret_in_public_registry(
                self.SIRET,
                "Amiens",
            )
        )

    @patch("search.enrichment.requests.get")
    def test_invalid_json_structure_is_rejected(self, mock_get):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = []
        mock_get.return_value = response

        self.assertFalse(
            enrichment.verify_siret_in_public_registry(
                self.SIRET,
                "Amiens",
            )
        )

    @patch("search.enrichment.requests.get")
    def test_network_error_is_rejected(self, mock_get):
        mock_get.side_effect = enrichment.requests.ConnectionError(
            "API indisponible"
        )

        self.assertFalse(
            enrichment.verify_siret_in_public_registry(
                self.SIRET,
                "Amiens",
            )
        )


class TestInternalIdentityEvidence(unittest.TestCase):

    def test_cgi_internal_event_page_can_verify_company(self):
        homepage = "https://www.cgi.com/france/fr-fr"
        evidence = (
            "https://www.cgi.com/france/fr-fr/"
            "event/games-on-web-2026"
        )

        home_soup = BeautifulSoup(
            '<a href="/france/fr-fr/event/games-on-web-2026">'
            'Événement</a>',
            "html.parser",
        )
        evidence_soup = BeautifulSoup(
            "<html><body>CGI France SAS</body></html>",
            "html.parser",
        )

        with (
            patch(
                "search.enrichment.safe_official_url",
                side_effect=lambda value: value,
            ),
            patch(
                "search.enrichment.ignored_domain",
                return_value=False,
            ),
            patch(
                "search.enrichment.company_tokens",
                return_value=["cgi"],
            ),
            patch(
                "search.enrichment.verify_siret_in_public_registry",
                return_value=True,
            ) as mock_registry,
            patch(
                "search.enrichment._legal_notice_links",
                return_value=[],
            ),
            patch(
                "search.enrichment._has_exact_siren",
                return_value=True,
            ),
            patch(
                "search.enrichment.contains_identity",
                return_value=True,
            ),
            patch(
                "search.enrichment._fetch_pinned_page",
                side_effect=[
                    (homepage, "text/html", "<html>Accueil CGI</html>"),
                    (evidence, "text/html", "<html>Preuve CGI</html>"),
                ],
            ) as mock_fetch,
            patch(
                "search.enrichment.page_text",
                side_effect=[
                    (home_soup, "CGI FR", "", "Accueil CGI"),
                    (
                        evidence_soup,
                        "Games On Web 2026",
                        "",
                        "CGI France SAS immatriculée sous le numéro "
                        "702 042 755",
                    ),
                ],
            ),
        ):
            verified = enrichment.verify_company_website(
                homepage,
                "CGI",
                legal_name="CGI FRANCE",
                brand="CGI",
                siret="70204275500307",
                locality="Amiens",
            )

        self.assertTrue(verified)
        mock_registry.assert_called_once_with(
            "70204275500307",
            "Amiens",
        )
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(
            mock_fetch.call_args_list[1].args[0],
            evidence,
        )


class TestPdfIdentityEvidence(unittest.TestCase):

    def test_invalid_pdf_is_rejected(self):
        self.assertEqual(
            enrichment._extract_pdf_evidence(b"not a pdf"),
            "",
        )

    def test_pdf_without_signature_is_rejected(self):
        self.assertEqual(
            enrichment._extract_pdf_evidence(b"\x00" * 100),
            "",
        )

    def test_pdf_evidence_can_verify_company(self):
        homepage = "https://www.example.com/"
        evidence = "https://www.example.com/documents/rapport-annuel.pdf"

        home_soup = BeautifulSoup(
            '<a href="/documents/rapport-annuel.pdf">Rapport annuel</a>',
            "html.parser",
        )

        with (
            patch(
                "search.enrichment.safe_official_url",
                side_effect=lambda value: value,
            ),
            patch("search.enrichment.ignored_domain", return_value=False),
            patch("search.enrichment.company_tokens", return_value=["acme"]),
            patch(
                "search.enrichment.verify_siret_in_public_registry",
                return_value=True,
            ) as mock_registry,
            patch("search.enrichment._legal_notice_links", return_value=[]),
            patch(
                "search.enrichment._identity_evidence_links",
                return_value=[evidence],
            ),
            patch(
                "search.enrichment._fetch_pinned_page",
                side_effect=[
                    (homepage, "text/html", "<html>Accueil</html>"),
                    (evidence, "application/pdf", b"%PDF-1.5 fake"),
                ],
            ),
            patch(
                "search.enrichment.page_text",
                return_value=(home_soup, "Accueil", "", "ACME"),
            ),
            patch(
                "search.enrichment._extract_pdf_evidence",
                return_value=(
                    "ACME France SAS SIREN 123456789"
                ),
            ) as mock_extract,
            patch(
                "search.enrichment._has_exact_siren",
                return_value=True,
            ),
            patch(
                "search.enrichment.contains_identity",
                return_value=True,
            ),
        ):
            verified = enrichment.verify_company_website(
                homepage,
                "ACME",
                legal_name="ACME FRANCE",
                siret="12345678900012",
                locality="Rouen",
            )

        self.assertTrue(verified)
        mock_registry.assert_called_once_with("12345678900012", "Rouen")
        mock_extract.assert_called_once_with(b"%PDF-1.5 fake")

    def test_contractual_pdf_link_is_recognized(self):
        soup = BeautifulSoup(
            '<a href="/documents/contract_cga_-_sous-traitance.pdf">'
            'Conditions générales de sous-traitance</a>',
            "html.parser",
        )
        links = enrichment._identity_evidence_links(
            soup,
            "https://www.example.com/",
            "www.example.com",
        )
        self.assertEqual(
            links,
            [
                "https://www.example.com/"
                "documents/contract_cga_-_sous-traitance.pdf"
            ],
        )

    def test_pdf_link_without_institutional_label_is_ignored(self):
        soup = BeautifulSoup(
            '<a href="/files/catalogue.pdf">Catalogue produits</a>',
            "html.parser",
        )
        links = enrichment._identity_evidence_links(
            soup,
            "https://www.example.com/",
            "www.example.com",
        )
        self.assertEqual(links, [])

