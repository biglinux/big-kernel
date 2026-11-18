"""Scenarios the kernel watcher must get right, on a miniature kernel.

Run from the repository root: python3 -m unittest discover -s .github/scripts
"""

import difflib
import hashlib
import io
import json
import lzma
import os
import re
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import bump_kernel as bump  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# The current series: the newest linux-bigNN directory.
CURRENT = max((d for d in os.listdir(ROOT) if re.fullmatch(r"linux-big[0-9]+", d)), key=lambda d: int(d[9:]))
REAL_PKGBUILD = open(os.path.join(ROOT, CURRENT, "PKGBUILD"), encoding="utf-8").read()


def releases(*entries):
    return {"releases": [{"moniker": m, "version": v, "iseol": eol} for m, v, eol in entries]}


KERNEL_ORG = releases(
    ("mainline", "7.3-rc4", False),
    ("stable", "7.2.9", False),
    ("stable", "7.1.13", True),
    ("longterm", "6.18.53", False),
)


class Series(unittest.TestCase):
    def test_the_newest_release_of_the_series(self):
        self.assertEqual(bump.latest_in_series(KERNEL_ORG, "7.2"), ("7.2.9", False))

    def test_a_series_marked_end_of_life(self):
        self.assertEqual(bump.latest_in_series(KERNEL_ORG, "7.1"), ("7.1.13", True))

    def test_a_series_no_longer_listed_is_end_of_life(self):
        self.assertEqual(bump.latest_in_series(KERNEL_ORG, "7.0"), (None, True))

    def test_7_2_is_not_7_20(self):
        self.assertEqual(bump.latest_in_series(releases(("stable", "7.20.1", False)), "7.2"), (None, True))

    def test_a_new_series_before_its_first_stable_release(self):
        listed = releases(("mainline", "7.3", False), ("stable", "7.2.9", False))
        self.assertEqual(bump.latest_in_series(listed, "7.3"), ("7.3", False))

    def test_release_candidates_do_not_count(self):
        self.assertEqual(bump.latest_in_series(KERNEL_ORG, "7.3"), (None, True))

    def test_versions_compare_as_numbers(self):
        self.assertGreater(bump.version_key("7.2.10"), bump.version_key("7.2.9"))


class RealPkgbuild(unittest.TestCase):
    """The edits, on the PKGBUILD the watcher will actually change."""

    def test_the_stable_patch_is_the_second_source(self):
        self.assertEqual(bump.patch_index(REAL_PKGBUILD), 1)

    def test_every_source_has_a_checksum(self):
        self.assertEqual(len(bump.source_entries(REAL_PKGBUILD)), len(bump.checksums(REAL_PKGBUILD)))

    def test_bump_changes_only_version_release_and_patch_checksum(self):
        # Whatever pkgrel is: the watcher's own bump leaves it at 1, and the
        # next bump must not be judged by how many lines changed.
        import re
        for pkgrel in (bump.pkgbuild_value(REAL_PKGBUILD, "pkgrel"), "1", "4"):
            with self.subTest(pkgrel=pkgrel):
                old = re.sub(r"^pkgrel=.*$", f"pkgrel={pkgrel}", REAL_PKGBUILD, count=1, flags=re.M)
                new = bump.bump(old, "7.2.99", "a" * 64)
                self.assertEqual(bump.pkgbuild_value(new, "pkgver"), "7.2.99")
                self.assertEqual(bump.pkgbuild_value(new, "pkgrel"), "1")
                old_sums, new_sums = bump.checksums(old), bump.checksums(new)
                self.assertEqual(new_sums[1], "a" * 64)
                self.assertEqual(new_sums[:1] + new_sums[2:], old_sums[:1] + old_sums[2:])
                self.assertEqual(bump.source_entries(new), bump.source_entries(old))
                # Nothing but those three lines differs.
                allowed = {"pkgver=7.2.99", "pkgrel=1", "            '" + "a" * 64 + "'"}
                changed = [n for o, n in zip(old.splitlines(), new.splitlines()) if o != n]
                self.assertLessEqual(set(changed), allowed, changed)
                self.assertEqual(len(old.splitlines()), len(new.splitlines()))

    def test_drop_removes_the_patch_and_its_own_checksum(self):
        entries, sums = bump.source_entries(REAL_PKGBUILD), bump.checksums(REAL_PKGBUILD)
        name = "0000-iwlwifi-fix.patch"
        index = entries.index(name)
        new = bump.drop_patch(REAL_PKGBUILD, name)
        self.assertEqual(bump.source_entries(new), entries[:index] + entries[index + 1 :])
        self.assertEqual(bump.checksums(new), sums[:index] + sums[index + 1 :])
        self.assertIn("# BigLinux: Intel display and Xe fixes", new)


