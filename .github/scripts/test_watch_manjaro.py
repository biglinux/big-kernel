"""Scenarios the Manjaro watcher must get right, against simulated databases.

Run from the repository root: python3 -m unittest discover -s .github/scripts
"""

import contextlib
import io
import os
import re
import sys
import tarfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import watch_manjaro as watch  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# The current series, whose real PKGBUILDs the scenarios run against.
K = watch.kernel_packages(ROOT)[-1]
KERNEL = "{}-{}".format(
    watch.pkgbuild_value(os.path.join(ROOT, K, "PKGBUILD"), "pkgver"),
    watch.pkgbuild_value(os.path.join(ROOT, K, "PKGBUILD"), "pkgrel"),
)
REL = watch.kernel_rel(*KERNEL.split("-"))
MODULE_PACKAGES = [watch.module_package(K, module) for module in watch.MODULES]


def plan(root, fetch_database, **kwargs):
    """watch.plan() for the current series."""
    return watch.plan(root, fetch_database, K, **kwargs)

MANJARO = {
    "nvidia-open-dkms": "610.57.04-1",
    "nvidia-dkms": "610.57.04-1",
    "nvidia-580xx-open-dkms": "580.178.04-1",
    "nvidia-580xx-dkms": "580.178.04-1",
    "broadcom-wl-dkms": "6.30.223.271-49.0",
    "nvidia-390xx-dkms": "390.157-33",
    "virtualbox-host-dkms": "7.2.16-1",
    "zfs-dkms": "2.4.4-1",
    "vhba-module-dkms": "20260313-1",
    "v4l2loopback-dkms": "0.15.4-2",
    "acpi_call-dkms": "1.2.2-3.0",
}


def suffix(module):
    return watch.rebuild_suffix(os.path.join(ROOT, module, "PKGBUILD"), KERNEL)


def builds_testing_too(module):
    """Whether the module's PKGBUILD also builds for Manjaro testing's driver."""
    with open(os.path.join(ROOT, module, "PKGBUILD"), encoding="utf-8") as pkgbuild:
        return bool(watch.TESTING_MARK.search(pkgbuild.read()))


def up_to_date(manjaro, testing=None):
    """What BigLinux has published when every module matches."""
    ours = {K: KERNEL}
    for name, source in watch.MODULES.items():
        module = watch.module_package(K, name)
        if source:
            driver = watch.without_pkgrel(manjaro[source])
            if testing and builds_testing_too(module) and watch.without_pkgrel(testing[source]) != driver:
                driver += "+" + watch.without_pkgrel(testing[source])
            ours[module] = f"{driver}-{REL}{suffix(module)}"
        else:
            ours[module] = f"{watch.pkgbuild_value(os.path.join(ROOT, module, 'PKGBUILD'), 'pkgver')}-{REL}{suffix(module)}"
    return ours


def databases(stable_ours, testing_ours, stable_manjaro=MANJARO, testing_manjaro=MANJARO, **extra):
    """Every repository the watcher reads; the ones not given are empty."""
    by_url = {
        watch.BRANCHES["stable"][1]: stable_ours,
        watch.BRANCHES["testing"][1]: testing_ours,
        watch.MANJARO_DB.format(branch="stable"): stable_manjaro,
        watch.MANJARO_DB.format(branch="testing"): testing_manjaro,
    }
    # BigLinux's stable and testing hold the kernels and modules as well as
    # the drivers BigLinux publishes: one database, both sets.
    for name, db in extra.items():
        url = watch.BIGLINUX_DB.format(branch=name.replace("_", "-"))
        by_url[url] = dict(by_url.get(url, {}), **db)
    known = {url for _m, _o, search in watch.BRANCHES.values() for url in search} | set(by_url)

    def fetch(url):
        if url not in known:
            raise AssertionError(f"unexpected repository {url}")
        return by_url.get(url, {})

    return fetch


def modules(builds, branch=None):
    return sorted(module for b, _m, module, _e in builds if branch in (None, b))


