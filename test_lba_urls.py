import unittest

from search.la_bonne_alternance import is_valid_web_url


class TestLBAUrls(unittest.TestCase):

    def test_accepts_valid_http_urls(self):
        self.assertTrue(
            is_valid_web_url("https://entreprise.fr/candidature")
        )
        self.assertTrue(
            is_valid_web_url("http://entreprise.fr/candidature")
        )

    def test_rejects_invalid_urls(self):
        invalid_urls = [
            "",
            "   ",
            None,
            "ftp://entreprise.fr",
            "https:///page",
            "ceci-n-est-pas-une-url",
        ]

        for url in invalid_urls:
            with self.subTest(url=url):
                self.assertFalse(is_valid_web_url(url))


if __name__ == "__main__":
    unittest.main(verbosity=2)
