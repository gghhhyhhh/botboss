import unittest
from datetime import datetime, timezone

from search.la_bonne_alternance import is_offer_available


class TestLBAOfferAvailability(unittest.TestCase):

    def setUp(self):
        self.now = datetime(2026, 11, 12, 17, 0, tzinfo=timezone.utc)

    def test_active_offer_not_expired(self):
        offer = {
            "status": "Active",
            "publication": {
                "expiration": "2026-11-14T23:05:57.064Z"
            },
        }
        self.assertTrue(is_offer_available(offer, self.now))

    def test_expired_offer_is_rejected(self):
        offer = {
            "status": "Active",
            "publication": {
                "expiration": "2026-11-10T23:05:57.064Z"
            },
        }
        self.assertFalse(is_offer_available(offer, self.now))

    def test_inactive_offer_is_rejected(self):
        offer = {
            "status": "Closed",
            "publication": {
                "expiration": "2026-11-14T23:05:57.064Z"
            },
        }
        self.assertFalse(is_offer_available(offer, self.now))

    def test_invalid_expiration_is_rejected(self):
        offer = {
            "status": "Active",
            "publication": {
                "expiration": "date-invalide"
            },
        }
        self.assertFalse(is_offer_available(offer, self.now))

    def test_missing_expiration_is_accepted(self):
        offer = {
            "status": "Active",
            "publication": {},
        }
        self.assertTrue(is_offer_available(offer, self.now))

    def test_missing_status_is_rejected(self):
        offer = {
            "publication": {
                "expiration": "2026-11-14T23:05:57.064Z"
            },
        }
        self.assertFalse(is_offer_available(offer, self.now))

    def test_missing_offer_is_rejected(self):
        self.assertFalse(is_offer_available(None, self.now))

    def test_malformed_publication_is_rejected(self):
        offer = {
            "status": "Active",
            "publication": "invalid",
        }
        self.assertFalse(is_offer_available(offer, self.now))

    def test_non_string_expiration_is_rejected(self):
        offer = {
            "status": "Active",
            "publication": {"expiration": 12345},
        }
        self.assertFalse(is_offer_available(offer, self.now))

    def test_missing_publication_is_accepted(self):
        offer = {"status": "Active"}
        self.assertTrue(is_offer_available(offer, self.now))


if __name__ == "__main__":
    unittest.main()
