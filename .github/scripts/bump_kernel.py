#!/usr/bin/env python3
"""Move a linux-big series to the newest kernel.org release of that series.

Runs unattended, once per series (--kernel linux-big73). When kernel.org has
a newer stable release of the series in <kernel>/PKGBUILD (_basekernel), it:

  1. sets pkgver to it and pkgrel to 1, with the checksum of its patch;
  2. drops the patches that reached the kernel in that release: applied in
     order on the new tree, those that no longer apply but reverse;
  3. runs the PKGBUILD's own prepare() through makepkg -o, which applies
     every patch and checks every option linux-big exists for.

The result is written to --result as JSON for the workflow: "current" (no
newer release), "bumped" (the PKGBUILD was updated and prepare() passed),
"failed" (it did not; nothing should be built) or "eol" (the series reached
its end of life and moving to the next one is a decision for a person). The
last release of a series comes already marked end of life: it is bumped to
all the same, with "eol": true in the result. "kernel" names the series'
package.
"""

import argparse
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

RELEASES_URL = "https://www.kernel.org/releases.json"
CDN = "https://cdn.kernel.org/pub/linux/kernel/v{major}.x"
USER_AGENT = "big-kernel-watch/1 (+https://github.com/biglinux/big-kernel)"


def log(message):
    print(message, flush=True)


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def pkgbuild_value(text, key):
    match = re.search(rf"^{key}=(\S+)\s*$", text, re.MULTILINE)
    if not match:
        raise SystemExit(f"PKGBUILD: no literal {key}=")
    return match.group(1).strip("'\"")


def latest_in_series(releases, series):
    """Return (newest release of series, whether the series is end of life).

    A series kernel.org no longer lists is past its end of life. Before its
    first stable release the series is listed as mainline, as plain "7.3".
    """
    found = [
        release
        for release in releases.get("releases", [])
        if release.get("moniker") in ("mainline", "stable", "longterm")
        and (release["version"] == series or release["version"].startswith(series + "."))
    ]
    if not found:
        return None, True
    newest = max(found, key=lambda release: version_key(release["version"]))
    return newest["version"], bool(newest.get("iseol"))


def _with_eol(finish, reason):
    """finish() that also marks the result as the series' last release."""
    return lambda status, **details: finish(status, eol=True, reason=reason, **details)


def source_entries(text):
    """The source=() entries in order, without comments."""
    block = re.search(r"^source=\((.*?)^\)", text, re.S | re.M).group(1)
    return [line.strip() for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]


def checksums(text):
    block = re.search(r"^sha256sums=\((.*?)\)", text, re.S | re.M).group(1)
    return re.findall(r"'([0-9a-f]{64}|SKIP)'", block)


def set_checksums(text, sums):
    body = "sha256sums=(" + "\n            ".join(f"'{s}'" for s in sums) + ")"
    return re.sub(r"^sha256sums=\(.*?\)", lambda _m: body, text, count=1, flags=re.S | re.M)


def patch_index(text):
    """Where the stable patch (patch-${pkgver}.xz) sits in source=()."""
    for index, entry in enumerate(source_entries(text)):
        if "patch-${pkgver}" in entry:
            return index
    raise SystemExit("PKGBUILD: no patch-${pkgver} in source=()")


def bump(text, version, patch_sha256):
    """The PKGBUILD moved to version, with pkgrel 1 and the new patch checksum."""
    text = re.sub(r"^pkgver=\S+$", f"pkgver={version}", text, count=1, flags=re.M)
    text = re.sub(r"^pkgrel=\S+$", "pkgrel=1", text, count=1, flags=re.M)
    sums = checksums(text)
    sums[patch_index(text)] = patch_sha256
    return set_checksums(text, sums)


def drop_patch(text, name):
    """The PKGBUILD without one patch in source=() and its checksum."""
    entries = source_entries(text)
    index = entries.index(name)
    sums = checksums(text)
    del sums[index]
    text = re.sub(rf"^[ \t]+{re.escape(name)}\n", "", text, count=1, flags=re.M)
    return set_checksums(text, sums)


def applies(tree, patch, reverse=False):
    command = ["patch", "-p1", "--dry-run", "-s", "-f"] + (["-R"] if reverse else ["-N"])
    with open(patch, "rb") as data:
        return subprocess.run(command, cwd=tree, stdin=data, capture_output=True).returncode == 0


def upstreamed_patches(tree, patches):
    """The patches the new release already contains, in source=() order.

    They are applied one after another, as prepare() does, because a patch
    is written on top of the ones before it: on a bare tree its context would
    not match either way. One that does not apply but reverses is already in
    the kernel. The first that does neither stops the search; prepare() then
    fails on it and names it.
    """
    found = []
    for patch in patches:
        if applies(tree, patch):
            with open(patch, "rb") as data:
                subprocess.run(["patch", "-p1", "-N", "-s"], cwd=tree, stdin=data, check=True)
        elif applies(tree, patch, reverse=True):
            found.append(patch)
        else:
            break
    return found


