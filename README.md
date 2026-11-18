<p align="center">
  <img src=".github/assets/banner.png" alt="linux-big — the BigLinux kernel" width="100%">
</p>

# linux-big — the BigLinux kernel

**linux-big** is the kernel of [BigLinux](https://www.biglinux.com.br). It is
built from the official [kernel.org](https://www.kernel.org) sources, keeps the
configuration and hardware patches of the Manjaro kernel BigLinux already
ships, and adds what can only be gained at compile time: a desktop-oriented CPU
scheduler, a faster idle CPU selector, an adaptive I/O scheduler, a Clang/LLVM
ThinLTO build ready for profile-guided optimisation, and BigLinux's own
fixes for Intel graphics.

The goal is simple: a kernel that feels faster under load, whether you are
gaming or not, on the hardware our users actually have.

> **Status:** 7.2 is the recommended series (`linux-big` brings it), 7.3 is in
> testing. Each kernel series is a package of its own (`linux-big72`,
> `linux-big73`) and they install side by side.
> See [Kernel series](#kernel-series) and the [roadmap](#roadmap).

---

## Contents

- [What is inside](#what-is-inside)
- [How it differs from the Manjaro kernel](#how-it-differs-from-the-manjaro-kernel)
- [Patches](#patches)
- [Installing](#installing)
- [Kernel series](#kernel-series)
- [Checking that it works](#checking-that-it-works)
- [Kernel updates](#kernel-updates)
- [NVIDIA and other kernel modules](#nvidia-and-other-kernel-modules)
- [External modules (DKMS)](#external-modules-dkms)
- [Secure Boot and anti-cheat](#secure-boot-and-anti-cheat)
- [System tuning stays in BigLinux](#system-tuning-stays-in-biglinux)
- [Building it yourself](#building-it-yourself)
- [Roadmap](#roadmap)
- [Credits and licenses](#credits-and-licenses)

---

## What is inside

| | Where it comes from |
|---|---|
| **Kernel source** | [kernel.org](https://www.kernel.org), stable series, verified by checksum |
| **Base configuration** | the Manjaro kernel configuration used by BigLinux |
| **Hardware patches** | Manjaro's set: AMD GPU and display fixes, Realtek audio, Intel Wi-Fi, handhelds |
| **BORE CPU scheduler** | [firelzrd/bore-scheduler](https://github.com/firelzrd/bore-scheduler), by Masahito Suzuki |
| **POC idle CPU selector** | [firelzrd/poc-selector](https://github.com/firelzrd/poc-selector), by Masahito Suzuki |
| **ADIOS I/O scheduler** | [firelzrd/adios](https://github.com/firelzrd/adios), by Masahito Suzuki |
| **Intel Xe / i915 fixes** | BigLinux, sent upstream and reviewed on the kernel mailing lists |
| **Compiler** | Clang/LLVM with ThinLTO and AutoFDO support |

### BORE — responsiveness under load

The **B**urst-**O**riented **R**esponse **E**nhancer builds on the kernel's
EEVDF scheduler. It tracks how long each task runs in bursts and favours
interactive work — the game, the desktop, audio — over tasks that hold the CPU
for long stretches, such as a compiler or a background download. The effect is
most visible exactly when the machine is busy: fewer stutters, a desktop that
keeps responding.

### POC — finding a free CPU faster

Every time a task wakes up — a game thread, an audio buffer, an input event —
the scheduler has to find an idle CPU to run it on, and the kernel does that by
scanning. The **P**iece-**O**f-**C**ake selector keeps a bitmap of idle CPUs and
answers in constant time instead. It does not change which task runs or how
fairly; it only shortens the path to getting it running, which matters most on
machines with many cores and workloads with many threads.

### ADIOS — adaptive I/O latency

The **A**daptive **D**eadline **I/O** **S**cheduler learns the latency of each
disk and adjusts its scheduling to it. It matters when the disk is contended — a
game loading while Steam downloads an update or the system indexes files — which
is when loading hitches appear. It is the kernel's default I/O scheduler in
linux-big (see [System tuning stays in BigLinux](#system-tuning-stays-in-biglinux)).

### Clang ThinLTO — a faster binary

The whole kernel is compiled with Clang and linked with **ThinLTO** (link-time
optimisation): the compiler sees across source files, inlines and removes code
it could not otherwise touch. The build targets generic **x86-64**, so the same
package runs on every 64-bit PC, not only on the machine that built it.

It is also built with **AutoFDO** support (`CONFIG_AUTOFDO_CLANG`). AutoFDO
recompiles the kernel from a profile of what it actually executes — collected
with `perf` on real gaming and desktop workloads — so the hot paths are laid out
and optimised for how the kernel is really used. The first profile is being
collected; until it ships, the kernel runs as a regular ThinLTO build.

The optimisation level stays at the kernel's default `-O2`. `-O3` was measured
by Phoronix across 230 tests at a 1.3% overall gain, with no measurable benefit
for gaming or desktop workloads, and it is the level upstream has declined to
support because of the miscompilations it has caused
([Phoronix](https://www.phoronix.com/review/linux-kernel-o3/9),
[Linux 6.0](https://www.phoronix.com/news/Linux-6.0-Drops-O3-Kconfig)).
Profile-guided optimisation is where the measurable gain is.

### Intel Xe fixes — for Arrow Lake and Meteor Lake

BigLinux found and fixed a bug on Intel Arrow Lake / Meteor Lake graphics:
on the media GT, the CPU could read stale data the graphics firmware had
already written, which showed up as **TLB invalidation stalls of ~2.3 s** and,
on some machines, as freezes under video workloads. The fix implements
hardware workaround `Wa_22016122933`, which the older i915 driver had and the
Xe driver never inherited.

The series was sent upstream and reviewed by the Xe maintainers
([issue 8678](https://gitlab.freedesktop.org/drm/xe/kernel/-/work_items/8678)).
linux-big carries it until it reaches a kernel release, together with a
follow-up by Navon John Lukose that removes the boot and resume slowdown the
first version of the workaround caused.

---

## How it differs from the Manjaro kernel

The Manjaro configuration already has most of what a desktop kernel needs:
1000 Hz timer, full dynamic preemption, `NO_HZ_FULL`, RCU boosting, transparent
huge pages, MGLRU, `sched_ext` and `ntsync`. linux-big keeps all of it and
changes only this:

| Option | Manjaro | linux-big |
|---|---|---|
| Compiler | GCC | **Clang/LLVM** |
| Link-time optimisation | none | **ThinLTO** (`CONFIG_LTO_CLANG_THIN`) |
| CPU scheduler | EEVDF | **EEVDF + BORE** (`CONFIG_SCHED_BORE`) |
| Idle CPU selection | linear scan | **POC** (`CONFIG_SCHED_POC_SELECTOR`) |
| Profile-guided optimisation | no | **ready** (`CONFIG_AUTOFDO_CLANG`) |
| Default I/O scheduler | — | **ADIOS** (`CONFIG_MQ_IOSCHED_DEFAULT_ADIOS`) |
| Intel Xe TLB workaround | no | **yes** (`Wa_22016122933`) |
| Kernel name | `-MANJARO` | `-big` |

These options are checked at build time: if a future kernel release silently
drops one of them, the build fails instead of shipping without it.

---

## Patches

Each series has its own patches, in its directory —
[`linux-big73/`](linux-big73), [`linux-big72/`](linux-big72) — applied in the
order its PKGBUILD lists them. The tables describe the current series, 7.3.

**BigLinux — Intel graphics**

| Patch | What it does |
|---|---|
| `0101` `drm/xe: Capture devcoredump on TLB invalidation timeout` | records the firmware state when a TLB invalidation times out |
| `0102` `drm/xe: Log when a timed out TLB invalidation ack finally arrives` | tells a lost acknowledgement from a late one |
| `0103` `drm/xe: Implement Wa_22016122933` | **the fix**: GuC-shared memory on the media GT is mapped uncached |
| `0104` `drm/xe: Map Wa_22016122933 buffers WC on the CPU side` | keeps the fix but maps that memory write-combined on the CPU, as i915 does: boot and resume are ~3 s and ~2.5 s faster (by Navon John Lukose) |
| `drm/xe/mcr: Keep GT forcewake during MCR steering` | ports i915's MCR locking; removes a warning on resume |
| `drm/xe/mcr: Sanitize steering semaphore on GT resume` | releases a semaphore left held across suspend |
| `drm/i915/vrr: Check TRANS_PUSH_SEND after DSB commit` | removes the `DSB poll error` at boot on Arrow Lake |
| `drm/xe: Scope LNL_FLUSH_* workaround to affected CPUs` | applies a CPU scheduling workaround only where it is needed (by Matthew Brost) |

**Schedulers**

| Patch | Version |
|---|---|
| `0200-sched-bore` | BORE 7.0.1 (6.8.0 on 7.2) |
| `0201-block-adios` | ADIOS 3.3.0r2 |
| `0202-sched-poc-selector` | POC 3.0.0 (2.6.3 on 7.2) |

**From the Manjaro kernel** — AMD GPU race fix and brightness curve check, USB
VHCI suspend, Realtek audio, Intel Wi-Fi, unprivileged user namespaces sysctl,
`simpledrm` with NVIDIA, and handheld support: ASUS ROG Ally and Ally X, Zotac
Zone, Steam Deck and OrangePi NEO.

---

## Installing

linux-big is published in the BigLinux repositories and installs next to
your current kernel — it does not replace it.

```bash
sudo pacman -S linux-big linux-big-headers
```

`linux-big` brings the series BigLinux recommends — 7.2 today — and keeps
following it: when the next one is approved, the update installs it next to
the one you have. A series still in testing is installed by name, and goes
next to the one you have:

```bash
sudo pacman -S linux-big73 linux-big73-headers
```

Each series boots from its own image, `/boot/vmlinuz-linux-big73`,
`/boot/vmlinuz-linux-big72`, with its own GRUB entry. Keep another kernel
installed as a fallback.

---

## Kernel series

Each kernel series is a package of its own, as in Manjaro (`linux72`,
`linux73`):

| Package | What it is |
|---|---|
| `linux-big73`, `linux-big73-headers` | the 7.3 series |
| `linux-big72`, `linux-big72-headers` | the 7.2 series, kept for the machines that boot it |
| `linux-big73-nvidia`, `linux-big72-zfs`, … | each series' modules |
| `linux-big`, `linux-big-headers`, `linux-big-<module>` | metapackages: they install nothing, and depend on the same package of every series in `_series` — today `linux-big72` only |

Every series has its own boot image, initramfs, `/usr/src/linux-bigNN` and
modules, so a new series never takes away the one that was working.

The metapackages are what the ISO and users ask for, and how a machine moves
on, in two steps:

1. **Now.** Before the series were split, `linux-big` was the 7.2 kernel
   itself. A machine that has it — every install of the 7.2 ISO — gets on its
   next update `linux-big72`, the same 7.2.15 under its new name, and for
   each module it had, the `linux-big72-` one. 7.3 is published to testing,
   but no metapackage brings it: on testing, whoever wants it installs
   `linux-big73` by name and keeps both.
2. **When 7.3 is approved for everyone.** It is put first in `_series`, next
   to 7.2, and the metapackage goes to testing and then to stable as any
   update. Machines get `linux-big73` **and** keep `linux-big72`; both show up
   in GRUB, each with its own modules. Once 7.3 has proven itself, 7.2 is
   dropped from `_series`, and machines keep it until they remove it.

`linux-big72` is 7.2.15-**2**, the same 7.2.15 as the old package: with the
old release, `/usr/lib/modules/7.2.15-1-big` would only change owner, and
mkinitcpio would leave `/boot/vmlinuz-linux-big` behind as a third GRUB
entry.

The metapackages are published only after everything they depend on is in
the branch — before that, `pacman -Syu` could not update. The module watcher
does that by itself for testing.

---

## Checking that it works

```bash
uname -r                                  # 7.3.x-N-big
sudo dmesg | grep -i "BORE CPU Scheduler" # BORE is active
sysctl kernel.sched_bore                  # 1 = enabled
sysctl kernel.sched_poc_selector          # 1 = enabled
cat /sys/block/*/queue/scheduler          # adios is listed, the active one in [brackets]
zgrep LTO_CLANG_THIN /proc/config.gz      # CONFIG_LTO_CLANG_THIN=y
```

BORE and POC can be switched off at runtime, for comparison, with
`sudo sysctl kernel.sched_bore=0` and `sudo sysctl kernel.sched_poc_selector=0`.

---

## Kernel updates

Each series follows its kernel.org series by itself. Every six hours the
[kernel watcher](.github/workflows/watch-kernel.yml) checks kernel.org for
every `linux-bigNN` directory, and when a new release of a series is out
(7.3.1, 7.3.2, …) it:

1. moves the PKGBUILD to it — new `pkgver`, `pkgrel` back to 1, the new stable
   patch and its checksum;
2. drops the patches that release already contains. They are applied in order
   on the new tree, as the build does, and one that no longer applies but
   applies in reverse is already in the kernel;
3. runs the package's own `prepare()`, in the same container the packages are
   built in: every patch must apply and every option linux-big exists for
   (BORE, ADIOS, POC, Clang ThinLTO, AutoFDO, …) must survive the configuration;
4. commits the update. The module watcher, which compares the PKGBUILD with
   what is published every 30 minutes, then sends the kernel to be built for
   **testing**, and the modules once it is published. Building is decided from
   what is published, so a dispatch that fails is simply retried. Every build
   the watcher sends is followed until it is published or fails, and then the
   watcher runs again, without waiting for GitHub's schedule (which can be
   hours late): the modules follow the kernel, a failed build is sent again
   half an hour later (three attempts in all), and the `linux-big`
   metapackage follows its modules.

When a patch no longer fits or an option is lost, nothing is committed or
built: the watcher opens an issue naming the patch or the option, once. It
does the same when kernel.org marks the current series end of life; an older
series at its end of life is the one kept for the machines that boot it, and
is left as it is.

Three decisions stay with a person: moving a kernel from testing to stable,
moving to the next series, and refreshing a patch that stopped applying.

### Moving to the next series

1. Copy the current series' directory and modules to the next number
   (`linux-big74`, `linux-big74-nvidia`, …). In each module PKGBUILD only the
   `_kernel_pkg=` line changes; a test checks that the copies stay the same
   PKGBUILD.
2. In the kernel: `pkgbase`, `_basekernel`, `pkgver`, the config and the
   patches.
3. When it is approved for everyone, in [`linux-big/PKGBUILD`](linux-big/PKGBUILD):
   put it first in `_series` and set `pkgver` to it. Until then it is only in
   testing, installed by name.
4. Once the oldest series is no longer needed, take it out of `_series` and
   delete its directories.

The watchers pick the new directories up by themselves.

### Moving a kernel to stable

Move the kernel, its headers and all its modules together (Repo-Management's
"move" moves the files as they are, nothing is rebuilt), and the `linux-big*`
metapackages with them or after them — never before: they depend on every
series being there. After that:

- testing is seen as its users see it, with stable underneath: the watcher
  does not take the kernel's absence from testing for a missing build;
- the next kernel still goes to testing by itself, over stable's;
- stable's modules keep following Manjaro stable's drivers. When stable's
  kernel is older than the PKGBUILD on main, the watcher finds the commit
  that described it, points that series' `linux-bigNN-stable` branch there
  and builds the modules from it.

## NVIDIA and other kernel modules

Like the Manjaro kernels, linux-big has prebuilt modules, named
`<kernel>-<module>` (`linux-big73-nvidia-open` and so on). On BigLinux they
are chosen and installed by
[big-driver-manager-cli](https://github.com/biglinux/big-driver-manager-cli),
which replaced mhwd: on the live system (`big-driver-manager-live.service`), in the
Calamares installer and in the Big Driver Manager interface. For each kernel
installed it looks for the module package named after that kernel's pkgbase,
and picks the NVIDIA branch from NVIDIA's own `supported-gpus.json`. When a
kernel has no module package for a driver, it installs the `-dkms` driver with
the kernel headers instead, and when there is none of those either, the GPU
stays on nouveau.

Two things in this repository are a contract with big-driver-manager-cli and
must not change: the `pkgbase` file each kernel PKGBUILD writes to
`/usr/lib/modules/<version>/pkgbase` (it reads `linux-big73` there), and the
`<kernel>-<module>` names of the module packages.

Every series has the same set; the table names the modules of 7.3.

| Package | For |
|---|---|
| `linux-big73-nvidia-open` | NVIDIA, Turing (GTX 16xx, RTX 20) and newer, open modules |
| `linux-big73-nvidia` | NVIDIA, Turing and newer, proprietary modules |
| `linux-big73-nvidia-580xx-open`, `linux-big73-nvidia-580xx` | NVIDIA 580xx: Maxwell, Pascal and Volta (GTX 750, 900, 10xx) |
| `linux-big73-nvidia-390xx` | NVIDIA 390xx: Fermi (GTX 400, 500) |
| `linux-big73-virtualbox-host-modules` | running VirtualBox virtual machines |
| `linux-big73-zfs` | the ZFS file system |
| `linux-big73-vhba-module` | virtual CD/DVD drives (CDEmu) |
| `linux-big73-broadcom-wl` | Broadcom BCM43xx Wi-Fi |
| `linux-big73-r8168` | Realtek RTL8168 Ethernet |
| `linux-big73-rtl8723bu` | Realtek RTL8723BU USB Wi-Fi |
| `linux-big73-acpi_call` | ACPI calls for power and battery tools (TLP) |
| `linux-big73-tp_smapi` | battery and sensors on older ThinkPads |

This is the set Manjaro builds for each of its kernels, less two:

- **`bbswitch`**, which switched off the discrete GPU of Optimus laptops in the
  Bumblebee days. The NVIDIA driver does that now, with RTD3 and PRIME.
- **`nvidia-470xx`**, for Kepler (GTX 600 and 700, GT 710 and 730): X11 only,
  and out of NVIDIA's support since 2024. Those cards stay on nouveau, or take
  `nvidia-470xx-dkms` from the Big Driver Manager interface.

`linux-big-bbswitch` and `linux-big-nvidia-470xx` were published before they
were dropped. Each depends on one exact linux-big, so on a machine that has
either one installed it holds every later linux-big back: remove it (on a
Kepler card, replace `linux-big-nvidia-470xx` with `nvidia-470xx-dkms`) to keep
getting linux-big updates.

They are built with dkms from the driver packages in Manjaro's repositories —
no driver code lives here — and each one depends on one exact kernel and
one exact driver version. That is what keeps an update from breaking graphics:
if a new kernel or a new NVIDIA driver is published before its module, pacman
holds that update back instead of booting without a driver.

Nobody rebuilds them by hand. The [watcher](.github/workflows/watch-manjaro.yml)
compares, every 30 minutes, for every series and for both the testing and
stable branches, what the modules should be — the driver users get, on that
series' kernel — with
what BigLinux has published, and dispatches a build of every module that is
behind. A build that does not get published, usually because of the network, is
dispatched again once it is no longer running and at least half an hour has
passed since the last dispatch, up to three attempts in all; after the third the
watcher run fails once, so GitHub mails it. The driver version is resolved the
way pacman does it: from the first repository in the users' order that has it —
BigLinux priority, then Manjaro, then BigLinux — so a driver BigLinux publishes ahead
of Manjaro is followed too. Both branches follow the drivers of Manjaro
**stable**, the branch every package is built against.

Which NVIDIA driver a machine has is decided by its Manjaro branch, not by
BigLinux's. So while Manjaro testing has a newer NVIDIA driver than stable,
the `nvidia-open`, `nvidia`, `nvidia-580xx-open` and `nvidia-580xx` modules
carry a build for each — the version names both, stable's first, as in
`610.57.04+615.71.09` — and a pacman hook points the system at the build of
the installed `nvidia-utils` (or `nvidia-580xx-utils`), through
`/etc/depmod.d/linux-bigNN-nvidia*.conf`, one per series, each for its own
kernel only. Moving between Manjaro branches
switches the build by itself; nothing is compiled on the user's machine. Once
both branches have the same driver, the package carries one build again.
VirtualBox needs no such thing: its modules keep working across the 7.2
releases.

The kernel build itself checks the other direction: in the CI, `check()` builds
the NVIDIA open driver against the new kernel, and a kernel it does not build
against is not published.

## External modules (DKMS)

A kernel built with Clang and LTO needs its external modules built with the same
toolchain. `linux-big-headers` depends on `clang`, `llvm` and `lld` and sets
`LLVM=1` in its build Makefile, so DKMS modules are compiled with Clang
automatically — the NVIDIA DKMS packages were tested this way too. No action is
needed; an explicit `LLVM=` on the command line still overrides it.

## Secure Boot and anti-cheat

The kernel image and every module package are signed with the BigLinux
Secure Boot key (`secureboot/sign.sh`; the certificate, which keeps its name
"BigCommunity Secure Boot 2026", is `secureboot/bigcommunity-secureboot-2026.crt`).
The CI provides the key to this repository's builds only
(biglinux/build-package, `scripts/secureboot-key.sh`). A build without the key, a local one for
example, signs nothing. With Secure Boot off the signature is ignored and
nothing changes. `.github/scripts/test_secureboot.py` fails if a series or a
module package stops signing.

Secure Boot itself is optional, through the `big-secureboot` package, which
nothing depends on. Installing it enables it: biglinux-driver-manager
installs it, or by hand:

```bash
sudo pacman -S big-secureboot # then reboot and confirm the key on the blue screen (password: big)
sudo big-secureboot enable    # if it was installed where it could not enable (a chroot, an ISO build)
big-secureboot status         # what is in place, and the Easy Anti-Cheat checks
sudo big-secureboot disable   # take it out of the boot (turn Secure Boot off first)
```

The package brings:

- Fedora's shim, signed by Microsoft with both the 2011 and the 2023 CA.
- A GRUB signed with the BigLinux key, with every module built in.
- The certificate, which the user enrolls as a MOK.
- What Easy Anti-Cheat Linux v2 checks besides Secure Boot: kexec off
  (`/usr/lib/sysctl.d/90-big-secureboot.conf`) and the Intel IOMMU on
  (`intel_iommu=on`, through `/etc/default/grub.d/90-big-secureboot.cfg`).

It installs in its own ESP directory, `\EFI\big-secureboot`, with its own
firmware boot entry, which the grub package's hooks never touch. Its GRUB
reads the usual `/boot/grub/grub.cfg`, so the menu and its theme stay as they
are. The key goes into shim's own list (MokList). The firmware keys (PK,
KEK, db, dbx) are not touched, Microsoft's included, which the firmware of
GPUs and other devices needs.

Booted with Secure Boot, linux-big locks itself down to integrity mode
(`0300-efi-lock-down-the-kernel-in-Secure-Boot-mode.patch`,
`CONFIG_LOCK_DOWN_IN_EFI_SECURE_BOOT`), as Fedora, Ubuntu and Debian do.
Only signed modules load then: the packaged ones (`linux-bigNN-nvidia*`, …)
are signed, while a module built locally by DKMS is not. Hibernation is not
available either. With Secure Boot off nothing changes. Under Secure Boot,
only linux-big boots, since it is the kernel signed with the BigLinux key.

---

## System tuning stays in BigLinux

BigLinux already tunes the running system — sysctl values for gaming and
memory, I/O scheduler rules per disk type (`auto-rules-io`). linux-big does not
override any of that: it works at the level only a kernel build can reach.

That includes the I/O scheduler. ADIOS is the kernel's default, but BigLinux's
udev rules pick a scheduler per disk at boot, and they win. To try ADIOS on a
disk yourself:

```bash
echo adios | sudo tee /sys/block/nvme0n1/queue/scheduler
```

---

## Building it yourself

### Requirements

- An Arch-based system (BigCommunity, BigLinux, Manjaro, Arch).
- About **30 GB** of free disk space and **16 GB of RAM** or more; the ThinLTO
  link is the memory-hungry step.
- A few hours of CPU: about 50 minutes on a 14-core desktop, much longer on a
  laptop.

### Build and install the kernel

```bash
git clone https://github.com/biglinux/big-kernel
cd big-kernel/linux-big73
makepkg -s
```

`makepkg -s` installs the build dependencies (Clang, LLVM, Rust, ...), downloads
the kernel from kernel.org, applies the patches and builds. On a machine with
little memory, or to keep it usable while it builds, limit the parallel jobs:

```bash
MAKEFLAGS=-j8 nice makepkg -s
```

Then install both packages; they go next to your current kernel:

```bash
sudo pacman -U linux-big73-*.pkg.tar.zst
```

Boot **linux-big73** from the GRUB menu, and remove another kernel only once it
has booted fine.

### Build a module

With the kernel's headers of the same version installed:

```bash
cd big-kernel/linux-big73-nvidia-open   # or any linux-big73-* directory
makepkg -s
sudo pacman -U linux-big73-nvidia-open-*.pkg.tar.zst
```

The module reads the kernel version from `../linux-big73/PKGBUILD` and the
driver version from your repositories, so it builds for the kernel and driver
you have.

### Notes

- A local build does not run the NVIDIA check the CI runs; it would install
  the NVIDIA driver on your machine.
- To tweak the configuration, edit `linux-big73/config`. The options listed in
  `_required_options` in the PKGBUILD must stay: the build refuses to go on
  without them.

### How the official packages are built

Official packages are built by the BigLinux CI
([build-package](https://github.com/biglinux/build-package)). The repository
holds one directory per package, and the CI builds the one it is asked for.

```
big-kernel/
├── linux-big/                  # the metapackages: linux-big, linux-big-headers, linux-big-<module>
├── linux-big73/                # the 7.3 kernel: PKGBUILD, config, patches
├── linux-big73-nvidia*/        # its NVIDIA modules
├── linux-big73-broadcom-wl/    # its Broadcom Wi-Fi module, and so on
├── linux-big72/, linux-big72-*/  # the 7.2 kernel and its modules
└── .github/
    ├── workflows/              # the kernel and module watchers
    ├── scripts/                # the watchers and their tests
    └── assets/                 # images for this page
```

---

## Roadmap

- [x] linux-big 7.2 — BORE, ADIOS, POC, Clang ThinLTO, Intel Xe fixes
- [x] One package per kernel series, side by side; 7.3 with BORE 7.0.1 and POC 3.0.0
- [x] Built ready for AutoFDO
- [ ] Validation on Intel (Arrow Lake) and AMD (Zen 2) machines, and with NVIDIA DKMS
- [ ] **linux-big-lts** — the 6.18 long-term series, as a conservative fallback
- [x] The 15 kernel modules Manjaro builds for its kernels, kept in step automatically
- [x] New releases of the kernel series picked up, checked and built for testing automatically
- [ ] Selectable in the BigLinux ISO builder
- [ ] **AutoFDO profile** from real gaming and desktop workloads, shipped in the package
- [ ] **Propeller** on top of AutoFDO
- [ ] Published benchmarks: scheduler latency, frame times and build times, side by side with other kernels on the same hardware

Claims about speed will be made here with numbers, once the benchmarks exist.

---

## Credits and licenses

- The Linux kernel and its patches are licensed under the
  [GPL-2.0](https://www.kernel.org/doc/html/latest/process/license-rules.html).
- BORE, ADIOS and the POC selector are the work of Masahito Suzuki, GPL-2.0.
- The hardware patches come from the Manjaro kernel team and their upstream
  authors, named in each patch.
- The Intel Xe fixes are by Tales A. Mendonça, with review by Matthew Brost and
  testing by Navon John Lukose; the workaround approach was suggested by
  Daniele Ceraolo Spurio.
- The packaging in this repository is under the [MIT license](LICENSE).

Maintained by [BigLinux](https://www.biglinux.com.br).
