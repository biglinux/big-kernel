"""big-secureboot follows the grub and the theme fonts its GRUB is built from.

Run from the repository root: python3 -m unittest discover -s .github/scripts
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import bump_secureboot as bump  # noqa: E402
import watch_manjaro as watch  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def repositories(by_url):
    """Every repository the script reads; the ones not given are empty."""
    def fetch(url):
        if url not in bump.SEARCH:
            raise AssertionError(f"unexpected repository {url}")
        return by_url.get(url, {})
    return fetch


class Bump(unittest.TestCase):
    def setUp(self):
        watch.log = lambda _message: None
        self.root = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.root, watch.SECUREBOOT))
        shutil.copy(os.path.join(ROOT, bump.PKGBUILD), os.path.join(self.root, bump.PKGBUILD))
        self.addCleanup(shutil.rmtree, self.root)
        with open(os.path.join(self.root, bump.PKGBUILD), encoding="utf-8") as source:
            self.text = source.read()
        self.recorded = bump.built_with(self.text)
        self.pkgrel = int(watch.text_value(self.text, "pkgrel", "PKGBUILD"))

    def as_recorded(self):
        """The repositories with what the PKGBUILD recorded: grub in core, themes in ours."""
        core, ours = {}, {}
        for package, version in self.recorded.items():
            (core if package == "grub" else ours)[package] = version
        return {bump.SEARCH[0]: core, watch.BIGLINUX_DB.format(branch="stable"): ours}

    def run_bump(self, by_url):
        result = bump.bump(self.root, repositories(by_url))
        with open(os.path.join(self.root, bump.PKGBUILD), encoding="utf-8") as source:
            return result, source.read()

    def test_it_records_grub_and_both_themes(self):
        self.assertEqual(set(self.recorded), {"grub", "grub-theme-biglinux", "grub-theme-community"})

    def test_nothing_changes_while_the_repositories_match(self):
        result, text = self.run_bump(self.as_recorded())
        self.assertEqual(result["status"], "current")
        self.assertEqual(text, self.text)

    def test_a_new_grub_raises_pkgrel_and_is_recorded(self):
        repos = self.as_recorded()
        repos[bump.SEARCH[0]] = {"grub": "2:2.18-1"}
        result, text = self.run_bump(repos)
        self.assertEqual(result["status"], "bumped")
        self.assertEqual(result["changed"], {"grub": (self.recorded["grub"], "2:2.18-1")})
        self.assertEqual(int(watch.text_value(text, "pkgrel", "PKGBUILD")), self.pkgrel + 1)
        self.assertEqual(bump.built_with(text)["grub"], "2:2.18-1")
        # Only those two lines change.
        changed = [line for line in text.splitlines() if line not in self.text.splitlines()]
        self.assertEqual(len(changed), 2)

    def test_the_first_repository_with_it_wins_like_pacman(self):
        repos = self.as_recorded()
        community_testing = bump.COMMUNITY.format(branch="testing")
        repos[community_testing] = {"grub-theme-biglinux": "27.01.01-1"}
        result, _text = self.run_bump(repos)
        # The theme in BigLinux stable is shadowed by community-testing, ahead of it.
        self.assertEqual(result["changed"], {"grub-theme-biglinux": (self.recorded["grub-theme-biglinux"], "27.01.01-1")})

    def test_manjaro_comes_before_the_repositories_build_package_appends(self):
        self.assertEqual(bump.SEARCH[:2], [bump.MANJARO.format(repo="core"), bump.MANJARO.format(repo="extra")])

    def test_a_package_gone_from_every_repository_stops_it(self):
        repos = self.as_recorded()
        repos[watch.BIGLINUX_DB.format(branch="stable")] = {"grub-theme-biglinux": "26.10.31-1730"}
        with self.assertRaises(SystemExit):
            self.run_bump(repos)

    def test_the_bumped_pkgbuild_is_planned_for_testing(self):
        repos = self.as_recorded()
        repos[bump.SEARCH[0]] = {"grub": "2:2.18-1"}
        result, _text = self.run_bump(repos)
        testing = {watch.SECUREBOOT: result["from"]}

        def databases(url):
            return testing if url == watch.BIGLINUX_DB.format(branch="testing") else {}

        self.assertEqual(watch.plan_secureboot(self.root, databases),
                         [("testing", "stable", watch.SECUREBOOT, result["to"])])


if __name__ == "__main__":
    unittest.main()
