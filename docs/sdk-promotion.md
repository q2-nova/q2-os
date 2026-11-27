# Buildroot SDK promotion and consumption

Pull requests use host controls for the external tree, artifact inputs and
platform bridge. Building a candidate SDK is a separate maintainer action.

1. From a clean signed q2-os revision, build the pinned Buildroot fork with
   `python3 os/build.py sdk`. Record the q2-os commit, Buildroot commit,
   container image digest, defconfig and resolved configuration digest.
2. Run `python3 os/test_sdk.py` against that exact archive, then cross-build and
   run representative C and Rust consumers with the published toolchain and
   QEMU. Retain the archive SHA-256, relocation and ABI results and dependency
   identities. Review the archive and Buildroot legal-info output for notices,
   corresponding source and redistribution terms before promotion.
3. Promote the exact tested archive and its manifest as a versioned asset
   with its SHA-256 after maintainer approval. The asset belongs with q2-os.
   A maintainer reviews the contents before uploading an asset to its intended
   repository.
4. The player and standalone cross-build consumers lock the promoted SDK's
   q2-os and Buildroot commits and archive SHA-256. Verify the digest before
   extraction, relocate into an empty directory and record the archive hash
   in the consumer build receipt. Contributors can build and test the same
   pinned SDK locally.

The source checks do not schedule SDK builds, upload artifacts or deploy any
image.
