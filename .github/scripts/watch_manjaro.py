#!/usr/bin/env python3
"""Keep the linux-big kernel modules in step with Manjaro and with linux-big.

Each kernel series is a package of its own (linux-big72, linux-big73), with
its own module packages (linux-big73-nvidia-open, linux-big73-broadcom-wl,
...), and every series in the repository is followed the same way. The
module packages each depend on one exact kernel and on one exact driver
version. They go stale in two ways: a new kernel is published, or Manjaro
publishes a new driver.
Either way the right version is computable, and this script compares it with
what BigLinux has published, branch by branch, and dispatches a build of
every module that is behind. The driver version is the one users get: the
first of their repositories that has it -- BigLinux priority, Manjaro,
BigLinux -- in their pacman.conf order, since that is how pacman chooses.

Nothing is edited: the module PKGBUILDs read the driver version from pacman
and the kernel version from ../linux-bigNN/PKGBUILD when they build, so a build
dispatched from the current repository always produces the expected version.

The NVIDIA modules also build for the driver of Manjaro testing, whose users
get a newer nvidia-utils than stable's: while the two differ, the package
carries both builds and its version names both drivers, stable's first
(610.57.04+615.71.09).

A module is only dispatched on a branch once its kernel is published there,
since the build needs the headers of that exact version.

linux-big, linux-big-headers and linux-big-<module> are metapackages that
depend on the same package of every series (see linux-big/PKGBUILD). They are
sent to testing once everything they depend on is there, never before: a
metapackage published ahead of its dependencies would stop pacman -Syu.
"""

import argparse
import calendar
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
import urllib.request

REPOSITORY_URL = "https://github.com/biglinux/big-kernel"
USER_AGENT = f"big-kernel-watch/1 (+{REPOSITORY_URL})"
WORKFLOW_REPOSITORY = "biglinux/build-package"

# Module -> the Manjaro package its driver version follows, or None when the
# version is fixed in the PKGBUILD itself. Each series has a directory per
# module: linux-big73-nvidia-open, linux-big72-nvidia-open, ...
MODULES = {
    "nvidia-open": "nvidia-open-dkms",
    "nvidia": "nvidia-dkms",
    "nvidia-580xx-open": "nvidia-580xx-open-dkms",
    "nvidia-580xx": "nvidia-580xx-dkms",
    "nvidia-390xx": "nvidia-390xx-dkms",
    "broadcom-wl": "broadcom-wl-dkms",
    "virtualbox-host-modules": "virtualbox-host-dkms",
    "zfs": "zfs-dkms",
    "vhba-module": "vhba-module-dkms",
    "v4l2loopback": "v4l2loopback-dkms",
    "acpi_call": "acpi_call-dkms",
    "r8168": None,
    "rtl8723bu": None,
    "tp_smapi": None,
}

# A kernel series' directory and package: linux-big72, linux-big73.
KERNEL_PACKAGE = re.compile(r"linux-big[0-9]+")
# The metapackages' directory and the name of the first of them.
META = "linux-big"
# The Secure Boot package: shim, the signed GRUB and the key. Built when its
# PKGBUILD is ahead of testing; bump_secureboot.py raises its pkgrel when the
# grub or the theme fonts it is built from change.
SECUREBOOT = "big-secureboot"


def kernel_packages(root):
    """The kernel series in the repository, oldest first."""
    found = [
        entry for entry in os.listdir(root)
        if KERNEL_PACKAGE.fullmatch(entry) and os.path.isfile(os.path.join(root, entry, "PKGBUILD"))
    ]
    return sorted(found, key=lambda entry: int(entry[len(META):]))


def module_package(kernel_pkg, module):
    return f"{kernel_pkg}-{module}"


def kernel_of(package):
    """The kernel series a package belongs to: linux-big73-zfs -> linux-big73."""
    match = re.match(r"linux-big[0-9]+", package)
    return match.group(0) if match else None

MANJARO_DB = "https://mirrors.manjaro.org/repo/{branch}/extra/x86_64/extra.db"
# Each repository at <host>/<section>/<arch>, the $repo/$arch layout pacman.conf uses.
BIGLINUX_DB = "https://packages.biglinux.com.br/biglinux-{branch}/x86_64/biglinux-{branch}.db"

