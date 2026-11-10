import unittest
from unittest.mock import patch

import email_validation


class TestEmailFormat(unittest.TestCase):

    def test_valid_addresses(self):
        addresses = [
            "contact@example.com",
            "prenom.nom@example.fr",
            "dev+alternance@example.com",
        ]

        for address in addresses:
            with self.subTest(address=address):
                self.assertTrue(
                    email_validation.valid_email_format(address)
                )

    def test_invalid_addresses(self):
        addresses = [
            "",
            "contact",
            "contact@",
            "@example.com",
            "contact@example",
            "contact..test@example.com",
            "contact@example..com",
            "contact@example.com\n",
            None,
        ]

        for address in addresses:
            with self.subTest(address=address):
                self.assertFalse(
                    email_validation.valid_email_format(address)
                )


class TestPublicURLValidation(unittest.TestCase):

    def test_http_is_rejected(self):
        with self.assertRaises(ValueError):
            email_validation.validate_public_url(
                "http://example.com/contact"
            )

    def test_localhost_is_rejected(self):
        with self.assertRaises(ValueError):
            email_validation.validate_public_url(
                "https://localhost/contact"
            )

    def test_private_ip_is_rejected(self):
        with self.assertRaises(ValueError):
            email_validation.validate_public_url(
                "https://127.0.0.1/contact"
            )

    def test_non_standard_port_is_rejected(self):
        with self.assertRaises(ValueError):
            email_validation.validate_public_url(
                "https://example.com:8443/contact"
            )

    @patch(
        "email_validation.socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("192.168.1.10", 443))
        ],
    )
    def test_private_dns_resolution_is_rejected(self, mock_dns):
        with self.assertRaises(ValueError):
            email_validation.validate_public_url(
                "https://example.com/contact"
            )

    @patch(
        "email_validation.socket.getaddrinfo",
        return_value=[
            (2, 1, 6, "", ("93.184.216.34", 443))
        ],
    )
    def test_public_dns_resolution_is_accepted(self, mock_dns):
        parsed, host, addresses = email_validation.validate_public_url(
            "https://example.com/contact"
        )

        self.assertEqual(host, "example.com")
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(addresses, ("93.184.216.34",))

    def test_official_domain_matching(self):
        self.assertTrue(
            email_validation.is_official_domain(
                "example.com",
                "example.com",
            )
        )

        self.assertTrue(
            email_validation.is_official_domain(
                "www.example.com",
                "example.com",
            )
        )

        self.assertFalse(
            email_validation.is_official_domain(
                "contact.example.com",
                "example.com",
            )
        )

        self.assertFalse(
            email_validation.is_official_domain(
                "example.com.evil.org",
                "example.com",
            )
        )


