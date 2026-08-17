#!/usr/bin/env python3
"""Project-identity tests: how a memory's `project` label is derived.

Stdlib only, like the rest of the project. Builds a throwaway git repo with a
linked worktree in a temp dir — no network, no daemon, and no touch of the real
brain.db (nothing under test opens the database).

    python3 tests/test_project_id.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brain  # noqa: E402

REMOTE = "git@github.com:acme/widget-api.git"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class ProjectIdTest(unittest.TestCase):
    """Each mode is exercised against a real repo + a real linked worktree."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        # Resolved: on macOS the temp dir is under a /var -> /private/var symlink,
        # and git reports the real path.
        base = Path(cls._tmp.name).resolve()

        cls.repo = base / "widget-api"
        cls.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=cls.repo)
        git("config", "user.email", "test@example.com", cwd=cls.repo)
        git("config", "user.name", "Test", cwd=cls.repo)
        git("remote", "add", "origin", REMOTE, cwd=cls.repo)
        (cls.repo / "README").write_text("x\n")
        git("add", "-A", cwd=cls.repo)
        git("commit", "-qm", "init", cwd=cls.repo)

        cls.subdir = cls.repo / "src" / "deep"
        cls.subdir.mkdir(parents=True)

        # A linked worktree named for a ticket — the case that motivated this.
        cls.worktree = base / "worktrees" / "tkt-123"
        git("worktree", "add", "-q", "-b", "tkt-123", str(cls.worktree), cwd=cls.repo)

        cls.plain = base / "not-a-repo"
        cls.plain.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        # Point config at a path that cannot exist, so a developer's real
        # ~/.claude/brain/config.json can never leak into a test run.
        self._env = dict(os.environ)
        os.environ["CLAUDE_BRAIN_CONFIG"] = str(Path(self._tmp.name) / "no-config.json")
        os.environ.pop("CLAUDE_BRAIN_PROJECT_ID", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def label(self, cwd, mode=None):
        if mode:
            os.environ["CLAUDE_BRAIN_PROJECT_ID"] = mode
        return brain._repo_meta(str(cwd))[0]

    # -- default: unchanged behaviour ------------------------------------

    def test_basename_is_the_default(self):
        self.assertEqual(brain._project_id_mode(), "basename")

    def test_basename_uses_the_git_root_folder(self):
        self.assertEqual(self.label(self.repo), "widget-api")

    def test_basename_from_a_subdirectory_still_uses_the_git_root(self):
        self.assertEqual(self.label(self.subdir), "widget-api")

    def test_basename_labels_a_worktree_by_its_own_folder(self):
        # Documents the pre-existing behaviour this PR must not change.
        self.assertEqual(self.label(self.worktree), "tkt-123")

    def test_unknown_mode_falls_back_to_the_default(self):
        os.environ["CLAUDE_BRAIN_PROJECT_ID"] = "nonsense"
        self.assertEqual(brain._project_id_mode(), "basename")
        self.assertEqual(self.label(self.worktree), "tkt-123")

    # -- worktree mode ---------------------------------------------------

    def test_worktree_mode_collapses_a_worktree_onto_the_main_checkout(self):
        self.assertEqual(self.label(self.worktree, "worktree"), "widget-api")

    def test_worktree_mode_is_a_no_op_in_a_plain_checkout(self):
        self.assertEqual(self.label(self.repo, "worktree"),
                         self.label(self.repo, "basename"))

    def test_worktree_mode_is_a_no_op_in_a_subdirectory(self):
        self.assertEqual(self.label(self.subdir, "worktree"), "widget-api")

    def test_worktree_mode_falls_back_outside_a_repo(self):
        self.assertEqual(self.label(self.plain, "worktree"), "not-a-repo")

    # -- remote mode -----------------------------------------------------

    def test_remote_mode_uses_owner_repo(self):
        self.assertEqual(self.label(self.repo, "remote"), "acme/widget-api")

    def test_remote_mode_agrees_across_a_worktree(self):
        self.assertEqual(self.label(self.worktree, "remote"), "acme/widget-api")

    def test_remote_mode_falls_back_when_there_is_no_origin(self):
        git("remote", "remove", "origin", cwd=self.repo)
        try:
            self.assertEqual(self.label(self.repo, "remote"), "widget-api")
        finally:
            git("remote", "add", "origin", REMOTE, cwd=self.repo)

    # -- provenance ------------------------------------------------------

    def test_origin_is_recoverable_from_a_worktree_in_every_mode(self):
        for mode in brain.PROJECT_ID_MODES:
            with self.subTest(mode=mode):
                _, root, remote, main_root = brain._repo_meta(str(self.worktree))
                self.assertEqual(Path(root), self.worktree)
                self.assertEqual(remote, "acme/widget-api")
                self.assertEqual(Path(main_root), self.repo)

    # -- config file -----------------------------------------------------

    def test_config_file_selects_the_mode(self):
        cfg = Path(self._tmp.name) / "config.json"
        cfg.write_text('{"project_id": "worktree"}')
        os.environ["CLAUDE_BRAIN_CONFIG"] = str(cfg)
        self.assertEqual(brain._project_id_mode(), "worktree")
        self.assertEqual(brain._repo_meta(str(self.worktree))[0], "widget-api")

    def test_env_overrides_the_config_file(self):
        cfg = Path(self._tmp.name) / "config.json"
        cfg.write_text('{"project_id": "remote"}')
        os.environ["CLAUDE_BRAIN_CONFIG"] = str(cfg)
        os.environ["CLAUDE_BRAIN_PROJECT_ID"] = "basename"
        self.assertEqual(brain._project_id_mode(), "basename")

    def test_a_malformed_config_is_ignored(self):
        cfg = Path(self._tmp.name) / "broken.json"
        cfg.write_text("{not json")
        os.environ["CLAUDE_BRAIN_CONFIG"] = str(cfg)
        self.assertEqual(brain._config(), {})
        self.assertEqual(brain._project_id_mode(), "basename")


if __name__ == "__main__":
    unittest.main(verbosity=2)