# BigLinux branch -> the Manjaro branch its builds run against, the database
# modules are published to, and the repositories the driver version is
# looked up in, in pacman.conf order. pacman takes a package from the first
# repository that has it, so that order decides which driver version users
# get: a driver BigLinux publishes ahead of Manjaro's wins. BigCommunity's
# own repositories, ahead of BigLinux's on its systems, carry no driver.
#
# Both branches follow Manjaro STABLE's drivers: the builds are sent with
# manjaro_branch=stable, so that is the driver a module is really built for. Expecting Manjaro testing's here
# made the watcher dispatch builds that came out with the old driver, record
# the new version as done, and then never rebuild it once Manjaro stable got
# that driver: testing kept a module older than nvidia-utils.
# The modules whose PKGBUILD also builds for Manjaro testing's driver
# (TESTING_MARK) follow that one too, in both branches: see module_driver().
BRANCHES = {
    "stable": (
        "stable",
        BIGLINUX_DB.format(branch="stable"),
        [
            BIGLINUX_DB.format(branch="priority"),
            MANJARO_DB.format(branch="stable"),
            BIGLINUX_DB.format(branch="stable"),
        ],
    ),
    "testing": (
        "stable",
        BIGLINUX_DB.format(branch="testing"),
        [
            BIGLINUX_DB.format(branch="priority"),
            MANJARO_DB.format(branch="stable"),
            BIGLINUX_DB.format(branch="testing"),
            BIGLINUX_DB.format(branch="stable"),
        ],
    ),
}


# A module PKGBUILD that builds for Manjaro testing's driver as well as for
# stable's has this line; one from before that change does not, and the
# stable branch can still be built from such a commit.
TESTING_MARK = re.compile(r"^_manjaro_testing=", re.MULTILINE)
MANJARO_TESTING_DB = MANJARO_DB.format(branch="testing")


# What a user of each branch sees of these packages in BigLinux, lowest
# priority first: a testing user also has stable, and pacman takes testing's
# copy when both have one. Moving the kernel from testing to stable must not
# look, to the watcher, as if testing had lost it.
VIEWS = {
    "stable": [BIGLINUX_DB.format(branch="stable")],
    "testing": [BIGLINUX_DB.format(branch="stable"), BIGLINUX_DB.format(branch="testing")],
}


def stable_ref(kernel_pkg):
    """The branch stable modules of a series are built from when stable's
    kernel is older than the PKGBUILD on main: linux-big73-stable.
    build-package checks out branches by name, and falls back to main when the
    name does not exist, so it has to be a real branch."""
    return f"{kernel_pkg}-stable"


def kernel_pkgbuild(kernel_pkg):
    return f"{kernel_pkg}/PKGBUILD"


class PkgbuildError(Exception):
    """A PKGBUILD without a value the watcher needs, such as a literal pkgver."""


def log(message):
    print(message, flush=True)


def fetch(url):
    # Package servers may refuse urllib's default User-Agent.
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def read_database(data):
    """Return {pkgname: 'pkgver-pkgrel'} from a pacman repository database."""
    versions = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            if not member.name.endswith("/desc"):
                continue
            fields, key = {}, None
            for line in archive.extractfile(member).read().decode().splitlines():
                if line.startswith("%") and line.endswith("%"):
                    key = line.strip("%")
                elif line and key and key not in fields:
                    fields[key] = line
            if "NAME" in fields and "VERSION" in fields:
                versions[fields["NAME"]] = fields["VERSION"]
    return versions


class Tree:
    """The repository's files: as checked out, or as they were at a commit."""

    def __init__(self, root, commit=None):
        self.root, self.commit = root, commit

    def read(self, relative):
        if self.commit is None:
            with open(os.path.join(self.root, relative), encoding="utf-8") as source:
                return source.read()
        return subprocess.run(
            ["git", "-C", self.root, "show", f"{self.commit}:{relative}"],
            capture_output=True, text=True, check=True,
        ).stdout