class TestEmailSourceValidation(unittest.TestCase):

    def test_unverified_website_is_rejected(self):
        with self.assertRaises(ValueError):
            email_validation.validate_email_source(
                "contact@example.com",
                "https://example.com/contact",
                "https://example.com",
                "not_verified",
            )

    def test_invalid_email_is_rejected(self):
        with self.assertRaises(ValueError):
            email_validation.validate_email_source(
                "adresse-invalide",
                "https://example.com/contact",
                "https://example.com",
                "website_verified",
            )

    def test_http_official_website_is_rejected(self):
        with self.assertRaises(ValueError):
            email_validation.validate_email_source(
                "contact@example.com",
                "https://example.com/contact",
                "http://example.com",
                "website_verified",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestRedirectSecurity(unittest.TestCase):

    def make_response(self, status, location=None):
        from types import SimpleNamespace

        headers = {}
        if location:
            headers["Location"] = location

        return SimpleNamespace(
            status_code=status,
            headers=headers,
            raw=SimpleNamespace(),
            text="contact@example.com",
            encoding="utf-8",
            iter_content=lambda chunk_size: [b"contact@example.com"],
            url="https://example.com/contact",
            close=lambda: None,
            raise_for_status=lambda: None,
        )

    def make_session(self, response):
        from unittest.mock import MagicMock

        session = MagicMock()
        session.__enter__.return_value = session
        session.__exit__.return_value = None
        session.get.return_value = response
        return session

    @patch("email_validation.get_peer_ip", return_value="93.184.216.34")
    @patch("email_validation.requests.Session")
    @patch("email_validation.validate_public_url")
    def test_external_redirect_is_rejected(
        self, mock_validate, mock_session, mock_peer
    ):
        from urllib.parse import urlsplit

        mock_validate.side_effect = [
            (urlsplit("https://example.com/contact"), "example.com", ("93.184.216.34",)),
            (urlsplit("https://evil.org/steal"), "evil.org", ("93.184.216.34",)),
        ]

        session = self.make_session(
            self.make_response(302, "https://evil.org/steal")
        )
        mock_session.return_value = session

        with self.assertRaises(ValueError):
            email_validation.validate_email_source(
                "contact@example.com",
                "https://example.com/contact",
                "https://example.com",
                "website_verified",
            )

        self.assertEqual(session.get.call_count, 1)

    @patch("email_validation.get_peer_ip", return_value="93.184.216.34")
    @patch("email_validation.requests.Session")
    @patch("email_validation.validate_public_url")
    def test_private_redirect_is_rejected(
        self, mock_validate, mock_session, mock_peer
    ):
        from urllib.parse import urlsplit

        mock_validate.side_effect = [
            (urlsplit("https://example.com/contact"), "example.com", ("93.184.216.34",)),
            ValueError("Adresse DNS non publique ou réservée"),
        ]

        session = self.make_session(
            self.make_response(302, "https://example.com/internal")
        )
        mock_session.return_value = session

        with self.assertRaises(ValueError):
            email_validation.validate_email_source(
                "contact@example.com",
                "https://example.com/contact",
                "https://example.com",
                "website_verified",
            )

        self.assertEqual(session.get.call_count, 1)

    @patch("email_validation.get_peer_ip", return_value="93.184.216.34")
    @patch("email_validation.requests.Session")
    @patch("email_validation.validate_public_url")
    def test_same_official_domain_redirect_is_followed(
        self, mock_validate, mock_session, mock_peer
    ):
        from urllib.parse import urlsplit

        mock_validate.side_effect = [
            (urlsplit("https://example.com/contact"), "example.com", ("93.184.216.34",)),
            (urlsplit("https://example.com/contact-us"), "example.com", ("93.184.216.34",)),
        ]

        session = self.make_session(
            self.make_response(302, "/contact-us")
        )
        session.get.side_effect = [
            self.make_response(302, "/contact-us"),
            self.make_response(200),
        ]
        mock_session.return_value = session

        result = email_validation.validate_email_source(
            "contact@example.com",
            "https://example.com/contact",
            "https://example.com",
            "website_verified",
        )

        self.assertTrue(result)
        self.assertEqual(session.get.call_count, 2)


class TestResponseSizeLimit(unittest.TestCase):

    def test_small_response_is_accepted(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            headers={"Content-Length": "20"},
            encoding="utf-8",
            iter_content=lambda chunk_size: [b"contact@example.com"],
        )

        result = email_validation.read_limited_response(response)
        self.assertEqual(result, "contact@example.com")

    def test_oversized_content_length_is_rejected(self):
        from types import SimpleNamespace

        response = SimpleNamespace(
            headers={
                "Content-Length": str(email_validation.MAX_RESPONSE_BYTES + 1)
            },
            encoding="utf-8",
            iter_content=lambda chunk_size: [],
        )

        with self.assertRaises(ValueError):
            email_validation.read_limited_response(response)

    def test_oversized_stream_is_rejected(self):
        from types import SimpleNamespace

        limit = email_validation.MAX_RESPONSE_BYTES

        response = SimpleNamespace(
            headers={},
            encoding="utf-8",
            iter_content=lambda chunk_size: [
                b"a" * (limit // 2),
                b"b" * (limit // 2),
                b"c",
            ],
        )

        with self.assertRaises(ValueError):
            email_validation.read_limited_response(response)
\


    class TestPinnedHTTPSConnection(unittest.TestCase):

        @patch("email_validation.urllib3_connection.create_connection")
        def test_tcp_connection_uses_pinned_ip(self, mock_connect):
            fake_socket = object()
            mock_connect.return_value = fake_socket

            connection = email_validation.PinnedHTTPSConnection(
                host="example.com",
                port=443,
                pinned_ip="93.184.216.34",
            )

            result = connection._new_conn()

            self.assertIs(result, fake_socket)
            self.assertEqual(connection.host, "example.com")
            self.assertEqual(connection._pinned_ip, "93.184.216.34")
            self.assertEqual(
                mock_connect.call_args.args[0],
                ("93.184.216.34", 443),
            )

        def test_private_pinned_ip_is_rejected(self):
            with self.assertRaises(ValueError):
                email_validation.PinnedHTTPSConnection(
                    host="example.com",
                    port=443,
                    pinned_ip="127.0.0.1",
                )

        def test_private_adapter_ip_is_rejected(self):
            with self.assertRaises(ValueError):
                email_validation.PinnedHTTPSAdapter("192.168.1.10")


if __name__ == "__main__":
    unittest.main(verbosity=2)