def failure_reasons(output):
    """The lines of a makepkg run that say why it failed.

    patch does not name the file it fails on, so each failure is preceded by
    the "Applying patch" line prepare() printed before it.
    """
    reasons, applying = [], None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Applying "):
            applying = line
        elif re.search(r"FAILED|can't find file|Reversed|did not survive|ERROR", line):
            if applying and applying not in reasons:
                reasons.append(applying)
            reasons.append(line)
    return reasons[:30]


def prepare_tree(workdir, tarball, stable_patch):
    """The new kernel tree: the base release with the stable patch applied."""
    with tarfile.open(tarball) as archive:
        archive.extractall(workdir, filter="tar")
    tree = os.path.join(workdir, sorted(os.listdir(workdir))[0])
    # Decompressed here: given as stdin, an lzma file hands patch its raw,
    # still compressed, descriptor.
    with lzma.open(stable_patch) as data:
        subprocess.run(["patch", "-p1", "-s"], cwd=tree, input=data.read(), check=True)
    return tree


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="the big-kernel repository")
    parser.add_argument("--kernel", required=True, help="the series' directory: linux-big73")
    parser.add_argument("--cache", default=os.path.expanduser("~/.cache/big-kernel"), help="downloads")
    parser.add_argument("--result", default="bump-result.json")
    parser.add_argument("--releases", help="a releases.json to use instead of kernel.org's")
    parser.add_argument("--no-prepare", action="store_true", help="skip makepkg -o")
    args = parser.parse_args()

    # Before any early return: the workflow caches this directory, and a run
    # with nothing to download must still leave it there.
    os.makedirs(args.cache, exist_ok=True)
    if not re.fullmatch(r"linux-big[0-9]+", args.kernel):
        raise SystemExit(f"--kernel: expected linux-bigNN, got {args.kernel!r}")
    pkgbuild = os.path.join(args.root, args.kernel, "PKGBUILD")
    text = open(pkgbuild, encoding="utf-8").read()
    current = pkgbuild_value(text, "pkgver")
    series = pkgbuild_value(text, "_basekernel")

    def finish(status, **details):
        result = {"status": status, "kernel": args.kernel, "series": series, "from": current, **details}
        json.dump(result, open(args.result, "w", encoding="utf-8"), indent=2)
        log(json.dumps(result, indent=2))
        return 0

    releases = json.load(open(args.releases)) if args.releases else json.loads(fetch(RELEASES_URL))
    latest, eol = latest_in_series(releases, series)
    reason = f"kernel.org lists {series} as end of life"
    # A series' last release is published already marked end of life (7.2.15
    # on 2026-11-01): it is still taken, then the end of life is reported.
    if latest is None or (eol and version_key(latest) <= version_key(current)):
        return finish("eol", to=latest, reason=reason)
    if version_key(latest) <= version_key(current):
        return finish("current", to=latest)
    if eol:
        finish = _with_eol(finish, reason)
    log(f"{args.kernel} {current} -> {latest}")

    cdn = CDN.format(major=series.split(".")[0])
    downloads = {}
    for name in (f"linux-{series}.tar.xz", f"patch-{latest}.xz"):
        path = os.path.join(args.cache, name)
        if not os.path.exists(path):
            log(f"downloading {name}")
            data = fetch(f"{cdn}/{name}")
            with open(path + ".part", "wb") as out:
                out.write(data)
            os.replace(path + ".part", path)
        downloads[name] = path
    stable_patch = downloads[f"patch-{latest}.xz"]
    patch_sha256 = hashlib.sha256(open(stable_patch, "rb").read()).hexdigest()

    text = bump(text, latest, patch_sha256)

    kernel_dir = os.path.dirname(pkgbuild)
    dropped = []
    with tempfile.TemporaryDirectory() as workdir:
        tree = prepare_tree(workdir, downloads[f"linux-{series}.tar.xz"], stable_patch)
        patches = [os.path.join(kernel_dir, e) for e in source_entries(text) if e.endswith(".patch")]
        for patch in upstreamed_patches(tree, patches):
            log(f"{os.path.basename(patch)}: already in {latest}, dropped")
            dropped.append(os.path.basename(patch))
    for name in dropped:
        text = drop_patch(text, name)
        os.remove(os.path.join(kernel_dir, name))
    with open(pkgbuild, "w", encoding="utf-8") as out:
        out.write(text)

    if args.no_prepare:
        return finish("bumped", to=latest, dropped=dropped, prepared=False)

    # prepare() applies every patch and checks every required option. makepkg
    # finds the downloads next to the PKGBUILD instead of fetching them again.
    for path in downloads.values():
        shutil.copy(path, kernel_dir)
    run = subprocess.run(
        ["makepkg", "--nobuild", "--nocheck", "--syncdeps", "--noconfirm", "--noprogressbar", "--cleanbuild"],
        cwd=kernel_dir,
        capture_output=True,
        text=True,
    )
    for path in downloads.values():
        os.remove(os.path.join(kernel_dir, os.path.basename(path)))
    shutil.rmtree(os.path.join(kernel_dir, "src"), ignore_errors=True)
    if run.returncode != 0:
        return finish("failed", to=latest, dropped=dropped, reasons=failure_reasons(run.stdout + run.stderr))
    return finish("bumped", to=latest, dropped=dropped, prepared=True)


if __name__ == "__main__":
    sys.exit(main())
