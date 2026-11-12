import unittest
from unittest.mock import patch

import search.jobs as jobs


class TestJobsSearch(unittest.TestCase):

    def test_find_target_locality_accepts_target_areas(self):
        cases = [
            ("Rouen", "Rouen"),
            ("BOIS GUILLAUME", "Bois-Guillaume"),
            ("Saint-Étienne-du-Rouvray", "Saint-Étienne-du-Rouvray"),
            ("Amiens, France", "Amiens"),
            ("Longueau, Somme", "Longueau"),
        ]

        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    jobs.find_target_locality(value),
                    expected,
                )

    def test_find_target_locality_rejects_out_of_area(self):
        cases = [
            "Paris",
            "Lyon",
            "Toulouse",
            "Normandie",
            "Rouennais",
        ]

        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    jobs.find_target_locality(value),
                    "",
                )

    def test_is_excluded_rejects_training_organizations(self):
        cases = [
            "École de développement informatique",
            "Centre de formation en alternance",
            "Organisme de formation",
            "Formation à distance",
            "Inscription formation",
            "Campus informatique",
        ]

        for value in cases:
            with self.subTest(value=value):
                self.assertTrue(jobs.is_excluded(value))

    def test_is_excluded_accepts_company_offer(self):
        self.assertFalse(
            jobs.is_excluded(
                "Développeur web en alternance chez Entreprise Test"
            )
        )

    def test_build_message_contains_offer_and_profile_links(self):
        message = jobs.build_message(
            "Développeur web en alternance",
            "Entreprise Test",
            ["Go", "Python"],
        )

        self.assertIn("Développeur web en alternance", message)
        self.assertIn("Entreprise Test", message)
        self.assertIn("Go, Python", message)
        self.assertIn("https://gghhhyhhh.github.io/cv/", message)
        self.assertIn(
            "https://gghhhyhhh.github.io/portfolio-independant/index.html",
            message,
        )

    def test_dry_run_does_not_save_offers(self):
        result = {
            "title": "Développeur web en alternance - Rouen",
            "href": "https://example.com/jobs/123",
            "body": "Alternance développeur web à Rouen",
        }

        with (
            patch.object(jobs, "load_skills", return_value=[]),
            patch.object(jobs, "SEARCH_AREAS", ["Rouen"]),
            patch.object(jobs, "JOB_TERMS", ["alternance développeur web"]),
            patch.object(jobs, "DDGS") as ddgs_class,
            patch.object(jobs, "save_offer") as save_offer,
            patch("builtins.print"),
        ):
            ddgs = ddgs_class.return_value.__enter__.return_value
            ddgs.text.return_value = [result]

            jobs.search_jobs(dry_run=True)

            save_offer.assert_not_called()


    def test_offer_in_paris_is_rejected_even_if_snippet_mentions_rouen(self):
        result = {
            "title": "Développeur web en alternance à Paris",
            "href": "https://example.com/jobs/456",
            "body": (
                "Poste situé à Paris. "
                "Notre entreprise travaille également avec Rouen."
            ),
        }

        with (
            patch.object(jobs, "load_skills", return_value=[]),
            patch.object(jobs, "SEARCH_AREAS", ["Rouen"]),
            patch.object(jobs, "JOB_TERMS", ["alternance développeur web"]),
            patch.object(jobs, "DDGS") as ddgs_class,
            patch.object(jobs, "save_offer") as save_offer,
            patch("builtins.print"),
        ):
            ddgs = ddgs_class.return_value.__enter__.return_value
            ddgs.text.return_value = [result]

            jobs.search_jobs(dry_run=False)

            save_offer.assert_not_called()


    def test_parse_job_url_accepts_valid_http_urls(self):
        cases = [
            "https://indeed.com/jobs/123",
            "http://fr.indeed.com/emploi/123",
            "https://example.com/offres/developpeur",
        ]

        for url in cases:
            with self.subTest(url=url):
                self.assertIsNotNone(jobs.parse_job_url(url))

    def test_parse_job_url_rejects_invalid_or_dangerous_urls(self):
        cases = [
            "javascript:alert(1)",
            "ftp://indeed.com/jobs",
            "https://",
            "https://user:pass@indeed.com/jobs",
            "https://indeed.com:invalid/jobs",
            "https://example.com/path with spaces",
            "",
        ]

        for url in cases:
            with self.subTest(url=url):
                self.assertIsNone(jobs.parse_job_url(url))

    def test_is_job_board_domain_accepts_official_domains_and_subdomains(self):
        cases = [
            "indeed.com",
            "fr.indeed.com",
            "www.indeed.com",
            "hellowork.com",
        ]

        for domain in cases:
            with self.subTest(domain=domain):
                self.assertTrue(jobs.is_job_board_domain(domain))

    def test_is_job_board_domain_rejects_spoofed_domains(self):
        cases = [
            "indeed.com.evil.test",
            "fakeindeed.com",
            "evil.test",
        ]

        for domain in cases:
            with self.subTest(domain=domain):
                self.assertFalse(jobs.is_job_board_domain(domain))


    def test_search_result_pages_are_rejected(self):
        urls = [
            "https://example.com/search?q=developpeur",
            "https://example.com/recherche",
            "https://example.com/results",
            "https://example.com/jobs-search",
        ]

        for url in urls:
            with self.subTest(url=url):
                result = {
                    "title": "Développeur web en alternance à Rouen",
                    "href": url,
                    "body": "Alternance développeur web à Rouen",
                }

                with (
                    patch.object(jobs, "load_skills", return_value=[]),
                    patch.object(jobs, "SEARCH_AREAS", ["Rouen"]),
                    patch.object(jobs, "JOB_TERMS", ["alternance développeur web"]),
                    patch.object(jobs, "DDGS") as ddgs_class,
                    patch.object(jobs, "save_offer") as save_offer,
                    patch("builtins.print"),
                ):
                    ddgs = ddgs_class.return_value.__enter__.return_value
                    ddgs.text.return_value = [result]

                    jobs.search_jobs(dry_run=False)

                    save_offer.assert_not_called()

    def test_duplicate_offer_is_saved_only_once(self):
        result = {
            "title": "Développeur web en alternance à Rouen",
            "href": "https://example.com/jobs/123",
            "body": "Alternance développeur web à Rouen",
        }

        with (
            patch.object(jobs, "load_skills", return_value=[]),
            patch.object(jobs, "SEARCH_AREAS", ["Rouen", "Amiens"]),
            patch.object(jobs, "JOB_TERMS", ["alternance développeur web"]),
            patch.object(jobs, "DDGS") as ddgs_class,
            patch.object(jobs, "save_offer") as save_offer,
            patch("builtins.print"),
        ):
            ddgs = ddgs_class.return_value.__enter__.return_value
            ddgs.text.side_effect = [[result], [result]]

            jobs.search_jobs(dry_run=False)

            save_offer.assert_called_once()


    def test_rejects_known_job_board_listing_urls(self):
        urls = [
            "https://fr.indeed.com/q-alternance-developpeur-l-rouen-emplois.html",
            "https://fr.jooble.org/emploi-developpeur-web-alternance/Rouen",
            "https://www.hellowork.com/fr-fr/alternance/metier_developpeur-web-ville_rouen-76000.html",
            "https://www.hellowork.com/fr-fr/alternance/domaine_informatique-ville_amiens-80000.html",
            "https://www.bing.com/aclick?url=https://example.com",
        ]

        for url in urls:
            with self.subTest(url=url):
                parsed, hostname = jobs.parse_job_url(url)
                self.assertTrue(
                    jobs.is_search_or_listing_url(parsed, hostname)
                )

    def test_accepts_individual_job_offer_urls(self):
        urls = [
            "https://fr.indeed.com/viewjob?jk=abc123",
            "https://www.hellowork.com/fr-fr/emplois/123456.html",
            "https://example.com/jobs/developpeur-web-123",
            "https://entreprise.fr/carrieres/offre-developpeur",
        ]

        for url in urls:
            with self.subTest(url=url):
                parsed, hostname = jobs.parse_job_url(url)
                self.assertFalse(
                    jobs.is_search_or_listing_url(parsed, hostname)
                )

    def test_rejects_generic_search_urls(self):
        urls = [
            "https://example.com/search?q=developpeur",
            "https://example.com/recherche",
            "https://example.com/results",
            "https://example.com/jobs-search",
        ]

        for url in urls:
            with self.subTest(url=url):
                parsed, hostname = jobs.parse_job_url(url)
                self.assertTrue(
                    jobs.is_search_or_listing_url(parsed, hostname)
                )


    def test_rejects_additional_indeed_and_jobsora_listing_urls(self):
        urls = [
            "https://fr.indeed.com/Rouen-(76)-Emplois-Alternance-Développeur",
            "https://fr.jobsora.com/emplois-développeur-logiciel-alternance-amiens",
        ]

        for url in urls:
            with self.subTest(url=url):
                parsed, hostname = jobs.parse_job_url(url)
                self.assertTrue(
                    jobs.is_search_or_listing_url(parsed, hostname)
                )

    def test_training_domains_are_rejected(self):
        domains = [
            "pigier.com",
            "www.pigier.com",
            "campus.pigier.com",
        ]

        for domain in domains:
            with self.subTest(domain=domain):
                self.assertTrue(jobs.is_training_domain(domain))

    def test_company_domains_are_not_training_domains(self):
        domains = [
            "example.com",
            "entreprise.fr",
            "indeed.com",
        ]

        for domain in domains:
            with self.subTest(domain=domain):
                self.assertFalse(jobs.is_training_domain(domain))


if __name__ == "__main__":
    unittest.main()
