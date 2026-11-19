# Secure Boot signing for linux-big, sourced by the kernel and module PKGBUILDs.
#
# The kernel image and the out-of-tree modules are signed with the
# BigLinux Secure Boot key. Users enroll its certificate
# (biglinux-secureboot-2026.crt, here) as a MOK; shim then boots the
# kernel, and the kernel takes the MOK into its .machine keyring and loads
# these modules under lockdown. The modules built with the kernel need
# nothing: they are signed with the kernel's own build-time key.
#
# The private key is never in this repository. The CI writes it to a file
# for the kernel builds only and passes its path in BIGCOMMUNITY_SB_KEY (a
# name biglinux/build-package sets; it kept the name of the first key). With
# no key (any local build) nothing is signed and the packages are exactly
# what they were before: the signature is only an extra block, ignored when
# Secure Boot is off. With a key, a signature that fails stops the build, so
# a half-signed kernel is never published.

_sb_cert="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/biglinux-secureboot-2026.crt"
_sb_signer="BigLinux Secure Boot 2026"

_sb_enabled() {
  [[ -n ${BIGCOMMUNITY_SB_KEY:-} ]] || return 1
  if [[ ! -r $BIGCOMMUNITY_SB_KEY ]]; then
    error "BIGCOMMUNITY_SB_KEY is set but $BIGCOMMUNITY_SB_KEY cannot be read"
    exit 1
  fi
  # The key must be the one whose certificate users enroll.
  if [[ $(openssl pkey -in "$BIGCOMMUNITY_SB_KEY" -pubout) != "$(openssl x509 -in "$_sb_cert" -noout -pubkey)" ]]; then
    error "BIGCOMMUNITY_SB_KEY does not match $_sb_cert"
    exit 1
  fi
}

# Signs a kernel image (an EFI binary) in place.
_sb_sign_kernel() {
  local image=$1
  if ! _sb_enabled; then
    warning "No Secure Boot key: $image is not signed"
    return 0
  fi
  sbsign --key "$BIGCOMMUNITY_SB_KEY" --cert "$_sb_cert" --output "$image.signed" "$image" ||
    { error "sbsign failed for $image"; exit 1; }
  mv "$image.signed" "$image"
  sbverify --cert "$_sb_cert" "$image" >/dev/null ||
    { error "$image does not verify against $_sb_cert"; exit 1; }
  msg2 "Signed $image for Secure Boot"
}

# Drops the signatures already appended to a module: DKMS signs what it
# builds with a throwaway key of its own, generated on the build machine.
_sb_strip_module_signature() {
  local module=$1 size siglen
  local marker='~Module signature appended~'
  while size=$(stat -c %s "$module") && (( size > 40 )) &&
        tail -c 28 "$module" | cmp -s - <(printf '%s\n' "$marker"); do
    # struct module_signature: 8 bytes, then the signature length as a
    # big-endian 32-bit number, then the signature, then the marker.
    siglen=$(od -An -tu1 -j $((size - 32)) -N4 "$module" |
             awk '{ print (($1 * 256 + $2) * 256 + $3) * 256 + $4 }')
    truncate -s $((size - 28 - 12 - siglen)) "$module"
  done
}

# Signs every module under a directory, before the PKGBUILD compresses them:
# a compressed module is decompressed first (the PKGBUILD compresses it again
# right after). $2 is the kernel release the modules are for.
_sb_sign_modules() {
  local dir=$1 kernver=$2 module plain sign_file hash count=0
  if ! _sb_enabled; then
    warning "No Secure Boot key: the modules are not signed"
    return 0
  fi
  sign_file=/usr/lib/modules/$kernver/build/scripts/sign-file
  hash=$(sed -n 's/^CONFIG_MODULE_SIG_HASH="\(.*\)"/\1/p' "/usr/lib/modules/$kernver/build/.config")
  [[ -x $sign_file ]] || { error "$sign_file not found: is the headers package installed?"; exit 1; }
  while IFS= read -r -d '' module; do
    plain=${module%.*}
    case $module in
      *.ko) plain=$module ;;
      *.ko.zst) zstd -dq --rm "$module" -o "$plain" ;;
      *.ko.xz) xz -d "$module" ;;
      *.ko.gz) gzip -d "$module" ;;
    esac || { error "cannot decompress $module"; exit 1; }
    _sb_strip_module_signature "$plain"
    "$sign_file" "${hash:-sha512}" "$BIGCOMMUNITY_SB_KEY" "$_sb_cert" "$plain" ||
      { error "sign-file failed for $plain"; exit 1; }
    [[ $(modinfo -F signer "$plain") == "$_sb_signer" ]] ||
      { error "$plain is not signed by $_sb_signer"; exit 1; }
    count=$((count + 1))
  done < <(find "$dir" -type f \( -name '*.ko' -o -name '*.ko.zst' -o -name '*.ko.xz' -o -name '*.ko.gz' \) -print0)
  (( count )) || { error "no module found under $dir to sign"; exit 1; }
  msg2 "Signed $count module(s) for Secure Boot"
}
