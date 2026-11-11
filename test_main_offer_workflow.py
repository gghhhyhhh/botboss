import sys
import unittest
from unittest.mock import patch

import main


class TestMainOfferWorkflow(unittest.TestCase):

    def setUp(self):
        self.offer = {
            "id": 123,
            "title": "Développeur informatique en alternance",
            "company": "Entreprise Test",
            "url": "https://example.com/offre/123",
            "snippet": "Contrat en alternance",
            "source_type": "offre publiée",
            "human_verified": 0,
            "status": "to_verify",
            "tailored_message": "Bonjour votre entreprise",
        }

    def run_cli(self, arguments, answers=()):
        return (
            patch.object(main, "init_db"),
            patch.object(main, "get_offer", return_value=self.offer),
            patch.object(main, "update_offer"),
            patch.object(main, "is_published_offer", return_value=True),
            patch("builtins.input", side_effect=answers),
            patch.object(sys, "argv", ["main.py"] + arguments),
        )

    def test_verify_refuses_already_verified_offer(self):
        self.offer["human_verified"] = 1

        patches = self.run_cli(["verify", "123"])
        with patches[0], patches[1], patches[2] as update, patches[5]:
            with self.assertRaises(SystemExit):
                main.main()

        update.assert_not_called()

    def test_verify_refuses_invalid_status(self):
        self.offer["status"] = "approved"

        patches = self.run_cli(["verify", "123"])
        with patches[0], patches[1], patches[2] as update, patches[5]:
            with self.assertRaises(SystemExit):
                main.main()

        update.assert_not_called()

    def test_verify_accepts_three_yes_answers(self):
        self.offer["status"] = "to_verify"

        patches = self.run_cli(["verify", "123"], ["oui", "oui", "oui"])
        with patches[0], patches[1], patches[2] as update, \
             patches[4], patches[5]:
            main.main()

        update.assert_called_once_with(
            123, human_verified=1, status="pending"
        )

    def test_approve_refuses_unverified_offer(self):
        self.offer["human_verified"] = 0
        self.offer["status"] = "pending"

        patches = self.run_cli([
            "approve", "123",
            "--company", "Entreprise Test",
            "--email", "contact@example.com",
        ])

        with patches[0], patches[1], patches[2] as update, patches[5]:
            with self.assertRaises(SystemExit):
                main.main()

        update.assert_not_called()

    def test_approve_refuses_verified_offer_with_wrong_status(self):
        self.offer["human_verified"] = 1
        self.offer["status"] = "to_verify"

        patches = self.run_cli([
            "approve", "123",
            "--company", "Entreprise Test",
            "--email", "contact@example.com",
        ])

        with patches[0], patches[1], patches[2] as update, patches[5]:
            with self.assertRaises(SystemExit):
                main.main()

        update.assert_not_called()

    def test_approve_accepts_verified_pending_offer(self):
        self.offer["human_verified"] = 1
        self.offer["status"] = "pending"

        patches = self.run_cli([
            "approve", "123",
            "--company", "Entreprise Test",
            "--email", "contact@example.com",
        ], ["oui"])

        with patches[0], patches[1], patches[2] as update, \
             patches[3], patches[4], patches[5]:
            main.main()

        update.assert_called_once_with(
            123,
            company="Entreprise Test",
            contact_email="contact@example.com",
            tailored_message="Bonjour Entreprise Test",
            status="approved",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
