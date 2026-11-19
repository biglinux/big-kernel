#!/usr/bin/env python3
"""Raise big-secureboot's pkgrel when what its GRUB is built from changes.

The signed GRUB image is made in build() from the build's grub package (its
modules, and its version in the SBAT section) and carries the fonts of the
grub-theme-biglinux and grub-theme-community packages. A new grub from
Manjaro, a security fix most often, reaches users only through a new
big-secureboot. _built_with in the PKGBUILD records the versions the last
release was built from; when a repository a testing build installs from
has another one, this script writes the new versions and raises pkgrel.
The workflow commits that, and the module watcher (watch_manjaro.py) sees
the PKGBUILD ahead of testing and sends it to be built.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import watch_manjaro as watch  # noqa: E402

PKGBUILD = os.path.join(watch.SECUREBOOT, "PKGBUILD")
MANJARO = "https://mirrors.manjaro.org/repo/stable/{repo}/x86_64/{repo}.db"
COMMUNITY = "https://repo.communitybig.org/community-{branch}/x86_64/community-{branch}.db"
# pacman.conf as build-package writes it for a testing build against Manjaro
# stable: Manjaro's own repositories, then the ones it appends, in its order
# (scripts/build-package.sh, setup_pacman_conf). pacman takes a package from
# the first repository that has it.
SEARCH = [
    MANJARO.format(repo="core"),
    MANJARO.format(repo="extra"),
    COMMUNITY.format(branch="testing"),
    COMMUNITY.format(branch="stable"),
    watch.BIGLINUX_DB.format(branch="testing"),
    watch.BIGLINUX_DB.format(branch="stable"),
    watch.BIGLINUX_DB.format(branch="extra"),
]

BUILT_WITH = re.compile(r"^_built_with=\(\n(?P<body>(?:[ \t]+'[^'\n]+'\n)+)\)$", re.MULTILINE)
PKGREL = re.compile(r"^pkgrel=([0-9]+)$", re.MULTILINE)


def built_with(text):
    """{package: version} from the PKGBUILD's _built_with array."""
    match = BUILT_WITH.search(text)
    if not match:
        raise SystemExit(f"{PKGBUILD}: no _built_with=( ... ) with one 'package=version' a line")
    pairs = re.findall(r"'([^'=]+)=([^']+)'", match.group("body"))
    return dict(pairs)


def current(packages, fetch_database):
    """The version of each package a testing build would install."""
    found = {}
    for package in packages:
        version = watch.first_in(SEARCH, package, fetch_database)
        if version is None:
            raise SystemExit(f"{package}: in none of the repositories a build installs from")
        found[package] = version
    return found


def rewrite(text, versions):
    """The PKGBUILD with these versions in _built_with and pkgrel raised by one."""
    body = "".join(f"  '{package}={version}'\n" for package, version in versions.items())
    text = BUILT_WITH.sub(lambda _m: f"_built_with=(\n{body})", text, count=1)
    pkgrel = int(PKGREL.search(text).group(1))
    return PKGREL.sub(f"pkgrel={pkgrel + 1}", text, count=1)


def bump(root, fetch_database):
    """Update the PKGBUILD if needed; what changed, for the commit message."""
    path = os.path.join(root, PKGBUILD)
    with open(path, encoding="utf-8") as source:
        text = source.read()
    recorded = built_with(text)
    now = current(recorded, fetch_database)
    changed = {package: (recorded[package], now[package]) for package in recorded if recorded[package] != now[package]}
    pkgver = watch.text_value(text, "pkgver", PKGBUILD)
    pkgrel = watch.text_value(text, "pkgrel", PKGBUILD)
    result = {"status": "current", "from": f"{pkgver}-{pkgrel}", "changed": changed}
    if changed:
        text = rewrite(text, now)
        with open(path, "w", encoding="utf-8") as out:
            out.write(text)
        result.update(status="bumped", to=f"{pkgver}-{watch.text_value(text, 'pkgrel', PKGBUILD)}")
        for package, (old, new) in changed.items():
            watch.log(f"{package}: {old} -> {new}")
        watch.log(f"{watch.SECUREBOOT}: {result['from']} -> {result['to']}")
    else:
        watch.log(f"{watch.SECUREBOOT} {result['from']}: built from the current {', '.join(recorded)}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="the big-kernel repository")
    parser.add_argument("--result", help="write what happened here, as JSON")
    args = parser.parse_args()
    cache = {}

    def fetch_database(url):
        if url not in cache:
            cache[url] = watch.read_database(watch.fetch(url))
        return cache[url]

    result = bump(args.root, fetch_database)
    if args.result:
        with open(args.result, "w", encoding="utf-8") as out:
            json.dump(result, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