class Plan(unittest.TestCase):
    def setUp(self):
        watch.log = lambda _message: None

    def test_modules_wait_for_the_kernel(self):
        builds = plan(ROOT, databases({}, {}))
        self.assertEqual([m for _b, _mb, m, _e in builds], [K])

    def test_an_older_kernel_published_is_not_enough(self):
        builds = plan(ROOT, databases({K: "7.2.7-1"}, {}))
        self.assertEqual([m for _b, _mb, m, _e in builds], [K])

    def test_a_kernel_ahead_of_testing_is_built_for_testing(self):
        # What the kernel watcher leaves behind: the PKGBUILD moved on,
        # testing still has the previous release. Modules wait for it.
        builds = plan(ROOT, databases(up_to_date(MANJARO), {K: "7.2.7-2"}))
        self.assertEqual(builds, [("testing", "stable", K, KERNEL)])

    def test_stable_never_gets_a_kernel_by_itself(self):
        builds = plan(ROOT, databases({K: "7.2.7-2"}, up_to_date(MANJARO)))
        self.assertNotIn(K, [m for b, _mb, m, _e in builds if b == "stable"])

    def test_a_newer_kernel_published_by_hand_is_not_rebuilt_older(self):
        builds = plan(ROOT, databases(up_to_date(MANJARO), {K: "9.9.9-1"}))
        self.assertEqual(builds, [])

    def test_the_kernel_build_is_dispatched_once(self):
        build = ("testing", "testing", K, KERNEL)
        state = {watch.state_key(*build): "2026-09-27T00:00:00Z"}
        now = watch.calendar.timegm((2026, 9, 27, 0, 10, 0))
        self.assertEqual(watch.pending([build], state, now=now), ([], []))

    def test_a_published_kernel_without_modules_builds_all_of_them(self):
        builds = plan(ROOT, databases({K: KERNEL}, {K: KERNEL}))
        self.assertEqual(modules(builds, "stable"), sorted(MODULE_PACKAGES))
        self.assertEqual(modules(builds, "testing"), sorted(MODULE_PACKAGES))

    def test_nothing_is_built_when_everything_matches(self):
        builds = plan(ROOT, databases(up_to_date(MANJARO), up_to_date(MANJARO)))
        self.assertEqual(builds, [])

    def test_a_new_driver_in_manjaro_testing_adds_a_build_for_it(self):
        # 2026-10-08: Manjaro testing got NVIDIA 615, stable kept 610, and
        # pacman refused linux-big-nvidia-open (nvidia-utils=610.57.04) to
        # Manjaro testing users. The NVIDIA modules now carry both builds.
        newer = dict(MANJARO, **{"nvidia-open-dkms": "615.71.09-2", "nvidia-dkms": "615.71.09-2",
                                 "virtualbox-host-dkms": "7.2.20-1"})
        builds = plan(ROOT, databases(up_to_date(MANJARO), up_to_date(MANJARO), testing_manjaro=newer))
        for branch in ("stable", "testing"):
            self.assertEqual(modules(builds, branch), [f"{K}-nvidia", f"{K}-nvidia-open"])
        self.assertEqual({e for _b, _m, _mod, e in builds}, {f"610.57.04+615.71.09-{REL}"})
        # Built against Manjaro stable, like everything: the PKGBUILD fetches
        # testing's driver by itself.
        self.assertEqual({mb for _b, mb, _mod, _e in builds}, {"stable"})

    def test_both_builds_published_is_up_to_date(self):
        newer = dict(MANJARO, **{"nvidia-open-dkms": "615.71.09-2", "nvidia-dkms": "615.71.09-2"})
        both = up_to_date(MANJARO, testing=newer)
        self.assertEqual(both[f"{K}-nvidia-open"], f"610.57.04+615.71.09-{REL}")
        self.assertEqual(plan(ROOT, databases(both, both, testing_manjaro=newer)), [])

    def test_a_pkgbuild_without_the_testing_build_follows_stable_alone(self):
        # The stable branch may be built from a commit before the change.
        newer = {"nvidia-open-dkms": "615.71.09-2"}
        fetch = databases({}, {}, testing_manjaro=newer)
        search = watch.BRANCHES["stable"][2]
        self.assertEqual(watch.module_driver("pkgver=1\n", "nvidia-open-dkms", search, fetch), "610.57.04")
        self.assertEqual(watch.module_driver("_manjaro_testing=x\n", "nvidia-open-dkms", search, fetch),
                         "610.57.04+615.71.09")

    def test_a_driver_missing_from_manjaro_testing_is_skipped(self):
        partial = {k: v for k, v in MANJARO.items() if k != "nvidia-dkms"}
        builds = plan(ROOT, databases({K: KERNEL}, {}, testing_manjaro=partial))
        self.assertNotIn(f"{K}-nvidia", modules(builds, "stable"))
        self.assertIn(f"{K}-nvidia-open", modules(builds, "stable"))

    def test_a_new_driver_in_manjaro_stable_rebuilds_both_branches(self):
        # What 2026-10-07 needed once NVIDIA 615 reaches Manjaro stable: testing
        # too, or community-testing keeps a module older than nvidia-utils.
        newer = dict(MANJARO, **{"nvidia-open-dkms": "615.10.02-1", "nvidia-dkms": "615.10.02-1"})
        both = up_to_date(MANJARO)
        builds = plan(ROOT, databases(both, both, stable_manjaro=newer, testing_manjaro=newer))
        self.assertEqual(modules(builds, "testing"), [f"{K}-nvidia", f"{K}-nvidia-open"])
        self.assertEqual(modules(builds, "stable"), [f"{K}-nvidia", f"{K}-nvidia-open"])
        self.assertEqual({e for _b, _m, _mod, e in builds}, {f"615.10.02-{REL}"})
        self.assertEqual({mb for _b, mb, _mod, _e in builds}, {"stable"})

    def test_a_new_kernel_rebuilds_every_module(self):
        old_rel = watch.kernel_rel("7.2.7", "1")
        published = {k: v.replace(f"-{REL}", f"-{old_rel}") for k, v in up_to_date(MANJARO).items()}
        published[K] = KERNEL
        builds = plan(ROOT, databases(published, {}))
        self.assertEqual(modules(builds, "stable"), sorted(MODULE_PACKAGES))

    def test_zfs_follows_the_compat_build_for_this_kernel_series(self):
        # 7.3 with OpenZFS 2.4.4 (META stops at 7.2): Manjaro ships
        # zfs-dkms-7.3-compat, and linux-big-zfs builds from it.
        series = ".".join(KERNEL.split(".")[:2])
        compat = dict(MANJARO, **{f"zfs-dkms-{series}-compat": "2.4.5-1"})
        both = up_to_date(MANJARO)
        builds = plan(ROOT, databases(both, both, stable_manjaro=compat, testing_manjaro=compat))
        self.assertEqual(modules(builds), [f"{K}-zfs", f"{K}-zfs"])
        self.assertEqual({e for _b, _m, _mod, e in builds}, {f"2.4.5-{REL}{suffix(f'{K}-zfs')}"})

    def test_a_compat_build_for_another_series_is_ignored(self):
        other = dict(MANJARO, **{"zfs-dkms-6.1-compat": "9.9.9-1"})
        both = up_to_date(MANJARO)
        self.assertEqual(plan(ROOT, databases(both, both, stable_manjaro=other, testing_manjaro=other)), [])

    def test_the_zfs_pkgbuild_picks_the_same_package(self):
        # The PKGBUILD and the watcher must agree on which package is followed.
        with open(os.path.join(ROOT, f"{K}-zfs", "PKGBUILD"), encoding="utf-8") as pkgbuild:
            text = pkgbuild.read()
        self.assertIn("_zfs_dkms=zfs-dkms-${_kbasekernel}-compat", text)
        self.assertIn('"$_zfs_dkms=$pkgver"', text)
        self.assertIn("zfs-dkms", watch.COMPAT_SOURCES)

    def test_a_driver_biglinux_publishes_first_wins_as_it_does_for_pacman(self):
        # biglinux-priority comes before Manjaro's extra in pacman.conf.
        biglinux = {"nvidia-open-dkms": "615.10.02-1"}
        builds = plan(
            ROOT, databases(up_to_date(MANJARO), {}, priority=biglinux)
        )
        stable = [(m, e) for b, _mb, m, e in builds if b == "stable"]
        self.assertEqual(stable, [(f"{K}-nvidia-open", f"615.10.02-{REL}")])

    def test_a_driver_only_in_biglinux_stable_is_found(self):
        partial = {k: v for k, v in MANJARO.items() if k != "broadcom-wl-dkms"}
        builds = plan(
            ROOT,
            databases({K: KERNEL}, {}, stable_manjaro=partial, stable={"broadcom-wl-dkms": "6.30.223.271-50"}),
        )
        found = {m: e for _b, _mb, m, e in builds}
        self.assertEqual(found[f"{K}-broadcom-wl"], f"6.30.223.271-{REL}")

    def test_manjaro_still_wins_over_biglinux_stable_which_comes_after_it(self):
        builds = plan(
            ROOT, databases(up_to_date(MANJARO), {}, stable={"nvidia-open-dkms": "999.0-1"})
        )
        self.assertEqual([b for b in builds if b[0] == "stable"], [])

    def test_every_module_directory_is_watched(self):
        directories = {d for d in os.listdir(ROOT) if d.startswith("linux-big") and os.path.isfile(os.path.join(ROOT, d, "PKGBUILD"))}
        series = watch.kernel_packages(ROOT)
        expected = {watch.META, *series, *(watch.module_package(k, m) for k in series for m in watch.MODULES)}
        self.assertEqual(directories, expected)

    def test_a_driver_missing_from_manjaro_is_skipped_not_fatal(self):
        partial = {k: v for k, v in MANJARO.items() if k != "nvidia-580xx-dkms"}
        builds = plan(ROOT, databases({K: KERNEL}, {}, stable_manjaro=partial))
        self.assertNotIn(f"{K}-nvidia-580xx", modules(builds, "stable"))


