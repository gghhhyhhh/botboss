import os
import unittest
from unittest.mock import MagicMock, patch

import search.la_bonne_alternance as lba


class TestLBASearch(unittest.TestCase):

    def test_offer_without_title_is_ignored(self):
        for title in ("", "   ", None):
            with self.subTest(title=repr(title)):
                response = MagicMock()
                response.json.return_value = {
                    "jobs": [
                        {
                            "workplace": {
                                "brand": "Entreprise Test",
                                "location": {"city": "Rouen"},
                            },
                            "offer": {
                                "title": title,
                                "status": "Active",
                                "publication": {
                                    "expiration": "2099-12-31T23:59:59Z"
                                },
                            },
                            "identifier": {},
                            "apply": {},
                        }
                    ]
                }

                with (
                    patch.dict(os.environ, {"LBA_API_KEY": "test-key"}),
                    patch.object(
                        lba.requests, "get", return_value=response
                    ),
                    patch.object(lba, "load_profile", return_value={}),
                    patch.object(lba, "save_offer") as save_offer,
                ):
                    lba.search_lba(include_recruiters=False)

                    save_offer.assert_not_called()


    def test_valid_offer_is_saved_with_original_title(self):
        response = MagicMock()
        response.json.return_value = {
            "jobs": [
                {
                    "workplace": {
                        "brand": "Entreprise Test",
                        "location": {"city": "Rouen"},
                    },
                    "offer": {
                        "title": "Développeur web en alternance",
                        "description": "Développement d'applications web.",
                        "status": "Active",
                        "publication": {
                            "expiration": "2099-12-31T23:59:59Z"
                        },
                    },
                    "identifier": {
                        "partner_label": "Partenaire Test",
                        "partner_job_id": "job-123",
                    },
                    "apply": {
                        "url": "https://entreprise.example/jobs/123"
                    },
                }
            ]
        }

        with (
            patch.dict(os.environ, {"LBA_API_KEY": "test-key"}),
            patch.object(
                lba.requests, "get", return_value=response
            ),
            patch.object(lba, "load_profile", return_value={}),
            patch.object(lba, "save_offer") as save_offer,
        ):
            lba.search_lba(include_recruiters=False)

            save_offer.assert_called_once()
            saved_offer = save_offer.call_args.args[0]

            self.assertEqual(
                saved_offer["title"],
                "Développeur web en alternance",
            )
            self.assertEqual(saved_offer["company"], "Entreprise Test")
            self.assertEqual(
                saved_offer["url"],
                "https://entreprise.example/jobs/123",
            )



    def test_target_locality_uses_city_as_priority(self):
        cases = [
            (
                {
                    "location": {
                        "city": "Paris",
                        "label": "Rouen, Normandie",
                        "address": "Rouen",
                    }
                },
                "",
            ),
            (
                {
                    "location": {
                        "city": "Rouen",
                        "label": "Paris",
                    }
                },
                "Rouen",
            ),
            (
                {"location": {"label": "Amiens, France"}},
                "Amiens",
            ),
            (
                {"location": {"address": "Longueau, Somme"}},
                "Longueau",
            ),
            (
                {"location": "Bois-Guillaume"},
                "Bois-Guillaume",
            ),
            (
                {"location": {"city": "Paris"}},
                "",
            ),
        ]

        for workplace, expected in cases:
            with self.subTest(workplace=workplace):
                self.assertEqual(
                    lba.get_target_locality(workplace),
                    expected,
                )

    def test_deduplicate_records_ignores_non_dictionary_records(self):
        records = [
            None,
            "invalid",
            123,
            {"identifier": {"id": "job-1"}, "title": "Offre 1"},
            {"identifier": {"id": "job-1"}, "title": "Doublon"},
            {"title": "Sans identifiant"},
        ]

        result = lba.deduplicate_records(records)

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["title"], "Offre 1")

    def test_get_company_rejects_invalid_workplace(self):
        for workplace in (None, "invalid", [], 123):
            with self.subTest(workplace=workplace):
                self.assertEqual(lba.get_company(workplace), "")

    def test_get_company_converts_company_name_to_string(self):
        workplace = {"brand": 123}
        self.assertEqual(lba.get_company(workplace), "123")

    def test_find_target_locality_rejects_out_of_area(self):
        cases = [
            "Paris",
            "Lyon",
            "Normandie",
            "Rouennais",
            "Toulouse",
        ]

        for value in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    lba.find_target_locality(value),
                    "",
                )


    def test_dry_run_does_not_save_jobs_or_recruiters(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "jobs": [
                {
                    "workplace": {
                        "brand": "Entreprise Rouen",
                        "location": {"city": "Rouen"},
                    },
                    "offer": {
                        "title": "Développeur web en alternance",
                        "description": "Développement logiciel.",
                        "status": "Active",
                        "publication": {
                            "expiration": "2099-12-31T23:59:59Z"
                        },
                    },
                    "identifier": {
                        "partner_label": "Partenaire Test",
                        "partner_job_id": "job-123",
                    },
                    "apply": {
                        "url": "https://example.com/jobs/123"
                    },
                }
            ],
            "recruiters": [
                {
                    "workplace": {
                        "brand": "Entreprise Amiens",
                        "description": "Développement informatique.",
                        "location": {"city": "Amiens"},
                    },
                    "identifier": {"id": "recruiter-123"},
                    "apply": {},
                }
            ],
        }

        with (
            patch.dict(os.environ, {"LBA_API_KEY": "test-key"}),
            patch.object(lba.requests, "get", return_value=response),
            patch.object(lba, "load_profile", return_value={}),
            patch.object(lba, "get_company",
                         side_effect=lambda workplace: workplace["brand"]),
            patch.object(lba, "is_training_organization", return_value=False),
            patch.object(lba, "get_skills", return_value=["Go"]),
            patch.object(lba, "get_activity_score", return_value=50),
            patch.object(lba, "get_prospect_activity_score", return_value=50),
            patch.object(lba, "calculate_score", return_value=80),
            patch.object(
                lba,
                "calculate_prospect_score",
                return_value=(75, {"activité": 20}),
            ),
            patch.object(
                lba,
                "get_activity_info",
                return_value=("62.01Z", "Programmation informatique"),
            ),
            patch.object(lba, "build_message", return_value="Message test"),
            patch.object(lba, "save_offer") as save_offer,
            patch.object(lba, "save_prospect") as save_prospect,
        ):
            lba.search_lba(dry_run=True)

            save_offer.assert_not_called()
            save_prospect.assert_not_called()


    def test_api_http_errors_do_not_save_data(self):
        cases = [
            (401, "Erreur 401"),
            (403, "Erreur 403"),
            (419, "Erreur 419"),
        ]

        for status_code, expected_message in cases:
            with self.subTest(status_code=status_code):
                response = MagicMock()
                response.status_code = status_code

                with (
                    patch.dict(os.environ, {"LBA_API_KEY": "test-key"}),
                    patch.object(
                        lba.requests, "get", return_value=response
                    ) as request_get,
                    patch.object(lba, "save_offer") as save_offer,
                    patch.object(lba, "save_prospect") as save_prospect,
                    patch("builtins.print") as mocked_print,
                ):
                    lba.search_lba()

                    request_get.assert_called_once()
                    save_offer.assert_not_called()
                    save_prospect.assert_not_called()

                    messages = [
                        str(call.args[0])
                        for call in mocked_print.call_args_list
                        if call.args
                    ]
                    self.assertTrue(
                        any(expected_message in message for message in messages)
                    )

    def test_api_timeout_and_network_errors_do_not_save_data(self):
        cases = [
            (
                lba.requests.Timeout("timeout"),
                "délai dépassé",
            ),
            (
                lba.requests.RequestException("network error"),
                "Erreur API La Bonne Alternance",
            ),
        ]

        for error, expected_message in cases:
            with self.subTest(error=type(error).__name__):
                with (
                    patch.dict(os.environ, {"LBA_API_KEY": "test-key"}),
                    patch.object(
                        lba.requests, "get", side_effect=error
                    ),
                    patch.object(lba, "save_offer") as save_offer,
                    patch.object(lba, "save_prospect") as save_prospect,
                    patch("builtins.print") as mocked_print,
                ):
                    lba.search_lba()

                    save_offer.assert_not_called()
                    save_prospect.assert_not_called()

                    messages = [
                        str(call.args[0])
                        for call in mocked_print.call_args_list
                        if call.args
                    ]
                    self.assertTrue(
                        any(expected_message in message for message in messages)
                    )

    def test_invalid_json_does_not_save_data(self):
        response = MagicMock()
        response.status_code = 200
        response.raise_for_status.return_value = None
        response.json.side_effect = lba.requests.exceptions.JSONDecodeError(
            "Invalid JSON", "", 0
        )

        with (
            patch.dict(os.environ, {"LBA_API_KEY": "test-key"}),
            patch.object(lba.requests, "get", return_value=response),
            patch.object(lba, "save_offer") as save_offer,
            patch.object(lba, "save_prospect") as save_prospect,
            patch("builtins.print") as mocked_print,
        ):
            lba.search_lba()

            save_offer.assert_not_called()
            save_prospect.assert_not_called()

            messages = [
                str(call.args[0])
                for call in mocked_print.call_args_list
                if call.args
            ]
            self.assertTrue(
                any("JSON valide" in message for message in messages)
            )



    def test_inactive_and_expired_offers_are_ignored(self):
        cases = [
            (
                "inactive",
                {
                    "status": "Inactive",
                    "publication": {
                        "expiration": "2099-12-31T23:59:59Z"
                    },
                },
            ),
            (
                "expired",
                {
                    "status": "Active",
                    "publication": {
                        "expiration": "2000-01-01T00:00:00Z"
                    },
                },
            ),
        ]

        for case_name, availability in cases:
            with self.subTest(case=case_name):
                response = MagicMock()
                response.status_code = 200
                response.raise_for_status.return_value = None
                response.json.return_value = {
                    "jobs": [
                        {
                            "workplace": {
                                "brand": "Entreprise Test",
                                "location": {"city": "Rouen"},
                            },
                            "offer": {
                                "title": "Développeur web en alternance",
                                "description": "Développement logiciel.",
                                **availability,
                            },
                            "identifier": {
                                "partner_label": "Partenaire Test",
                                "partner_job_id": f"job-{case_name}",
                            },
                            "apply": {
                                "url": "https://example.com/jobs/123"
                            },
                        }
                    ]
                }

                with (
                    patch.dict(os.environ, {"LBA_API_KEY": "test-key"}),
                    patch.object(
                        lba.requests, "get", return_value=response
                    ),
                    patch.object(lba, "load_profile", return_value={}),
                    patch.object(lba, "save_offer") as save_offer,
                    patch("builtins.print") as mocked_print,
                ):
                    lba.search_lba(include_recruiters=False)

                    save_offer.assert_not_called()

                    messages = [
                        str(call.args[0])
                        for call in mocked_print.call_args_list
                        if call.args
                    ]

                    self.assertTrue(
                        any(
                            "Résultats ignorés : 1" in message
                            for message in messages
                        )
                    )

if __name__ == "__main__":
    unittest.main(verbosity=2)
