import unittest

import search.la_bonne_alternance as lba


class TestLBAScoring(unittest.TestCase):

    def test_developer_web_gets_development_points(self):
        score = lba.calculate_score(
            matched_skills=[],
            title="Développeur web en alternance",
            description=(
                "Développement logiciel, API et base de données."
            ),
            activity_score=0,
        )

        self.assertEqual(score, 35)

    def test_it_project_management_gets_limited_it_points(self):
        score = lba.calculate_score(
            matched_skills=[],
            title="Assistant gestion de projet informatique en alternance H/F",
            description=(
                "Pilotage de projet informatique et coordination "
                "des équipes."
            ),
            activity_score=0,
        )

        self.assertEqual(score, 15)

    def test_it_technician_is_not_automatically_a_developer(self):
        score = lba.calculate_score(
            matched_skills=[],
            title="Technicien informatique en alternance",
            description="Maintenance des postes de travail.",
            activity_score=0,
        )

        self.assertEqual(score, 15)

    def test_it_support_gets_limited_it_points(self):
        score = lba.calculate_score(
            matched_skills=[],
            title="Technicien support informatique en alternance",
            description="Assistance aux utilisateurs.",
            activity_score=0,
        )

        self.assertEqual(score, 15)

    def test_unrelated_job_gets_no_points(self):
        score = lba.calculate_score(
            matched_skills=[],
            title="Assistant administratif",
            description="Gestion et classement des dossiers.",
            activity_score=0,
        )

        self.assertEqual(score, 0)

    def test_matched_skills_are_limited_to_40_points(self):
        score = lba.calculate_score(
            matched_skills=["Go", "Python", "JavaScript", "HTML", "CSS"],
            title="Développeur en alternance",
            description="Développement logiciel.",
            activity_score=0,
        )

        self.assertEqual(score, 65)

    def test_activity_score_is_limited_to_25_points(self):
        score = lba.calculate_score(
            matched_skills=[],
            title="Assistant administratif",
            description="Gestion de dossiers.",
            activity_score=40,
        )

        self.assertEqual(score, 25)


if __name__ == "__main__":
    unittest.main()