class Rebuild(unittest.TestCase):
    """_rebuild in a module PKGBUILD, as _pkgrel() there reads it."""

    def pkgbuild(self, rebuild_for, rebuild):
        import tempfile
        handle = tempfile.NamedTemporaryFile("w", suffix="PKGBUILD", delete=False)
        handle.write(f"_rebuild_for={rebuild_for}\n_rebuild={rebuild}\npkgrel=$(_pkgrel)\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_a_rebuild_for_this_kernel_adds_a_suffix(self):
        self.assertEqual(watch.rebuild_suffix(self.pkgbuild("7.2.7-2", "1"), "7.2.7-2"), ".1")

    def test_a_new_kernel_drops_it(self):
        self.assertEqual(watch.rebuild_suffix(self.pkgbuild("7.2.7-2", "1"), "7.2.8-1"), "")

    def test_no_rebuild_no_suffix(self):
        self.assertEqual(watch.rebuild_suffix(self.pkgbuild("", ""), "7.2.7-2"), "")

    def test_the_old_build_is_rebuilt_and_the_rebuild_is_kept(self):
        module = f"{K}-zfs"
        if not suffix(module):
            self.skipTest("no rebuild pending in linux-big-zfs")
        published = up_to_date(MANJARO)
        published[module] = published[module].rsplit(".", 1)[0]
        builds = plan(ROOT, databases(published, {}))
        self.assertEqual([(m, e) for _b, _mb, m, e in builds], [(module, f"2.4.4-{REL}{suffix(module)}")])
        self.assertEqual(plan(ROOT, databases(up_to_date(MANJARO), {})), [])

    def test_a_new_driver_in_either_branch_is_an_upgrade(self):
        import subprocess
        def vercmp(a, b):
            return int(subprocess.run(["vercmp", a, b], capture_output=True, text=True).stdout)
        try:
            self.assertEqual(vercmp("610.57.04+615.71.09", "610.57.04"), 1)       # testing moves ahead
            self.assertEqual(vercmp("610.57.04+615.80.01", "610.57.04+615.71.09"), 1)  # testing again
            self.assertEqual(vercmp("610.60.01+615.71.09", "610.57.04+615.71.09"), 1)  # stable alone
            self.assertEqual(vercmp("615.71.09", "610.57.04+615.71.09"), 1)       # stable catches up
        except FileNotFoundError:
            self.skipTest("vercmp (pacman) not installed")

    def test_the_suffixed_version_is_an_upgrade_until_the_next_kernel(self):
        import subprocess
        def vercmp(a, b):
            return int(subprocess.run(["vercmp", a, b], capture_output=True, text=True).stdout)
        try:
            self.assertEqual(vercmp("2.4.4-7020702.1", "2.4.4-7020702"), 1)
            self.assertEqual(vercmp("2.4.4-7020801", "2.4.4-7020702.1"), 1)
        except FileNotFoundError:
            self.skipTest("vercmp (pacman) not installed")


class Versions(unittest.TestCase):
    def test_kernel_versions_compare_as_numbers(self):
        self.assertGreater(watch.version_key("7.2.10-1"), watch.version_key("7.2.9-3"))
        self.assertGreater(watch.version_key("7.2.8-2"), watch.version_key("7.2.8-1"))
        self.assertGreater(watch.version_key("7.3-1"), watch.version_key("7.2.99-1"))


class Rel(unittest.TestCase):
    def test_kernel_releases_order_as_pkgrel(self):
        order = [("7.2.7", "1"), ("7.2.7", "2"), ("7.2.10", "1"), ("7.3", "1"), ("7.3.0", "2"), ("8.0.1", "1")]
        values = [int(watch.kernel_rel(v, r)) for v, r in order]
        self.assertEqual(values, sorted(values))
        self.assertEqual(len(set(values)), len(values))


class Dedupe(unittest.TestCase):
    build = ("testing", "testing", f"{K}-nvidia-open", "615.10.02-7020702")
    at = watch.calendar.timegm((2026, 9, 24, 0, 0, 0))

    def state(self, attempts=None, **extra):
        value = "2026-09-24T00:00:00Z" if attempts is None else dict({"at": "2026-09-24T00:00:00Z", "attempts": attempts}, **extra)
        return {watch.state_key(*self.build): value}

    def test_a_version_dispatched_recently_waits(self):
        now = self.at + watch.RETRY_AFTER - 1
        self.assertEqual(watch.pending([self.build], self.state(), now=now), ([], []))

    def test_force_dispatches_it_again(self):
        now = self.at + 60
        self.assertEqual(watch.pending([self.build], self.state(3), force=True, now=now), ([self.build], []))

    def test_a_newer_version_is_dispatched(self):
        newer = self.build[:3] + ("615.20.01-7020702",)
        self.assertEqual(watch.pending([newer], self.state(), now=self.at + 60), ([newer], []))

    def test_a_build_that_did_not_publish_is_dispatched_again(self):
        # 2026-10-18: linux-big-nvidia failed to download linux-big from the
        # repository; the version stayed behind and nobody dispatched it again.
        now = self.at + watch.RETRY_AFTER
        self.assertEqual(watch.pending([self.build], self.state(), now=now), ([self.build], []))
        self.assertEqual(watch.pending([self.build], self.state(2), now=now), ([self.build], []))

    def test_three_attempts_then_it_gives_up(self):
        now = self.at + watch.RETRY_AFTER
        self.assertEqual(watch.pending([self.build], self.state(3), now=now), ([], [self.build]))

    def test_the_old_state_format_counts_as_one_attempt(self):
        self.assertEqual(watch.attempt(self.state(), watch.state_key(*self.build)), (1, self.at, False))

    def test_giving_up_is_remembered(self):
        state = self.state(3, gave_up=True)
        self.assertEqual(watch.attempt(state, watch.state_key(*self.build)), (3, self.at, True))


class MovedToStable(unittest.TestCase):
    """Testing is seen as a testing user sees it: its packages over stable's."""

    def setUp(self):
        watch.log = lambda _message: None

    def test_moving_everything_to_stable_rebuilds_nothing(self):
        # Repo-Management moves the files: testing is left without linux-big.
        builds = plan(ROOT, databases(up_to_date(MANJARO), {}))
        self.assertEqual(builds, [])

    def test_a_newer_driver_only_in_manjaro_testing_builds_both_drivers(self):
        newer = dict(MANJARO, **{"nvidia-open-dkms": "615.71.09-1"})
        builds = plan(ROOT, databases(up_to_date(MANJARO), {}, testing_manjaro=newer))
        self.assertEqual(sorted(builds), [
            (branch, "stable", f"{K}-nvidia-open", f"610.57.04+615.71.09-{REL}")
            for branch in ("stable", "testing")
        ])


class StableBehindMain(unittest.TestCase):
    """Stable keeps its kernel while main moves on: its modules still follow
    Manjaro stable, built from the commit that described that kernel."""

    def setUp(self):
        import shutil
        import subprocess
        import tempfile
        watch.log = lambda _message: None
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        for entry in [K, *MODULE_PACKAGES]:
            os.makedirs(os.path.join(self.repo, entry))
            shutil.copy(os.path.join(ROOT, entry, "PKGBUILD"), os.path.join(self.repo, entry, "PKGBUILD"))

        def git(*args):
            return subprocess.run(["git", "-C", self.repo, *args], capture_output=True, text=True, check=True).stdout.strip()

        git("init", "-q")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "start")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"linux-big {KERNEL}")
        self.stable_commit = git("rev-parse", "HEAD")
        kernel = os.path.join(self.repo, K, "PKGBUILD")
        text = open(kernel).read()
        # A later release of the same series, whatever the PKGBUILD is on.
        self.main_kernel = ".".join(KERNEL.split(".")[:2]) + ".99"
        text = re.sub(r"^pkgver=.*$", f"pkgver={self.main_kernel}", text, count=1, flags=re.M)
        text = re.sub(r"^pkgrel=.*$", "pkgrel=1", text, count=1, flags=re.M)
        open(kernel, "w").write(text)
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-am", f"linux-big {self.main_kernel}-1")

    def test_a_new_driver_in_stable_is_built_from_stables_commit(self):
        newer = dict(MANJARO, **{"nvidia-open-dkms": "615.71.09-1"})
        refs = {}
        builds = plan(self.repo, databases(up_to_date(MANJARO), up_to_date(MANJARO), stable_manjaro=newer,
                                                 testing_manjaro=newer), refs=refs)
        stable = [b for b in builds if b[0] == "stable"]
        # The stable kernel's pkgrel, not main's: built against the kernel stable has.
        self.assertEqual(stable, [("stable", "stable", f"{K}-nvidia-open", f"615.71.09-{REL}")])
        self.assertEqual(refs, {"stable": self.stable_commit})
        # Testing follows main: it gets the new kernel.
        self.assertIn(("testing", "stable", K, f"{self.main_kernel}-1"), builds)

    def test_nothing_to_do_for_stable_when_its_modules_match(self):
        refs = {}
        builds = plan(self.repo, databases(up_to_date(MANJARO), up_to_date(MANJARO)), refs=refs)
        self.assertEqual([b for b in builds if b[0] == "stable"], [])

    def test_a_stable_kernel_no_commit_describes_is_left_alone(self):
        refs = {}
        builds = plan(self.repo, databases(up_to_date(MANJARO), {}), refs=refs, find=lambda root, k, kernel_pkg: None)
        self.assertEqual([b for b in builds if b[0] == "stable"], [])
        self.assertEqual(refs, {})

    def test_the_commit_is_found_by_the_kernel_it_describes(self):
        self.assertEqual(watch.find_commit(self.repo, KERNEL, K), self.stable_commit)
        self.assertIsNone(watch.find_commit(self.repo, "6.1.0-1", K))


