import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database


class TestOfferWorkflow(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_botboss.db"

        self.db_patch = patch.object(database, "DB_PATH", self.db_path)
        self.db_patch.start()

        database.init_db()

        self.offer = {
            "title": "Développeur informatique en alternance",
            "company": "Entreprise Test",
            "url": "https://example.com/offre/123",
            "snippet": "Contrat en alternance",
            "location": "Rouen",
            "source_domain": "example.com",
            "source_type": "offre publiée",
            "matched_skills": ["Go", "Python"],
            "score": 80,
            "tailored_message": "Message de test",
        }

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def get_offer(self):
        with database.connect() as conn:
            return conn.execute(
                "SELECT * FROM offers WHERE url = ?",
                (self.offer["url"],)
            ).fetchone()

    def test_new_offer_starts_to_verify(self):
        database.save_offer(self.offer)

        saved = self.get_offer()

        self.assertIsNotNone(saved)
        self.assertEqual(saved["status"], "to_verify")
        self.assertEqual(saved["human_verified"], 0)

    def test_unverified_duplicate_can_be_updated(self):
        database.save_offer(self.offer)

        updated = dict(self.offer)
        updated["title"] = "Titre actualisé"
        updated["snippet"] = "Nouvelle description"

        database.save_offer(updated)

        saved = self.get_offer()

        self.assertEqual(saved["title"], "Titre actualisé")
        self.assertEqual(saved["snippet"], "Nouvelle description")
        self.assertEqual(saved["status"], "to_verify")
        self.assertEqual(saved["human_verified"], 0)

    def test_manually_verified_offer_is_preserved(self):
        database.save_offer(self.offer)

        with database.connect() as conn:
            conn.execute("""
                UPDATE offers
                SET status = 'pending', human_verified = 1
                WHERE url = ?
            """, (self.offer["url"],))

        updated = dict(self.offer)
        updated["title"] = "Titre qui ne doit pas remplacer l'ancien"

        database.save_offer(updated)

        saved = self.get_offer()

        self.assertEqual(
            saved["title"],
            "Développeur informatique en alternance"
        )
        self.assertEqual(saved["status"], "pending")
        self.assertEqual(saved["human_verified"], 1)

    def test_processed_offers_are_preserved(self):
        protected_statuses = (
            "approved",
            "sending",
            "smtp_accepted",
        )

        for status in protected_statuses:
            with self.subTest(status=status):
                database.save_offer(self.offer)

                with database.connect() as conn:
                    conn.execute("""
                        UPDATE offers
                        SET status = ?
                        WHERE url = ?
                    """, (status, self.offer["url"]))

                updated = dict(self.offer)
                updated["title"] = "Titre modifié interdit"

                database.save_offer(updated)

                saved = self.get_offer()

                self.assertEqual(
                    saved["title"],
                    "Développeur informatique en alternance"
                )
                self.assertEqual(saved["status"], status)

                with database.connect() as conn:
                    conn.execute(
                        "DELETE FROM offers WHERE url = ?",
                        (self.offer["url"],)
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
