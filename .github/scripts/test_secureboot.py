"""Every package of every series is signed for Secure Boot.

Booted with Secure Boot, linux-big runs locked down, where a module that is
not signed does not load: one package that forgets to sign leaves its users
without that driver. The signing itself is
secureboot/sign.sh; this checks every PKGBUILD calls it, and in the right
place, so a new series or module cannot ship without it.

Run from the repository root: python3 -m unittest discover -s .github/scripts
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SOURCE = "source ../secureboot/sign.sh"
CERT = os.path.join(ROOT, "secureboot", "biglinux-secureboot-2026.crt")


def pkgbuilds(pattern):
    for name in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, name, "PKGBUILD")
        if re.fullmatch(pattern, name) and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                yield name, f.read()


def function(text, name):
    match = re.search(rf"^{re.escape(name)}\(\) {{\n(.*?)^}}", text, re.M | re.S)
    return match.group(1) if match else ""


class TestKernels(unittest.TestCase):
    def test_the_kernel_image_is_signed_once_installed(self):
        kernels = list(pkgbuilds(r"linux-big[0-9]+"))
        self.assertTrue(kernels)
        for name, text in kernels:
            with self.subTest(name):
                self.assertIn(SOURCE, text)
                body = function(text, "_package")
                install = body.index('"$modulesdir/vmlinuz"\n')
                self.assertTrue(body[install:].split("\n", 2)[1].strip() == '_sb_sign_kernel "$modulesdir/vmlinuz"',
                                "_sb_sign_kernel must follow the vmlinuz install")

    def test_sbsigntools_is_needed_only_with_a_key(self):
        for name, text in pkgbuilds(r"linux-big[0-9]+"):
            with self.subTest(name):
                self.assertNotRegex(text, r"(?m)^\s+sbsigntools$", "sbsigntools must not be an unconditional makedepend")
                self.assertIn("makedepends+=(sbsigntools)", text)


class TestModules(unittest.TestCase):
    def test_every_module_is_signed_before_it_is_compressed(self):
        modules = list(pkgbuilds(r"linux-big[0-9]+-.+"))
        self.assertTrue(modules)
        for name, text in modules:
            with self.subTest(name):
                self.assertIn(SOURCE, text)
                body = function(text, "package")
                self.assertIn('_sb_sign_modules "$pkgdir" "$_kernver"', body)
                sign = body.index("_sb_sign_modules")
                for compress in re.finditer(r"-exec (zstd|xz|gzip)\b", body):
                    self.assertLess(sign, compress.start(), "modules must be signed before compression")


class TestLockdown(unittest.TestCase):
    """Booted with Secure Boot, linux-big locks itself down (integrity), as
    Easy Anti-Cheat requires: only signed modules load."""

    PATCH = "0300-efi-lock-down-the-kernel-in-Secure-Boot-mode.patch"

    def test_every_series_carries_the_patch_and_the_options(self):
        kernels = list(pkgbuilds(r"linux-big[0-9]+"))
        self.assertTrue(kernels)
        for name, text in kernels:
            with self.subTest(name):
                self.assertIn(self.PATCH, text)
                self.assertTrue(os.path.isfile(os.path.join(ROOT, name, self.PATCH)))
                with open(os.path.join(ROOT, name, "config"), encoding="utf-8") as f:
                    config = f.read()
                for option in ("CONFIG_SECURITY_LOCKDOWN_LSM_EARLY=y", "CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT=y"):
                    self.assertIn(option + "\n", config)
                    self.assertIn("  " + option + "\n", text, "prepare() must check it survives")


class TestCertificate(unittest.TestCase):
    def test_the_certificate_names_the_signer_sign_sh_checks(self):
        with open(os.path.join(ROOT, "secureboot", "sign.sh"), encoding="utf-8") as f:
            signer = re.search(r'^_sb_signer="(.*)"$', f.read(), re.M).group(1)
        subject = subprocess.run(["openssl", "x509", "-in", CERT, "-noout", "-subject", "-nameopt", "multiline"],
                                 capture_output=True, text=True, check=True).stdout
        self.assertRegex(subject, rf"commonName\s+= {re.escape(signer)}\n")


class TestPackage(unittest.TestCase):
    """big-secureboot: what the package ships is there and consistent."""

    DIR = os.path.join(ROOT, "big-secureboot")

    def setUp(self):
        with open(os.path.join(self.DIR, "PKGBUILD"), encoding="utf-8") as f:
            self.text = f.read()

    def array(self, name):
        body = re.search(rf"^{name}=\((.*?)\)", self.text, re.M | re.S).group(1)
        return re.findall(r"'([^']*)'|\"([^\"]*)\"|(\S+)", body)

    def test_every_local_source_exists_and_has_a_checksum(self):
        sources = [next(v for v in m if v) for m in self.array("source")]
        sums = [next(v for v in m if v) for m in self.array("sha256sums")]
        self.assertEqual(len(sources), len(sums))
        for source in sources:
            if "://" not in source:
                self.assertTrue(os.path.isfile(os.path.join(self.DIR, source)), source)

    def test_the_install_script_runs_commands_the_tool_has(self):
        with open(os.path.join(self.DIR, "big-secureboot"), encoding="utf-8") as f:
            tool = f.read()
        commands = set(re.findall(r"^  (\w+)\)", tool, re.M))
        options = set(re.findall(r"^      (--[\w-]+)\)", tool, re.M))
        self.assertIn("install=big-secureboot.install", self.text)
        with open(os.path.join(self.DIR, "big-secureboot.install"), encoding="utf-8") as f:
            script = f.read()
        for name in ("post_install", "post_upgrade", "pre_remove"):
            self.assertIn(name + "()", script)
        for command, option in re.findall(r"/usr/bin/big-secureboot (\w+) ?(--[\w-]+)?", script):
            self.assertIn(command, commands)
            if option:
                self.assertIn(option, options)

    def test_the_grub_has_no_serial_command(self):
        modules = re.search(r"^_grub_modules=\((.*?)\)", self.text, re.M | re.S).group(1).split()
        self.assertNotIn("serial", modules, "CVE-2026-97876 went through the serial command")
        for needed in ("btrfs", "luks2", "argon2", "tpm", "gfxmenu", "png"):
            self.assertIn(needed, modules)

    def test_what_easy_anti_cheat_checks_besides_secure_boot(self):
        with open(os.path.join(self.DIR, "90-big-secureboot.cfg"), encoding="utf-8") as f:
            grub = f.read()
        self.assertIn("intel_iommu=on", grub)
        self.assertIn("iommu.passthrough=0", grub)
        self.assertIn('${GRUB_CMDLINE_LINUX_DEFAULT}', grub, "the user's own options must stay")
        with open(os.path.join(self.DIR, "90-big-secureboot.conf"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"(?m)^kernel\.kexec_load_disabled = 1$")
        for name in ("90-big-secureboot.cfg", "90-big-secureboot.conf"):
            self.assertIn(name, self.text)

# The CLI's functions with a stand-in mokutil that keeps its lists in files:
# what is enrolled, what an import or a delete scheduled.
FAKE_MOKUTIL = r"""
mok() { cat "$STATE/$1" 2>/dev/null; }
fp() { openssl x509 -inform DER -in "$1" -noout -fingerprint -sha1 | sed 's/.*=//'; }
mokutil() {
  case $1 in
    --list-enrolled) sed 's/^/SHA1 Fingerprint: /' "$STATE/enrolled" 2>/dev/null ;;
    --list-new) sed 's/^/SHA1 Fingerprint: /' "$STATE/new" 2>/dev/null ;;
    --list-delete) sed 's/^/SHA1 Fingerprint: /' "$STATE/delete" 2>/dev/null ;;
    --import) cat >/dev/null; fp "$2" >> "$STATE/new" ;;
    --delete) cat >/dev/null; fp "$2" >> "$STATE/delete" ;;
    --revoke-import) : > "$STATE/new" ;;
  esac
}
"""


class TestKeySwap(unittest.TestCase):
    """2026-11-19: the BigCommunity key gave way to the BigLinux key."""

    DIR = os.path.join(ROOT, "big-secureboot")

    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work)
        share = os.path.join(self.work, "share")
        os.makedirs(os.path.join(share, "retired"))
        self.cert = os.path.join(share, "big-secureboot.der")
        der(CERT, self.cert)
        self.retired = []
        for name in sorted(os.listdir(os.path.join(self.DIR, "retired"))):
            out = os.path.join(share, "retired", name.replace(".crt", ".der"))
            der(os.path.join(self.DIR, "retired", name), out)
            self.retired.append(out)
        self.state = os.path.join(self.work, "state")
        os.mkdir(self.state)
        with open(os.path.join(self.DIR, "big-secureboot"), encoding="utf-8") as f:
            script = f.read()
        # Its functions only: everything before the command dispatch.
        self.functions = os.path.join(self.work, "functions.sh")
        with open(self.functions, "w", encoding="utf-8") as f:
            f.write(script[: script.index("\ncase ${1:-} in")])
            f.write(f"\nSHARE={share}; CERT=$SHARE/big-secureboot.der; RETIRED=$SHARE/retired\n")
            f.write(FAKE_MOKUTIL)

    def enroll(self, *certs):
        with open(os.path.join(self.state, "enrolled"), "w", encoding="utf-8") as f:
            for cert in certs:
                f.write(fingerprint(cert) + "\n")

    def run_swap(self):
        out = subprocess.run(["bash", "-c", f"source {self.functions}; swap_keys"],
                             env=dict(os.environ, STATE=self.state), capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        lists = {}
        for name in ("new", "delete"):
            try:
                with open(os.path.join(self.state, name), encoding="utf-8") as f:
                    lists[name] = f.read().split()
            except FileNotFoundError:
                lists[name] = []
        return out.stdout, lists

    def test_there_is_a_retired_key_and_it_is_not_the_current_one(self):
        self.assertTrue(self.retired)
        self.assertNotIn(fingerprint(self.cert), [fingerprint(old) for old in self.retired])

    def test_a_machine_with_the_old_key_gets_the_new_one_and_loses_the_old(self):
        self.enroll(self.retired[0])
        stdout, lists = self.run_swap()
        self.assertEqual(lists, {"new": [fingerprint(self.cert)], "delete": [fingerprint(self.retired[0])]})
        self.assertIn("Enroll MOK", stdout)
        self.assertIn("Delete MOK", stdout)
        # Run again (another upgrade before the reboot): nothing doubles.
        _stdout, again = self.run_swap()
        self.assertEqual(again, lists)

    def test_a_machine_with_the_new_key_only_is_left_alone(self):
        self.enroll(self.cert)
        self.assertEqual(self.run_swap(), ("", {"new": [], "delete": []}))

    def test_a_machine_with_both_only_loses_the_old(self):
        self.enroll(self.cert, self.retired[0])
        _stdout, lists = self.run_swap()
        self.assertEqual(lists, {"new": [], "delete": [fingerprint(self.retired[0])]})

    def test_the_package_ships_the_retired_keys(self):
        with open(os.path.join(self.DIR, "PKGBUILD"), encoding="utf-8") as f:
            self.assertIn('"$startdir"/retired/*.crt', f.read())

    def test_the_upgrade_runs_the_swap(self):
        with open(os.path.join(self.DIR, "big-secureboot"), encoding="utf-8") as f:
            refresh = re.search(r"^cmd_refresh\(\) \{\n(.*?)^\}", f.read(), re.M | re.S).group(1)
        self.assertIn("swap_keys", refresh)


def der(pem, out):
    subprocess.run(["openssl", "x509", "-in", pem, "-outform", "DER", "-out", out], check=True)


def fingerprint(cert):
    out = subprocess.run(["openssl", "x509", "-inform", "DER", "-in", cert, "-noout", "-fingerprint", "-sha1"],
                         capture_output=True, text=True, check=True).stdout
    return out.strip().split("=", 1)[1]


if __name__ == "__main__":
    unittest.main()