class Failure(unittest.TestCase):
    def test_names_the_patch_that_failed(self):
        output = "\n".join(
            [
                "Applying patch 0001-a.patch...",
                "patching file drivers/a.c",
                "Applying patch 0002-b.patch...",
                "patching file drivers/b.c",
                "Hunk #1 FAILED at 10.",
                "1 out of 1 hunk FAILED -- saving rejects to file drivers/b.c.rej",
                "==> ERROR: A failure occurred in prepare().",
            ]
        )
        reasons = bump.failure_reasons(output)
        self.assertEqual(reasons[0], "Applying patch 0002-b.patch...")
        self.assertNotIn("Applying patch 0001-a.patch...", reasons)
        self.assertIn("Hunk #1 FAILED at 10.", reasons)

    def test_names_a_lost_option(self):
        reasons = bump.failure_reasons("==> ERROR: These options did not survive the configuration: CONFIG_SCHED_BORE=y")
        self.assertIn("CONFIG_SCHED_BORE=y", reasons[0])


PKGBUILD = """\
_basekernel=7.2
pkgbase=linux-big72
pkgver=7.2.6
pkgrel=3
source=(https://cdn.kernel.org/pub/linux/kernel/v7.x/linux-${_basekernel}.tar.xz
        https://cdn.kernel.org/pub/linux/kernel/v7.x/patch-${pkgver}.xz
        config
        # ours
%s
)
sha256sums=(%s)
"""

A = ["old a1\n", "old a2\n", "old a3\n"]
A_OURS = ["ours a1\n", "ours a2\n", "ours a3\n"]
B, B_FIXED = ["old b\n"], ["fixed b\n"]


def diff(path, before, after):
    """A real unified diff, three lines of context, as git writes them."""
    return "".join(difflib.unified_diff(before, after, f"a/{path}", f"b/{path}"))