class StableDispatch(unittest.TestCase):
    """Stable builds that need an older commit run from linux-bigNN-stable."""

    def test_the_branch_is_pointed_once_and_used_by_every_stable_build(self):
        import tempfile
        sent, pointed = [], []
        state_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        state_file.write("{}")
        state_file.close()
        self.addCleanup(os.unlink, state_file.name)
        builds = [
            ("stable", "stable", f"{K}-nvidia", "615.71.09-7020801"),
            ("stable", "stable", f"{K}-nvidia-open", "615.71.09-7020801"),
            ("testing", "testing", K, "7.2.99-1"),
        ]

        def fake_plan(root, fetch, refs=None, **_kw):
            refs[K] = {"stable": "abc123def456"}
            return builds

        saved = (watch.plan_all, watch.building, watch.dispatch, watch.point_branch, watch.log, sys.argv)
        watch.plan_all, watch.building = fake_plan, (lambda token: set())
        watch.dispatch = lambda token, body: sent.append((body["event_type"], body["client_payload"]["branch"])) or 204
        watch.point_branch = lambda root, name, commit: pointed.append((name, commit))
        watch.log = lambda _message: None
        sys.argv = ["watch_manjaro.py", "--state", state_file.name]
        os.environ["DISPATCH_TOKEN"] = "x"
        github_output = os.environ.pop("GITHUB_OUTPUT", None)
        try:
            watch.main()
        finally:
            watch.plan_all, watch.building, watch.dispatch, watch.point_branch, watch.log, sys.argv = saved
            del os.environ["DISPATCH_TOKEN"]
            if github_output is not None:
                os.environ["GITHUB_OUTPUT"] = github_output
        self.assertEqual(pointed, [(watch.stable_ref(K), "abc123def456")])
        self.assertEqual(sent, [
            (f"{K}-nvidia", watch.stable_ref(K)),
            (f"{K}-nvidia-open", watch.stable_ref(K)),
            (K, "main"),
        ])