def text_value(text, key, where):
    match = re.search(rf"^{key}=(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise PkgbuildError(f"{where}: no literal {key}=")
    return match.group(1).strip("'\"")


def pkgbuild_value(path, key):
    with open(path, encoding="utf-8") as pkgbuild:
        return text_value(pkgbuild.read(), key, path)


def kernel_version(text, where="PKGBUILD"):
    return f"{text_value(text, 'pkgver', where)}-{text_value(text, 'pkgrel', where)}"


def find_commit(root, kernel, kernel_pkg):
    """The newest commit on this branch whose kernel_pkg PKGBUILD is `kernel`.

    The last state of the repository for that kernel: what its modules were
    built from, with every module fix made before the next kernel.
    """
    try:
        commits = subprocess.run(
            ["git", "-C", root, "rev-list", "--first-parent", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    for commit in commits:
        try:
            if kernel_version(Tree(root, commit).read(kernel_pkgbuild(kernel_pkg))) == kernel:
                return commit
        except (subprocess.CalledProcessError, PkgbuildError):
            continue  # a commit before the series, or one without a literal version
    return None


def rebuild_suffix(path, kernel):
    with open(path, encoding="utf-8") as pkgbuild:
        return rebuild_suffix_text(pkgbuild.read(), kernel)


def rebuild_suffix_text(text, kernel):
    """".N" when the module PKGBUILD sets _rebuild=N for this kernel, else "".

    Mirrors _pkgrel() in the module PKGBUILDs: a rebuild for the same kernel
    and driver gets a new version, and a new kernel drops it by itself.
    """
    values = {}
    for key in ("_rebuild_for", "_rebuild"):
        match = re.search(rf"^{key}=(\S*)\s*$", text, re.MULTILINE)
        values[key] = match.group(1).strip("'\"") if match else ""
    if values["_rebuild"] and values["_rebuild_for"] == kernel:
        return f".{values['_rebuild']}"
    return ""


def kernel_rel(pkgver, pkgrel):
    """The pkgrel the modules derive from the kernel: 7.2.7-2 -> 7020702."""
    major, minor, *rest = pkgver.split(".")
    patch = rest[0] if rest else "0"
    return f"{int(major)}{int(minor):02d}{int(patch):02d}{int(pkgrel):02d}"


def without_pkgrel(version):
    return version.rsplit("-", 1)[0]


def first_in(search, package, fetch_database):
    """The version of package in the first repository that has it, like pacman."""
    for url in search:
        version = fetch_database(url).get(package)
        if version:
            return version
    return None


# Branches the kernel itself is sent to when the PKGBUILD moves ahead of what
# is published. Only testing: moving a kernel to stable is a person's call.
KERNEL_BRANCHES = ("testing",)


def version_key(version):
    """A sortable key for a linux-big version such as 7.2.8-1."""
    pkgver, _, pkgrel = version.partition("-")
    return tuple(int(part) for part in pkgver.split(".")), int(pkgrel or 0)


def branch_view(branch, fetch_database):
    """These packages, in BigLinux, as a user of `branch` sees them."""
    ours = {}
    for url in VIEWS[branch]:
        ours.update(fetch_database(url))
    return ours


def kernel_behind(branch, kernel, published):
    """Whether the PKGBUILD's kernel should be built for `branch`."""
    return branch in KERNEL_BRANCHES and (published is None or version_key(kernel) > version_key(published))


# Packages Manjaro also ships as <package>-<series>-compat for a kernel series
# newer than the upstream release supports (zfs-dkms-7.3-compat). The module
# PKGBUILD builds from that one while it exists; so does the watcher.
COMPAT_SOURCES = ("zfs-dkms",)


def module_source(source, kernel, search, fetch_database):
    """The package a module follows for the kernel `kernel` (7.3.1-1)."""
    if source in COMPAT_SOURCES:
        series = ".".join(kernel.split("-")[0].split(".")[:2])
        compat = f"{source}-{series}-compat"
        if first_in(search, compat, fetch_database) is not None:
            return compat
    return source


def module_driver(text, source, search, fetch_database):
    """The driver part of a module's version, as its PKGBUILD computes it.

    The driver users of this branch get; for a module that also builds for
    Manjaro testing's driver, "stable+testing" while the one users on Manjaro
    testing get -- the same repositories, Manjaro testing in place of stable
    -- differs. None when a driver is missing.
    """
    found = first_in(search, source, fetch_database)
    if found is None:
        return None
    driver = without_pkgrel(found)
    if TESTING_MARK.search(text):
        manjaro_stable = MANJARO_DB.format(branch="stable")
        testing_search = [MANJARO_TESTING_DB if url == manjaro_stable else url for url in search]
        testing = first_in(testing_search, source, fetch_database)
        if testing is None:
            return None
        if without_pkgrel(testing) != driver:
            driver = f"{driver}+{without_pkgrel(testing)}"
    return driver


def module_builds(branch, manjaro_branch, search, tree, kernel_pkg, branch_kernel, ours, fetch_database):
    """The modules of `branch` behind the drivers its users get."""
    rel = kernel_rel(*branch_kernel.split("-"))
    builds = []
    for name, source in MODULES.items():
        module = module_package(kernel_pkg, name)
        where = f"{module}/PKGBUILD"
        try:
            text = tree.read(where)
        except (OSError, subprocess.CalledProcessError):
            log(f"[{branch}] {module}: not in the repository for {kernel_pkg} {branch_kernel}, skipped")
            continue
        if source is None:
            driver = text_value(text, "pkgver", where)
        else:
            source = module_source(source, branch_kernel, search, fetch_database)
            driver = module_driver(text, source, search, fetch_database)
            if driver is None:
                log(f"[{branch}] {module}: {source} is missing from the {branch} repositories or Manjaro testing, skipped")
                continue
        expected = f"{driver}-{rel}{rebuild_suffix_text(text, branch_kernel)}"
        current = ours.get(module)
        if current == expected:
            log(f"[{branch}] {module} {expected}: up to date")
        else:
            log(f"[{branch}] {module}: published {current}, expected {expected}")
            builds.append((branch, manjaro_branch, module, expected))
    return builds


def plan(root, fetch_database, kernel_pkg, refs=None, find=find_commit):
    """Return the builds of one kernel series as (branch, manjaro_branch, package, expected).

    When stable's kernel is older than the PKGBUILD, its modules are planned
    from the commit that described that kernel, and refs["stable"] is set to
    it: the builds have to run from there.
    """
    head = Tree(root)
    kernel = kernel_version(head.read(kernel_pkgbuild(kernel_pkg)), kernel_pkgbuild(kernel_pkg))
    log(f"{kernel_pkg} in the repository: {kernel} (module pkgrel {kernel_rel(*kernel.split('-'))})")

    builds = []
    for branch, (manjaro_branch, _published_to, search) in BRANCHES.items():
        ours = branch_view(branch, fetch_database)
        published = ours.get(kernel_pkg)

        tree, branch_kernel = head, kernel
        if branch not in KERNEL_BRANCHES and published is not None and published != kernel:
            commit = find(root, published, kernel_pkg)
            if commit is None:
                log(f"[{branch}] {kernel_pkg} {published}: no commit describes it; its modules are not followed")
                continue
            tree, branch_kernel = Tree(root, commit), published
            if refs is not None:
                refs[branch] = commit
            log(f"[{branch}] {kernel_pkg} {published} is behind the PKGBUILD; modules from {commit[:12]}")

        if published == branch_kernel:
            builds += module_builds(branch, manjaro_branch, search, tree, kernel_pkg, branch_kernel, ours,
                                    fetch_database)
        elif kernel_behind(branch, kernel, published):
            # The kernel watcher only commits the new version; building it is
            # decided here, from what is published, so a dispatch that failed
            # is retried on the next run instead of being forgotten.
            log(f"[{branch}] {kernel_pkg}: published {published}, PKGBUILD is {kernel}; building it")
            builds.append((branch, manjaro_branch, kernel_pkg, kernel))
        else:
            log(f"[{branch}] {kernel_pkg} {kernel} is not published (found {published}); modules wait for it")
    return builds


def array_value(text, key, where):
    """The words of a literal bash array: _series=(73 72) -> ["73", "72"]."""
    match = re.search(rf"^{key}=\((.*?)\)", text, re.MULTILINE | re.DOTALL)
    if not match:
        raise PkgbuildError(f"{where}: no literal {key}=()")
    words = []
    for line in match.group(1).splitlines():
        words += line.split("#", 1)[0].split()
    return [word.strip("'\"") for word in words]


def meta_version(text, where=f"{META}/PKGBUILD"):
    """The metapackages' version as pacman's database writes it: 1:7.3-1."""
    version = f"{text_value(text, 'pkgver', where)}-{text_value(text, 'pkgrel', where)}"
    match = re.search(r"^epoch=(\S+)\s*$", text, re.MULTILINE)
    return f"{match.group(1)}:{version}" if match else version


def meta_key(version):
    """A sortable key for a pacman version with an optional epoch, digits only."""
    epoch, _, rest = version.rpartition(":")
    pkgver, _, pkgrel = rest.partition("-")
    return int(epoch or 0), tuple(int(part) for part in pkgver.split(".")), int(pkgrel or 0)


def meta_packages(text, where=f"{META}/PKGBUILD"):
    """What the metapackages depend on, for every series in _series."""
    modules = array_value(text, "_modules", where)
    needed = []
    for series in array_value(text, "_series", where):
        kernel_pkg = f"{META}{series}"
        needed += [kernel_pkg, f"{kernel_pkg}-headers", *(module_package(kernel_pkg, m) for m in modules)]
    return needed


def plan_meta(root, fetch_database):
    """The metapackages' build for testing, once all they depend on is there."""
    where = f"{META}/PKGBUILD"
    try:
        text = Tree(root).read(where)
    except OSError:
        return []
    expected = meta_version(text, where)
    ours = branch_view("testing", fetch_database)
    published = ours.get(META)
    if published == expected:
        log(f"[testing] {META} {expected}: up to date")
        return []
    if published is not None and meta_key(published) > meta_key(expected):
        log(f"[testing] {META}: published {published} is newer than the PKGBUILD's {expected}; left alone")
        return []
    missing = [package for package in meta_packages(text, where) if package not in ours]
    if missing:
        log(f"[testing] {META} {expected} waits for {len(missing)} package(s): {', '.join(missing)}")
        return []
    log(f"[testing] {META}: published {published}, PKGBUILD is {expected}; building it")
    return [("testing", "stable", META, expected)]


def plan_secureboot(root, fetch_database):
    """big-secureboot's build for testing, when its PKGBUILD is ahead.

    Like the metapackages, it is moved to stable by a person.
    """
    where = f"{SECUREBOOT}/PKGBUILD"
    try:
        text = Tree(root).read(where)
    except OSError:
        return []
    expected = f"{text_value(text, 'pkgver', where)}-{text_value(text, 'pkgrel', where)}"
    published = branch_view("testing", fetch_database).get(SECUREBOOT)
    if published == expected:
        log(f"[testing] {SECUREBOOT} {expected}: up to date")
        return []
    if published is not None and version_key(published) > version_key(expected):
        log(f"[testing] {SECUREBOOT}: published {published} is newer than the PKGBUILD's {expected}; left alone")
        return []
    log(f"[testing] {SECUREBOOT}: published {published}, PKGBUILD is {expected}; building it")
    return [("testing", "stable", SECUREBOOT, expected)]


def plan_all(root, fetch_database, refs=None, find=find_commit):
    """Every series' builds, then the metapackages' and big-secureboot's.
    refs[kernel_pkg] is plan()'s refs."""
    builds = []
    for kernel_pkg in kernel_packages(root):
        series_refs = {}
        builds += plan(root, fetch_database, kernel_pkg, refs=series_refs, find=find)
        if series_refs and refs is not None:
            refs[kernel_pkg] = series_refs
    return builds + plan_meta(root, fetch_database) + plan_secureboot(root, fetch_database)


def payload(branch, manjaro_branch, module, ref="main"):
    """The dispatch gitrepo sends for a package, pointed at one directory."""
    data = {
        "branch": ref,
        "branch_type": branch,
        "build_env": "normal",
        "url": REPOSITORY_URL,
        "tmate": False,
        "pkgbuild_dir": module,
        "manjaro_branch": manjaro_branch,
    }
    if branch == "testing":
        data["new_branch"] = "main"
    return {"event_type": module, "client_payload": data}


# A build that did not publish is dispatched again, up to MAX_ATTEMPTS in all:
# most failures are the network (2026-10-18: a module's build could not
# download linux-big from repo.communitybig.org, "Connection reset by peer").
# RETRY_AFTER is how long a dispatch is given to show up as a run and, once
# built, in the repository database. A build still queued or running is never
# dispatched again (building()), so a kernel's four hours are not cut short.
MAX_ATTEMPTS = 3
RETRY_AFTER = 30 * 60


def attempt(state, key):
    """(attempts, last dispatch as epoch seconds, gave_up) recorded for key.

    A plain timestamp is the format before retries existed: one attempt.
    """
    entry = state.get(key)
    if entry is None:
        return 0, None, False
    if isinstance(entry, str):
        entry = {"at": entry, "attempts": 1}
    at = calendar.timegm(time.strptime(entry["at"], "%Y-%m-%dT%H:%M:%SZ"))
    return entry.get("attempts", 1), at, entry.get("gave_up", False)


def pending(builds, state, force=False, now=None):
    """Split the builds behind into (to dispatch, given up).

    A version is dispatched once; while it is still behind, it is dispatched
    again RETRY_AFTER after the last attempt, up to MAX_ATTEMPTS. force
    dispatches it whatever was recorded.
    """
    now = time.time() if now is None else now
    todo, given_up = [], []
    for build in builds:
        attempts, at, _gave_up = attempt(state, state_key(*build))
        if force or attempts == 0:
            todo.append(build)
        elif now - at < RETRY_AFTER:
            continue
        elif attempts < MAX_ATTEMPTS:
            todo.append(build)
        else:
            given_up.append(build)
    return todo, given_up


def state_key(branch, _manjaro_branch, module, expected):
    return f"{branch}/{module}/{expected}"


def dispatch(token, body):
    request = urllib.request.Request(
        f"https://api.github.com/repos/{WORKFLOW_REPOSITORY}/dispatches",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.status


def building(token):
    """Packages build-package is building or has queued right now.

    A second guard besides the dispatch state: the state lives in the
    Actions cache, which a re-run or an eviction can lose, and a kernel
    dispatched twice is built twice, four hours each.
    """
    names = set()
    for status in ("queued", "in_progress", "waiting", "pending"):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if token:  # build-package is public: listing its runs works without one too
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            f"https://api.github.com/repos/{WORKFLOW_REPOSITORY}/actions/runs?status={status}&per_page=100",
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            runs = json.load(response).get("workflow_runs", [])
        names.update(run.get("display_title", "") for run in runs)
    return names


def wait_for_build(expected, published, in_flight, sleep=time.sleep, now=time.monotonic,
                   minutes=330, poll=300, grace=1200):
    """Wait until version `expected` of a package is published.

    Returns "published", "failed" when build-package is no longer building
    it and it is not published (after `grace` seconds, the time a dispatch
    takes to show up as a run), or "timeout".
    """
    start = now()
    while True:
        if published() == expected:
            return "published"
        waited = now() - start
        if waited >= grace and not in_flight():
            return "failed"
        if waited >= minutes * 60:
            return "timeout"
        sleep(poll)


# What a run dispatched, handed to the job that follows it: BRANCH:PACKAGE=VERSION,
# a kernel, one of its modules, the metapackage (testing:linux-big=1:7.3-1) or
# big-secureboot.
FOLLOWED = re.compile(
    r"(stable|testing):(linux-big(?:[0-9]+(?:-[A-Za-z0-9._+-]+)?)?|big-secureboot)=([A-Za-z0-9._+:~-]+)"
)


def follow_builds(items, token, wait=wait_for_build, sleep=time.sleep, now=time.monotonic):
    """Wait for the builds a watcher run dispatched; how many did not publish.

    The workflow runs the watcher again right after, so a build that failed
    (most often the network) is dispatched again within the hour instead of
    whenever GitHub next runs the schedule, which has been hours apart.
    The same run dispatches the modules of a kernel just published, and the
    metapackage once its modules are there. When a build failed this returns
    only once RETRY_AFTER has passed since the dispatch: before that the
    watcher counts it as dispatched recently and waits for it.
    """
    start = now()
    builds = []
    for item in items:
        match = FOLLOWED.fullmatch(item)
        if not match:
            raise SystemExit(f"--follow: expected BRANCH:PACKAGE=VERSION, got {item!r}")
        builds.append(match.groups())
    failed = 0
    # One after the other: they build at the same time, so the later waits
    # are short by the time the first one ends. A kernel takes hours.
    for branch, package, version in builds:
        database = BRANCHES[branch][1]
        result = wait(
            version,
            published=lambda database=database, package=package: read_database(fetch(database)).get(package),
            in_flight=lambda package=package: package in building(token),
            minutes=330 if package == kernel_of(package) else 120,
        )
        log(f"[{branch}] {package} {version}: {result}")
        failed += result != "published"
    if failed:
        left = RETRY_AFTER + 60 - (now() - start)
        if left > 0:
            log(f"{failed} build(s) did not publish; waiting {int(left)}s so the watcher dispatches them again")
            sleep(left)
    return failed


def point_branch(root, name, commit):
    """Make branch `name` on GitHub point at `commit`, for build-package."""
    # `commit` comes from git rev-list, `name` from stable_ref(); checked anyway,
    # since both end up in a git command line.
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError(f"refusing to point {name!r} at {commit!r}")
    subprocess.run(["git", "-C", root, "push", "--force", "origin", f"{commit}:refs/heads/{name}"], check=True)
    log(f"branch {name} -> {commit[:12]}")


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as out:
        json.dump(state, out, indent=2, sort_keys=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="the big-kernel repository")
    parser.add_argument("--state", default="watch-state.json", help="builds already dispatched")
    parser.add_argument("--dry-run", action="store_true", help="report, dispatch nothing")
    parser.add_argument("--force", action="store_true", help="dispatch even what was dispatched before")
    parser.add_argument("--follow", metavar="BRANCH:PACKAGE=VERSION", nargs="+",
                        help="wait for these builds (testing:linux-big73-zfs=2.4.4-7030301) to publish or fail")
    args = parser.parse_args()

    if args.follow:
        failed = follow_builds(args.follow, os.environ.get("DISPATCH_TOKEN", ""))
        if failed:
            # A warning, not a failure: the watcher dispatches them again, and
            # fails its own run once a build is given up after three attempts.
            print(f"::warning::{failed} build(s) did not publish, dispatching them again", flush=True)
        return 0

    cache = {}

    def fetch_database(url):
        if url not in cache:
            cache[url] = read_database(fetch(url))
        return cache[url]

    refs = {}
    builds = plan_all(args.root, fetch_database, refs=refs)

    try:
        state = json.load(open(args.state, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    todo, given_up = pending(builds, state, args.force)
    for build in builds:
        if build not in todo and build not in given_up:
            attempts, _at, _gave_up = attempt(state, state_key(*build))
            log(f"[{build[0]}] {build[2]} {build[3]}: attempt {attempts} of {MAX_ATTEMPTS} dispatched recently, waiting for it")
    newly_given_up = []
    for build in given_up:
        key = state_key(*build)
        log(f"[{build[0]}] {build[2]} {build[3]}: still not published after {MAX_ATTEMPTS} attempts, given up")
        if not attempt(state, key)[2]:
            newly_given_up.append(build)
            if not args.dry_run:
                entry = state[key] if isinstance(state[key], dict) else {"at": state[key], "attempts": 1}
                state[key] = dict(entry, gave_up=True)

    token = os.environ.get("DISPATCH_TOKEN", "")
    active = building(token) if todo and token and not args.dry_run else set()
    dispatched = 0
    pointed = set()
    followed = []
    for branch, manjaro_branch, module, expected in todo:
        if module in active:
            log(f"[{branch}] {module}: a build of it is already queued or running, not dispatched again")
            continue
        key = state_key(branch, manjaro_branch, module, expected)
        ref = "main"
        kernel_pkg = kernel_of(module)
        series_refs = refs.get(kernel_pkg, {})
        if branch in series_refs:
            ref = stable_ref(kernel_pkg)
            if ref not in pointed and not args.dry_run:
                point_branch(args.root, ref, series_refs[branch])
            pointed.add(ref)
        body = payload(branch, manjaro_branch, module, ref)
        if args.dry_run:
            log(f"[{branch}] would dispatch {module}: {json.dumps(body['client_payload'])}")
            continue
        if not token:
            raise SystemExit("DISPATCH_TOKEN is not set")
        attempts = 1 if args.force else attempt(state, key)[0] + 1
        status = dispatch(token, body)
        log(f"[{branch}] dispatched {module} {expected}, attempt {attempts} of {MAX_ATTEMPTS} (HTTP {status})")
        state[key] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "attempts": attempts}
        dispatched += 1
        followed.append(f"{branch}:{module}={expected}")
        # Saved after each one: if a later dispatch fails, the ones already
        # sent are not sent again.
        save_state(args.state, state)

    if followed:
        # The workflow waits for them and then runs this watcher again: the
        # modules follow a kernel, and a failed build is retried, without
        # waiting for the schedule.
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as out:
                out.write(f"follow={' '.join(followed)}\n")

    if not args.dry_run:
        save_state(args.state, state)
    log(f"{len(builds)} package(s) behind, {dispatched} dispatched")
    if newly_given_up:
        # Fails this run once, so GitHub mails it; later runs only log it.
        names = ", ".join(f"{b[0]}/{b[2]} {b[3]}" for b in newly_given_up)
        print(f"::error::not published after {MAX_ATTEMPTS} attempts: {names}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PkgbuildError as error:
        sys.exit(f"error: {error}")
