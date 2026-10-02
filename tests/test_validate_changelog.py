import os
import subprocess
import tempfile
import textwrap
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(ROOT, "scripts-cicd", "validate_changelog.sh")

SAMPLE = textwrap.dedent(
    """\
    # CHANGELOG.md - Proceso de comunicaciones para las etiquetas y prompt de comandos

    ---

    ## Versión: [COM-LABELS] v1.0.2 - 18/09/2026

    ### 🚀 Nuevas Funcionalidades
    * [ETI-10] - Mejorar la consulta de estado de etiquetas

    ### 🐛 Corrección de Errores
    * [ETI-15] - Lecturas CLI ven commits nuevos

    ### 🛠️ Mejoras

    ---

    ## Versión: [COM-LABELS] v1.0.1 - 15/09/2026

    ### 🚀 Nuevas Funcionalidades
    * [ETI-1] - Estructura básica.

    ### 🐛 Corrección de Errores

    ### 🛠️ Mejoras

    ---
    """
)


def run_validator(changelog_text, today):
    env = os.environ.copy()
    env["TZ"] = "Europe/Madrid"
    env["CHANGELOG_TZ"] = "Europe/Madrid"
    env["CHANGELOG_TODAY"] = today
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
        fh.write(changelog_text)
        path = fh.name
    try:
        return subprocess.run(
            ["sh", SCRIPT, path],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.unlink(path)


class ChangelogShellValidationTests(unittest.TestCase):
    def test_ok_when_date_is_today_and_jira_present(self):
        result = run_validator(SAMPLE, "18/09/2026")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CHANGELOG OK", result.stdout)
        self.assertIn("ETI-10", result.stdout)

    def test_fails_when_date_is_not_today(self):
        result = run_validator(SAMPLE, "19/09/2026")
        self.assertEqual(result.returncode, 1)
        self.assertIn("18/09/2026", result.stdout)
        self.assertIn("19/09/2026", result.stdout)

    def test_fails_when_no_jira_in_tracked_sections(self):
        no_jira = SAMPLE.replace("[ETI-10]", "sin id").replace("[ETI-15]", "sin id")
        result = run_validator(no_jira, "18/09/2026")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Jira", result.stdout)

    def test_jira_in_mejoras_only_is_enough(self):
        only_mejoras = SAMPLE.replace("[ETI-10]", "sin ticket").replace("[ETI-15]", "sin ticket")
        only_mejoras = only_mejoras.replace(
            "### 🛠️ Mejoras\n\n---",
            "### 🛠️ Mejoras\n* [ETI-99] - nota\n\n---",
            1,
        )
        result = run_validator(only_mejoras, "18/09/2026")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ETI-99", result.stdout)

    def test_older_version_jira_is_ignored(self):
        no_top_jira = SAMPLE.replace("[ETI-10]", "sin id").replace("[ETI-15]", "sin id")
        result = run_validator(no_top_jira, "18/09/2026")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("ETI-1", result.stdout.split("Jira:")[-1] if "Jira:" in result.stdout else "")


if __name__ == "__main__":
    unittest.main()