class Retry(unittest.TestCase):
    """main() records each attempt and fails one run when it gives up."""

    build = ("testing", "stable", f"{K}-nvidia", "610.57.04+615.71.09-7021102")

    def run_main(self, state, active=frozenset()):
        import json
        import tempfile
        sent = []
        state_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(state, state_file)
        state_file.close()
        self.addCleanup(os.unlink, state_file.name)
        saved = (watch.plan_all, watch.building, watch.dispatch, watch.log, sys.argv)
        watch.plan_all = lambda root, fetch, refs=None, **_kw: [self.build]
        watch.building = lambda token: set(active)
        watch.dispatch = lambda token, body: sent.append(body["event_type"]) or 204
        watch.log = lambda _message: None
        sys.argv = ["watch_manjaro.py", "--state", state_file.name]
        os.environ["DISPATCH_TOKEN"] = "x"
        github_output = os.environ.pop("GITHUB_OUTPUT", None)
        try:
            with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet):
                code = watch.main()
        finally:
            watch.plan_all, watch.building, watch.dispatch, watch.log, sys.argv = saved
            del os.environ["DISPATCH_TOKEN"]
            if github_output is not None:
                os.environ["GITHUB_OUTPUT"] = github_output
        with open(state_file.name, encoding="utf-8") as saved_state:
            return code, sent, json.load(saved_state)[watch.state_key(*self.build)]

    def test_a_failed_build_is_dispatched_again_and_counted(self):
        code, sent, entry = self.run_main({watch.state_key(*self.build): "2026-10-18T21:00:00Z"})
        self.assertEqual((code, sent, entry["attempts"]), (0, [f"{K}-nvidia"], 2))

    def test_a_build_still_running_is_not_dispatched_again(self):
        old = {watch.state_key(*self.build): "2026-10-18T21:00:00Z"}
        code, sent, _entry = self.run_main(old, active={f"{K}-nvidia"})
        self.assertEqual((code, sent), (0, []))

    def test_giving_up_fails_one_run_only(self):
        old = {watch.state_key(*self.build): {"at": "2026-10-18T21:00:00Z", "attempts": 3}}
        code, sent, entry = self.run_main(old)
        self.assertEqual((code, sent, entry.get("gave_up")), (1, [], True))
        code, sent, _entry = self.run_main({watch.state_key(*self.build): entry})
        self.assertEqual((code, sent), (0, []))


class PointBranch(unittest.TestCase):
    def test_only_a_full_commit_hash_is_pushed(self):
        for commit in ("abc123", "HEAD", "main", "--force", "0" * 39 + "; rm -rf /"):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                watch.point_branch(".", watch.stable_ref(K), commit)

    def test_a_pkgbuild_without_a_version_is_reported_by_name(self):
        with self.assertRaises(watch.PkgbuildError) as raised:
            watch.kernel_version("pkgname=linux-big73\n", "linux-big73/PKGBUILD")
        self.assertIn("linux-big73/PKGBUILD", str(raised.exception))


