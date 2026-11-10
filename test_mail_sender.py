import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import database
import mail_sender


class TestSpontaneousMailSender(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cv = Path(self.temp_dir.name) / "cv.pdf"
        self.cv.write_bytes(b"%PDF-1.4 fake test attachment")

        self.status = "draft"
        self.application = {
            "id": 999999,
            "status": "draft",
            "recipient_email": "contact@example.com",
            "email_source_url": "https://example.com/contact",
            "official_website": "https://example.com",
            "verification_status": "website_verified",
            "subject": "Test simulé",
            "message": "Message de test.",
        }

        self.smtp = MagicMock()
        self.smtp.__enter__.return_value = self.smtp
        self.smtp.__exit__.return_value = False

        self.patches = [
            patch.object(database, "get_application",
                          return_value=self.application),
            patch.object(database, "transition_application_status",
                         side_effect=self.transition),
            patch.object(mail_sender, "is_email_blocked",
                         return_value=False),
            patch.object(mail_sender, "validate_email_source"),
            patch.object(mail_sender, "SMTP_APP_PASSWORD",
                         "mot-de-passe-factice"),
            patch.object(mail_sender, "find_cv", return_value=self.cv),
            patch("mail_sender.smtplib.SMTP", return_value=self.smtp),
        ]

        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp_dir.cleanup()

    def transition(self, application_id, expected, new):
        if self.status != expected:
            return False
        self.status = new
        return True

    def test_success_records_smtp_accepted_and_attachment(self):
        mail_sender.send_spontaneous_application(999999)

        self.assertEqual(self.status, "smtp_accepted")
        message = self.smtp.send_message.call_args.args[0]
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "cv.pdf")

    def test_login_failure_records_failed(self):
        self.smtp.login.side_effect = RuntimeError("login refusé")

        with self.assertRaises(RuntimeError):
            mail_sender.send_spontaneous_application(999999)

        self.assertEqual(self.status, "failed")
        self.smtp.send_message.assert_not_called()

    def test_send_message_failure_records_delivery_unknown(self):
        self.smtp.send_message.side_effect = RuntimeError("réponse perdue")

        with self.assertRaises(RuntimeError):
            mail_sender.send_spontaneous_application(999999)

        self.assertEqual(self.status, "delivery_unknown")

    def test_smtp_close_failure_keeps_accepted_status(self):
        self.smtp.__exit__.side_effect = RuntimeError("fermeture échouée")

        mail_sender.send_spontaneous_application(999999)

        self.assertEqual(self.status, "smtp_accepted")
        self.smtp.send_message.assert_called_once()

    def test_non_draft_application_never_connects(self):
        self.application["status"] = "smtp_accepted"

        with self.assertRaises(ValueError):
            mail_sender.send_spontaneous_application(999999)

        self.mocks[-1].assert_not_called()

    def test_blocked_address_never_connects(self):
        self.mocks[2].return_value = True

        with self.assertRaises(ValueError):
            mail_sender.send_spontaneous_application(999999)

        self.mocks[-1].assert_not_called()


class TestOfferMailSender(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cv = Path(self.temp_dir.name) / "cv.pdf"
        self.cv.write_bytes(b"%PDF-1.4 fake test attachment")

        self.status = "approved"
        self.offer = {
            "id": 888888,
            "status": "approved",
            "title": "Développeur en alternance",
            "contact_email": "recrutement@example.com",
            "tailored_message": "Bonjour, voici ma candidature.",
        }

        self.smtp = MagicMock()
        self.smtp.__enter__.return_value = self.smtp
        self.smtp.__exit__.return_value = False

        self.patches = [
            patch.object(mail_sender, "get_offer", return_value=self.offer),
            patch.object(mail_sender, "is_published_offer", return_value=True),
            patch.object(mail_sender, "is_email_blocked", return_value=False),
            patch.object(mail_sender, "valid_email_format", return_value=True),
            patch.object(mail_sender, "transition_offer_status",
                         side_effect=self.transition),
            patch.object(mail_sender, "SMTP_APP_PASSWORD", "mot-de-passe-factice"),
            patch.object(mail_sender, "find_cv", return_value=self.cv),
            patch("mail_sender.smtplib.SMTP", return_value=self.smtp),
        ]

        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp_dir.cleanup()

    def transition(self, offer_id, expected, new):
        if self.status != expected:
            return False
        self.status = new
        return True

    def test_success_records_smtp_accepted_and_attachment(self):
        mail_sender.send_offer(888888)

        self.assertEqual(self.status, "smtp_accepted")
        message = self.smtp.send_message.call_args.args[0]
        self.assertEqual(message["To"], "recrutement@example.com")
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "cv.pdf")

    def test_login_failure_records_failed(self):
        self.smtp.login.side_effect = RuntimeError("login refusé")

        with self.assertRaises(RuntimeError):
            mail_sender.send_offer(888888)

        self.assertEqual(self.status, "failed")
        self.smtp.send_message.assert_not_called()

    def test_send_message_failure_records_delivery_unknown(self):
        self.smtp.send_message.side_effect = RuntimeError("réponse perdue")

        with self.assertRaises(RuntimeError):
            mail_sender.send_offer(888888)

        self.assertEqual(self.status, "delivery_unknown")

    def test_blocked_address_never_connects(self):
        self.mocks[2].return_value = True

        with self.assertRaises(ValueError):
            mail_sender.send_offer(888888)

        self.mocks[-1].assert_not_called()

    def test_invalid_email_never_connects(self):
        self.mocks[3].return_value = False

        with self.assertRaises(ValueError):
            mail_sender.send_offer(888888)

        self.mocks[-1].assert_not_called()

    def test_non_approved_offer_never_connects(self):
        self.offer["status"] = "rejected"

        with self.assertRaises(ValueError):
            mail_sender.send_offer(888888)

        self.mocks[-1].assert_not_called()

    def test_failed_reservation_never_connects(self):
        self.mocks[4].side_effect = lambda *_: False

        with self.assertRaises(ValueError):
            mail_sender.send_offer(888888)

        self.mocks[-1].assert_not_called()

    def test_smtp_accepted_transition_failure_becomes_unknown(self):
        calls = []

        def transition(offer_id, expected, new):
            calls.append((expected, new))
            if expected == "approved" and new == "sending":
                self.status = "sending"
                return True
            if expected == "sending" and new == "smtp_accepted":
                return False
            if expected == "sending" and new == "delivery_unknown":
                self.status = "delivery_unknown"
                return True
            return False

        self.mocks[4].side_effect = transition

        with self.assertRaises(RuntimeError):
            mail_sender.send_offer(888888)

        self.assertEqual(self.status, "delivery_unknown")
        self.smtp.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