class Main(unittest.TestCase):
    """A whole run, on a kernel of two files, without makepkg.

    0002 is written on top of 0001: every line of its context is one 0001
    changed, so it fits the tree only once 0001 is applied. 0003 is what
    7.2.9 fixed. Only by applying the series in order, as prepare() does,
    does the watcher get past 0002 and find 0003 already in the kernel.
    """

    def setUp(self):
        bump.log = lambda _message: None
        self.work = tempfile.TemporaryDirectory()
        base = self.work.name
        self.root, self.cache = os.path.join(base, "repo"), os.path.join(base, "cache")
        self.kernel = os.path.join(self.root, "linux-big72")
        os.makedirs(self.kernel)
        os.makedirs(self.cache)
        with tarfile.open(os.path.join(self.cache, "linux-7.2.tar.xz"), "w:xz") as archive:
            for name, lines in (("a.c", A), ("b.c", B)):
                data = "".join(lines).encode()
                info = tarfile.TarInfo(f"linux-7.2/{name}")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        with lzma.open(os.path.join(self.cache, "patch-7.2.9.xz"), "wb") as out:
            out.write(diff("b.c", B, B_FIXED).encode())
        self.patch_sha = hashlib.sha256(open(os.path.join(self.cache, "patch-7.2.9.xz"), "rb").read()).hexdigest()
        self.releases = os.path.join(base, "releases.json")
        self.result = os.path.join(base, "result.json")

    def tearDown(self):
        self.work.cleanup()

    def series(self, patches):
        for name, text in patches:
            open(os.path.join(self.kernel, name), "w").write(text)
        self.sums = ["1" * 64, "2" * 64, "3" * 64] + [c * 64 for c in "456789"[: len(patches)]]
        entries = "\n".join(f"        {name}" for name, _text in patches)
        sums = "\n            ".join(f"'{s}'" for s in self.sums)
        open(os.path.join(self.kernel, "PKGBUILD"), "w").write(PKGBUILD % (entries, sums))

    def run_watcher(self, listed):
        json.dump(listed, open(self.releases, "w"))
        argv = ["bump_kernel.py", "--root", self.root, "--kernel", "linux-big72", "--cache", self.cache,
                "--releases", self.releases, "--result", self.result, "--no-prepare"]
        old, sys.argv = sys.argv, argv
        try:
            self.assertEqual(bump.main(), 0)
        finally:
            sys.argv = old
        return json.load(open(self.result))

    def pkgbuild(self):
        return open(os.path.join(self.kernel, "PKGBUILD")).read()

    def test_a_patch_already_in_the_release_is_found_behind_a_stacked_one(self):
        self.series([
            ("0001-ours.patch", diff("a.c", A, A_OURS)),
            ("0002-on-top.patch", diff("a.c", A_OURS, A_OURS + ["more a\n"])),
            ("0003-merged.patch", diff("b.c", B, B_FIXED)),
        ])
        result = self.run_watcher(KERNEL_ORG)
        self.assertEqual((result["status"], result["from"], result["to"]), ("bumped", "7.2.6", "7.2.9"))
        self.assertEqual(result["dropped"], ["0003-merged.patch"])
        text = self.pkgbuild()
        self.assertEqual(bump.pkgbuild_value(text, "pkgver"), "7.2.9")
        self.assertEqual(bump.pkgbuild_value(text, "pkgrel"), "1")
        self.assertEqual(bump.source_entries(text)[3:], ["0001-ours.patch", "0002-on-top.patch"])
        self.assertEqual(bump.checksums(text), [self.sums[0], self.patch_sha, self.sums[2], self.sums[3], self.sums[4]])
        self.assertFalse(os.path.exists(os.path.join(self.kernel, "0003-merged.patch")))

    def test_the_stacked_patch_fits_only_on_top_of_its_base(self):
        # What makes the test above meaningful: alone, 0002 fits neither way.
        with tempfile.TemporaryDirectory() as workdir:
            tree = bump.prepare_tree(workdir, os.path.join(self.cache, "linux-7.2.tar.xz"),
                                     os.path.join(self.cache, "patch-7.2.9.xz"))
            patch = os.path.join(workdir, "0002.patch")
            open(patch, "w").write(diff("a.c", A_OURS, A_OURS + ["more a\n"]))
            self.assertFalse(bump.applies(tree, patch))
            self.assertFalse(bump.applies(tree, patch, reverse=True))

    def test_a_patch_that_creates_a_file_and_then_changes_it_is_not_a_stop(self):
        # 0001-steam-deck.patch adds drivers/hwmon/steamdeck-hwmon.c and then
        # changes it further down: patch --dry-run never wrote the file, so
        # it fits neither way there. 7.3.4 and 7.3.5 stopped on it, short of
        # Wa_22016122933, already in the kernel, and prepare() failed.
        creates_then_changes = (
            "--- /dev/null\n+++ b/c.c\n@@ -0,0 +1,3 @@\n+one\n+two\n+three\n"
            "--- a/c.c\n+++ b/c.c\n@@ -1,3 +1,3 @@\n one\n-two\n+TWO\n three\n"
        )
        self.series([
            ("0001-new-file.patch", creates_then_changes),
            ("0002-merged.patch", diff("b.c", B, B_FIXED)),
        ])
        result = self.run_watcher(KERNEL_ORG)
        self.assertEqual(result["dropped"], ["0002-merged.patch"])
        self.assertEqual(bump.source_entries(self.pkgbuild())[3:], ["0001-new-file.patch"])

    def test_a_patch_that_fits_neither_way_stops_the_search(self):
        # 0001 no longer fits: prepare() is left to fail on it and name it,
        # and nothing behind it is dropped on a guess.
        self.series([
            ("0001-broken.patch", diff("a.c", ["other\n"] * 3, ["changed\n"] * 3)),
            ("0002-merged.patch", diff("b.c", B, B_FIXED)),
        ])
        result = self.run_watcher(KERNEL_ORG)
        self.assertEqual(result["dropped"], [])
        self.assertEqual(bump.source_entries(self.pkgbuild())[3:], ["0001-broken.patch", "0002-merged.patch"])

    def test_the_result_names_the_series_package(self):
        self.series([("0001-a.patch", diff("a.c", A, A_OURS))])
        self.assertEqual(self.run_watcher(KERNEL_ORG)["kernel"], "linux-big72")

    def test_only_a_series_directory_is_taken(self):
        for kernel in ("linux-big", "linux-big73-zfs", "../linux-big72"):
            argv = ["bump_kernel.py", "--root", self.root, "--kernel", kernel, "--releases", self.releases]
            old, sys.argv = sys.argv, argv
            try:
                with self.subTest(kernel=kernel), self.assertRaises(SystemExit):
                    bump.main()
            finally:
                sys.argv = old

    def test_nothing_changes_when_current(self):
        self.series([("0001-ours.patch", diff("a.c", A, A_OURS))])
        before = self.pkgbuild()
        self.assertEqual(self.run_watcher(releases(("stable", "7.2.6", False)))["status"], "current")
        self.assertEqual(self.pkgbuild(), before)

    def test_a_current_run_leaves_the_cache_directory(self):
        # The workflow prunes the cache after every run; with nothing to
        # download the directory must exist all the same.
        self.series([("0001-ours.patch", diff("a.c", A, A_OURS))])
        import shutil
        shutil.rmtree(self.cache)
        self.assertEqual(self.run_watcher(releases(("stable", "7.2.6", False)))["status"], "current")
        self.assertTrue(os.path.isdir(self.cache))

    def test_the_last_release_is_taken_even_marked_end_of_life(self):
        # 7.2.15 came out already marked end of life on 2026-11-01, and the
        # watcher reported the end of life without moving to it.
        self.series([("0001-ours.patch", diff("a.c", A, A_OURS))])
        result = self.run_watcher(releases(("stable", "7.2.9", True)))
        self.assertEqual((result["status"], result["to"], result["eol"]), ("bumped", "7.2.9", True))
        self.assertIn("pkgver=7.2.9\n", self.pkgbuild())

    def test_end_of_life_on_the_last_release_is_reported_not_acted_on(self):
        self.series([("0001-ours.patch", diff("a.c", A, A_OURS))])
        before = self.pkgbuild()
        self.assertEqual(self.run_watcher(releases(("stable", "7.2.6", True)))["status"], "eol")
        self.assertEqual(self.pkgbuild(), before)


