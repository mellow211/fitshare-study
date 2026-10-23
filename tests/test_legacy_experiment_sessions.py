import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LegacyExperimentSessionTests(unittest.TestCase):
    def test_saved_browser_experiment_is_added_when_server_list_has_no_copy(self):
        store = (ROOT / 'js' / 'measurement' / 'store.js').read_text(encoding='utf-8')
        self.assertIn('localSession = await loadSession()', store)
        self.assertIn("id: 'local'", store)
        self.assertIn('localSession.serverId', store)

    def test_local_experiment_can_open_from_the_sessions_page(self):
        sessions = (ROOT / 'js' / 'measurement' / 'sessions.js').read_text(encoding='utf-8')
        results = (ROOT / 'js' / 'measurement' / 'results.js').read_text(encoding='utf-8')
        self.assertIn("href=\"index.html?run=${encodeURIComponent(session.id)}\"", sessions)
        self.assertIn("selectedRun === 'local'", results)
        self.assertIn('? loadSession()', results)


if __name__ == '__main__':
    unittest.main()
