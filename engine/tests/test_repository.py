# SPDX-License-Identifier: MIT
import copy
from pathlib import Path
import tempfile
import unittest
from engine.server.store import Store, Conflict, authoring_digest, safe_path

class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = Store(self.root / 'Projects')
    def test_conditional_save_preserves_newer_bytes_and_non_english_text(self):
        slug, initial = self.store.create('Norsk åpning')
        draft = copy.deepcopy(initial); draft['sceneDescription'] = 'Hun åpner døren.'
        saved = self.store.put(slug, draft, expected_digest=authoring_digest(initial))
        before = (self.store.project_dir(slug) / 'storyboard.json').read_bytes()
        with self.assertRaises(Conflict): self.store.put(slug, initial, expected_digest=authoring_digest(initial))
        self.assertEqual(before, (self.store.project_dir(slug) / 'storyboard.json').read_bytes())
        self.assertEqual(saved['sceneDescription'], 'Hun åpner døren.')
    def test_media_service_never_resolves_private_documents_or_symlinks(self):
        slug, _ = self.store.create('Safe')
        for ref in ('../secret.png', '/etc/passwd', f'/media/{slug}/storyboard.json', f'/media/{slug}/%2e%2e/secret.png'):
            with self.subTest(ref=ref), self.assertRaises((ValueError, FileNotFoundError)): self.store.media_path(ref)
        path = self.store.project_dir(slug) / 'refs/private.png'; path.symlink_to(self.root / 'elsewhere.png')
        with self.assertRaises(ValueError): self.store.media_path(f'{slug}/refs/private.png')
    def test_progress_does_not_invalidate_authoring_and_rename_preserves_id(self):
        slug, board = self.store.create('One')
        modified = dict(board, progress=.7)
        self.assertEqual(authoring_digest(board), authoring_digest(modified))
        self.assertEqual(self.store.rename(slug, 'A new name')[0], slug)
        self.assertEqual(self.store.list()[0]['slug'], slug)
    def test_unique_creation_never_overwrites_an_existing_project(self):
        first, _ = self.store.create('Film'); second, _ = self.store.create('Film')
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.store.list()), 2)