class InFlight(unittest.TestCase):
    """A package build-package is already building is not dispatched again."""

    def run_main(self, active, builds):
        import tempfile
        sent = []
        state_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        state_file.write("{}")
        state_file.close()
        self.addCleanup(os.unlink, state_file.name)
        saved = (watch.plan_all, watch.building, watch.dispatch, sys.argv, os.environ.get("DISPATCH_TOKEN"))
        watch.plan_all = lambda root, fetch, **_kw: builds
        watch.building = lambda token: active
        watch.dispatch = lambda token, body: sent.append(body["event_type"]) or 204
        sys.argv = ["watch_manjaro.py", "--state", state_file.name]
        os.environ["DISPATCH_TOKEN"] = "x"
        github_output = os.environ.pop("GITHUB_OUTPUT", None)
        if github_output is not None:
            self.addCleanup(os.environ.__setitem__, "GITHUB_OUTPUT", github_output)
        try:
            watch.main()
        finally:
            watch.plan_all, watch.building, watch.dispatch, sys.argv = saved[:4]
            if saved[4] is None:
                del os.environ["DISPATCH_TOKEN"]
            else:
                os.environ["DISPATCH_TOKEN"] = saved[4]
        return sent

    def setUp(self):
        watch.log = lambda _message: None

    def test_a_kernel_being_built_is_not_dispatched_again(self):
        # What happened with 7.2.8: the dispatch state was lost on a re-run
        # and the next run sent a second four-hour build.
        builds = [("testing", "testing", K, "7.2.8-1")]
        self.assertEqual(self.run_main({K}, builds), [])

    def test_only_the_packages_in_flight_are_skipped(self):
        builds = [
            ("testing", "testing", f"{K}-nvidia", "615.71.09-7020801"),
            ("testing", "testing", f"{K}-zfs", "2.4.4-7020801"),
        ]
        self.assertEqual(self.run_main({f"{K}-nvidia"}, builds), [f"{K}-zfs"])

    def test_nothing_in_flight_dispatches_everything(self):
        builds = [("testing", "testing", K, "7.2.8-1")]
        self.assertEqual(self.run_main(set(), builds), [K])


class WaitForBuild(unittest.TestCase):
    """A dispatched build is followed until it is published or fails."""

    def run_wait(self, published, in_flight, **limits):
        clock = {"t": 0}
        states = iter(published)
        last = {"v": None}

        def get_published():
            last["v"] = next(states, last["v"])
            return last["v"]

        def sleep(seconds):
            clock["t"] += seconds

        return watch.wait_for_build("7.2.8-1", get_published, lambda: in_flight(clock["t"]),
                                    sleep=sleep, now=lambda: clock["t"], **limits)

    def test_published_after_a_while(self):
        result = self.run_wait(["7.2.7-2"] * 40 + ["7.2.8-1"], lambda t: True)
        self.assertEqual(result, "published")

    def test_a_failed_build_is_reported_not_waited_for(self):
        # Nothing building any more and the kernel never appeared.
        result = self.run_wait(["7.2.7-2"] * 1000, lambda t: t < 3600)
        self.assertEqual(result, "failed")

    def test_the_dispatch_gets_time_to_show_up_as_a_run(self):
        # Right after the dispatch build-package has no run yet: not a failure.
        result = self.run_wait(["7.2.7-2"] * 3 + ["7.2.8-1"], lambda t: False)
        self.assertEqual(result, "published")

    def test_gives_up_after_the_limit(self):
        result = self.run_wait(["7.2.7-2"] * 10000, lambda t: True, minutes=60)
        self.assertEqual(result, "timeout")


class Payload(unittest.TestCase):
    def test_matches_what_build_package_reads(self):
        body = watch.payload("testing", "testing", f"{K}-nvidia-open")
        self.assertEqual(body["event_type"], f"{K}-nvidia-open")
        data = body["client_payload"]
        self.assertEqual(data["pkgbuild_dir"], f"{K}-nvidia-open")
        self.assertEqual(data["url"], "https://github.com/biglinux/big-kernel")
        self.assertEqual((data["branch_type"], data["manjaro_branch"], data["new_branch"]), ("testing", "testing", "main"))
        self.assertNotIn("new_branch", watch.payload("stable", "stable", "x")["client_payload"])