def bash_array(text, name):
    """The words of a literal bash array assignment, quotes removed and
    comments left out; None when the PKGBUILD has none."""
    match = re.search(rf"^{name}=\(", text, re.M)
    if not match:
        return None
    words, word, quote, i = [], None, None, match.end()
    while i < len(text):
        char = text[i]
        if quote:
            if char == quote:
                quote = None
            else:
                word += char
        elif char in "'\"":
            quote, word = char, word or ""
        elif char == "#" and word is None:
            i = text.find("\n", i)
            continue
        elif char == "$" and text[i + 1 : i + 2] == "(":
            depth = 0
            while i < len(text):
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                word = (word or "") + text[i]
                if depth == 0:
                    break
                i += 1
        elif char.isspace() or char == ")":
            if word is not None:
                words.append(word)
                word = None
            if char == ")":
                return words
        else:
            word = (word or "") + char
        i += 1
    return words


class LocalSources(unittest.TestCase):
    """Every file a PKGBUILD lists from its own directory matches its checksum.

    makepkg -e skips the check, and a build that reuses its tree never sees
    a wrong one: on 2026-11-05 linux-big73 reached the CI with the checksum
    of the config before it was resolved, and failed there.
    """

    def test_every_local_source_matches_its_checksum(self):
        checked = 0
        for directory in sorted(os.listdir(ROOT)):
            pkgbuild = os.path.join(ROOT, directory, "PKGBUILD")
            if not directory.startswith("linux-big") or not os.path.isfile(pkgbuild):
                continue
            with open(pkgbuild, encoding="utf-8") as source:
                text = source.read()
            entries, sums = bash_array(text, "source"), bash_array(text, "sha256sums")
            if entries is None or sums is None:
                continue
            for entry, expected in zip(entries, sums):
                if "://" in entry or "$" in entry or expected == "SKIP":
                    continue
                path = os.path.join(ROOT, directory, entry)
                with self.subTest(file=f"{directory}/{entry}"):
                    self.assertTrue(os.path.isfile(path), "listed in source=() but missing")
                    with open(path, "rb") as data:
                        self.assertEqual(hashlib.sha256(data.read()).hexdigest(), expected)
                    checked += 1
        self.assertGreater(checked, 40)


if __name__ == "__main__":
    unittest.main()