class Database(unittest.TestCase):
    def test_reads_a_pacman_database(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name, version in ((K, "7.2.7-2"), ("nvidia-utils", "610.57.04-1")):
                desc = f"%FILENAME%\n{name}.pkg.tar.zst\n\n%NAME%\n{name}\n\n%VERSION%\n{version}\n\n".encode()
                info = tarfile.TarInfo(f"{name}-{version}/desc")
                info.size = len(desc)
                archive.addfile(info, io.BytesIO(desc))
        self.assertEqual(
            watch.read_database(buffer.getvalue()), {K: "7.2.7-2", "nvidia-utils": "610.57.04-1"}
        )


class Series(unittest.TestCase):
    """Every series is a package of its own, followed the same way."""

    def setUp(self):
        watch.log = lambda _message: None

    def test_the_series_in_the_repository_oldest_first(self):
        self.assertEqual(watch.kernel_packages(ROOT), sorted(watch.kernel_packages(ROOT), key=lambda k: int(k[9:])))
        self.assertGreaterEqual(len(watch.kernel_packages(ROOT)), 1)
        self.assertEqual(watch.kernel_of("linux-big73-nvidia-open"), "linux-big73")
        self.assertEqual(watch.kernel_of("linux-big72"), "linux-big72")
        self.assertIsNone(watch.kernel_of("linux-big-nvidia"))

    def test_each_series_module_is_the_same_pkgbuild_but_for_its_kernel(self):
        # One fix, every series: the copies may differ only in _kernel_pkg
        # and in a pending rebuild, which names a kernel of its own series.
        varying = re.compile(r"^(_kernel_pkg|_rebuild_for|_rebuild)=.*$", re.MULTILINE)
        for module in watch.MODULES:
            texts = {}
            for kernel_pkg in watch.kernel_packages(ROOT):
                directory = os.path.join(ROOT, watch.module_package(kernel_pkg, module))
                with open(os.path.join(directory, "PKGBUILD"), encoding="utf-8") as pkgbuild:
                    text = pkgbuild.read()
                self.assertIn(f"\n_kernel_pkg={kernel_pkg}\n", text)
                others = {}
                for name in sorted(os.listdir(directory)):
                    if name != "PKGBUILD":
                        with open(os.path.join(directory, name), "rb") as other:
                            others[name] = other.read()
                texts[kernel_pkg] = (varying.sub("", text), others)
            with self.subTest(module=module):
                self.assertEqual(len({repr(v) for v in texts.values()}), 1)

    def test_every_series_is_planned_and_each_kernel_built_for_testing(self):
        builds = watch.plan_all(ROOT, databases({}, {}))
        kernels = [(b, m) for b, _mb, m, _e in builds if m in watch.kernel_packages(ROOT)]
        self.assertEqual(kernels, [("testing", k) for k in watch.kernel_packages(ROOT)])

    def test_a_series_modules_follow_their_own_kernel(self):
        published = up_to_date(MANJARO)
        builds = watch.plan_all(ROOT, databases(published, published))
        # The current series is up to date; any other is not published at all.
        self.assertEqual({watch.kernel_of(m) for _b, _mb, m, _e in builds} - {None},
                         set(watch.kernel_packages(ROOT)) - {K})


def everything_published(meta_text):
    """The testing view with every package the metapackages depend on."""
    return {package: "1-1" for package in watch.meta_packages(meta_text)}


class Meta(unittest.TestCase):
    """linux-big, linux-big-headers and linux-big-<module> follow the series."""

    def setUp(self):
        watch.log = lambda _message: None
        with open(os.path.join(ROOT, watch.META, "PKGBUILD"), encoding="utf-8") as pkgbuild:
            self.text = pkgbuild.read()
        self.expected = watch.meta_version(self.text)

    def plan(self, testing):
        return watch.plan_meta(ROOT, databases({}, testing))

    def test_it_lists_every_module_and_only_series_in_the_repository(self):
        # A series may be in the repository and not in _series (7.3 while it
        # is in testing), never the other way round.
        self.assertEqual(sorted(watch.array_value(self.text, "_modules", "meta")), sorted(watch.MODULES))
        series = [f"{watch.META}{s}" for s in watch.array_value(self.text, "_series", "meta")]
        self.assertTrue(series)
        self.assertLessEqual(set(series), set(watch.kernel_packages(ROOT)))
        self.assertEqual(len(series), len(set(series)))

    def test_its_version_names_the_series_it_follows(self):
        current = f"{watch.META}{watch.array_value(self.text, '_series', 'meta')[0]}"
        with open(os.path.join(ROOT, current, "PKGBUILD"), encoding="utf-8") as pkgbuild:
            base = watch.text_value(pkgbuild.read(), "_basekernel", current)
        self.assertEqual(watch.text_value(self.text, "pkgver", "meta"), base)

    def test_it_is_above_every_package_from_before_the_split(self):
        for old in ("7.2.15-1", "9.9.99-9", "615.71.09-7021501", "610.57.04+615.71.09-7021501".replace("+", ".")):
            with self.subTest(old=old):
                self.assertGreater(watch.meta_key(self.expected), watch.meta_key(old))

    def test_it_waits_for_everything_it_depends_on(self):
        testing = everything_published(self.text)
        del testing[watch.module_package(watch.kernel_packages(ROOT)[0], "zfs")]
        self.assertEqual(self.plan(testing), [])

    def test_it_is_built_once_everything_is_there(self):
        testing = everything_published(self.text)
        self.assertEqual(self.plan(testing), [("testing", "stable", watch.META, self.expected)])
        testing[watch.META] = "7.2.15-1"  # the kernel itself, from before the split
        self.assertEqual(self.plan(testing), [("testing", "stable", watch.META, self.expected)])

    def test_nothing_when_published(self):
        testing = dict(everything_published(self.text), **{watch.META: self.expected})
        self.assertEqual(self.plan(testing), [])

    def test_a_newer_one_published_by_hand_is_left_alone(self):
        testing = dict(everything_published(self.text), **{watch.META: "9:9.9-1"})
        self.assertEqual(self.plan(testing), [])


class SecureBoot(unittest.TestCase):
    """big-secureboot goes to testing when its PKGBUILD is ahead."""

    def setUp(self):
        watch.log = lambda _message: None
        where = os.path.join(ROOT, watch.SECUREBOOT, "PKGBUILD")
        self.expected = "{}-{}".format(watch.pkgbuild_value(where, "pkgver"), watch.pkgbuild_value(where, "pkgrel"))

    def plan(self, testing, stable=None):
        return watch.plan_secureboot(ROOT, databases(stable or {}, testing))

    def test_built_when_testing_lacks_it(self):
        self.assertEqual(self.plan({}), [("testing", "stable", watch.SECUREBOOT, self.expected)])

    def test_built_when_testing_has_an_older_release(self):
        self.assertEqual(self.plan({watch.SECUREBOOT: "1.0.0-1"}),
                         [("testing", "stable", watch.SECUREBOOT, self.expected)])

    def test_nothing_when_published_in_testing_or_moved_to_stable(self):
        self.assertEqual(self.plan({watch.SECUREBOOT: self.expected}), [])
        self.assertEqual(self.plan({}, stable={watch.SECUREBOOT: self.expected}), [])

    def test_a_newer_one_published_by_hand_is_left_alone(self):
        self.assertEqual(self.plan({watch.SECUREBOOT: "99.0.0-1"}), [])

    def test_its_dispatch_points_at_its_directory(self):
        body = watch.payload("testing", "stable", watch.SECUREBOOT)
        self.assertEqual(body["client_payload"]["pkgbuild_dir"], watch.SECUREBOOT)
        self.assertEqual(body["client_payload"]["url"], watch.REPOSITORY_URL)

    def test_the_follow_up_accepts_it(self):
        self.assertTrue(watch.FOLLOWED.fullmatch(f"testing:{watch.SECUREBOOT}=1.1.5-2"))


class TwoKernels(unittest.TestCase):
    """Both series dispatched at once: the workflow waits for both."""

    def test_the_output_names_every_kernel_dispatched(self):
        import tempfile
        state_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        state_file.write("{}")
        state_file.close()
        self.addCleanup(os.unlink, state_file.name)
        output = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        output.close()
        self.addCleanup(os.unlink, output.name)
        builds = [
            ("testing", "stable", "linux-big72", "7.2.15-1"),
            ("testing", "stable", "linux-big73", "7.3.1-1"),
            ("testing", "stable", "linux-big73-zfs", "2.4.5-7030101"),
        ]
        saved = (watch.plan_all, watch.building, watch.dispatch, watch.log, sys.argv, os.environ.get("GITHUB_OUTPUT"))
        watch.plan_all = lambda root, fetch, **_kw: builds
        watch.building = lambda token: set()
        watch.dispatch = lambda token, body: 204
        watch.log = lambda _message: None
        sys.argv = ["watch_manjaro.py", "--state", state_file.name]
        os.environ["DISPATCH_TOKEN"] = "x"
        os.environ["GITHUB_OUTPUT"] = output.name
        try:
            watch.main()
        finally:
            watch.plan_all, watch.building, watch.dispatch, watch.log, sys.argv = saved[:5]
            del os.environ["DISPATCH_TOKEN"]
            if saved[5] is None:
                del os.environ["GITHUB_OUTPUT"]
            else:
                os.environ["GITHUB_OUTPUT"] = saved[5]
        with open(output.name, encoding="utf-8") as written:
            self.assertEqual(
                written.read(),
                "follow=testing:linux-big72=7.2.15-1 testing:linux-big73=7.3.1-1 "
                "testing:linux-big73-zfs=2.4.5-7030101\n",
            )

    def test_following_takes_only_what_the_watcher_writes(self):
        for item in ("linux-big73-zfs=1-1", "extra:linux-big73=7.3.3-1", "testing:firefox=1-1",
                     "testing:linux-big73=7.3.3-1;rm"):
            with self.subTest(item=item), self.assertRaises(SystemExit):
                watch.follow_builds([item], "", wait=lambda *a, **k: "published")


class FollowBuilds(unittest.TestCase):
    """2026-11-09: a module failed to download its kernel; the schedule did
    not run for hours, so nothing dispatched it again until it was run by hand."""

    def follow(self, results, items):
        clock = {"t": 0}
        calls = []

        def wait(version, published, in_flight, minutes):
            calls.append((version, minutes))
            clock["t"] += 600
            return results[len(calls) - 1]

        def sleep(seconds):
            clock["t"] += seconds

        saved = watch.log
        watch.log = lambda _message: None
        try:
            failed = watch.follow_builds(items, "", wait=wait, sleep=sleep, now=lambda: clock["t"])
        finally:
            watch.log = saved
        return failed, calls, clock["t"]

    def test_all_published_returns_at_once(self):
        failed, calls, elapsed = self.follow(
            ["published", "published"],
            ["testing:linux-big73=7.3.3-1", "testing:linux-big73-zfs=2.4.4-7030301"],
        )
        self.assertEqual(failed, 0)
        self.assertEqual(elapsed, 1200)

    def test_a_failed_build_waits_until_the_watcher_dispatches_it_again(self):
        failed, _calls, elapsed = self.follow(["failed"], ["testing:linux-big73-nvidia-580xx-open=580.178.04-7030301"])
        self.assertEqual(failed, 1)
        self.assertGreater(elapsed, watch.RETRY_AFTER)

    def test_a_kernel_gets_hours_and_a_module_two(self):
        _failed, calls, _elapsed = self.follow(
            ["published", "published"],
            ["testing:linux-big73=7.3.3-1", "stable:linux-big72-zfs=2.4.4-7021503"],
        )
        self.assertEqual(calls, [("7.3.3-1", 330), ("2.4.4-7021503", 120)])

    def test_the_metapackage_is_followed_too(self):
        failed, calls, _elapsed = self.follow(["published"], ["testing:linux-big=1:7.3-1"])
        self.assertEqual((failed, calls), (0, [("1:7.3-1", 120)]))

    def test_each_build_is_looked_up_in_its_own_branch(self):
        fetched = []
        saved = (watch.fetch, watch.read_database, watch.building, watch.log)
        watch.fetch = lambda url: fetched.append(url) or url
        watch.read_database = lambda url: {"linux-big72-zfs": "2", "linux-big73": "1"}
        watch.building = lambda token: set()
        watch.log = lambda _message: None
        try:
            failed = watch.follow_builds(["stable:linux-big72-zfs=2", "testing:linux-big73=1"], "",
                                         sleep=lambda s: None)
        finally:
            watch.fetch, watch.read_database, watch.building, watch.log = saved
        self.assertEqual(failed, 0)
        self.assertEqual(fetched, [watch.BRANCHES["stable"][1], watch.BRANCHES["testing"][1]])

    def test_a_run_that_dispatches_nothing_is_not_followed(self):
        # Otherwise every run would start another one, forever.
        import tempfile
        state_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        state_file.write("{}")
        state_file.close()
        self.addCleanup(os.unlink, state_file.name)
        output = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        output.close()
        self.addCleanup(os.unlink, output.name)
        saved = (watch.plan_all, watch.log, sys.argv, os.environ.get("GITHUB_OUTPUT"))
        watch.plan_all = lambda root, fetch, **_kw: []
        watch.log = lambda _message: None
        sys.argv = ["watch_manjaro.py", "--state", state_file.name]
        os.environ["GITHUB_OUTPUT"] = output.name
        try:
            watch.main()
        finally:
            watch.plan_all, watch.log, sys.argv = saved[:3]
            if saved[3] is None:
                del os.environ["GITHUB_OUTPUT"]
            else:
                os.environ["GITHUB_OUTPUT"] = saved[3]
        with open(output.name, encoding="utf-8") as written:
            self.assertEqual(written.read(), "")


if __name__ == "__main__":
    unittest.main()
