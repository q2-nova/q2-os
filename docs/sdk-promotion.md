# Buildroot SDK promotion and consumption

The manual OS host checks cover the external tree, artifact inputs and platform
bridge. The SDK release workflow runs the host checks first.

1. Dispatch **SDK release** with `operation=validate` for a source-only
   check. Pushing an `sdk-vX.Y.Z` tag runs the same checks and validates the tag.
2. Select the trusted q2-os ref in a separate dispatch with
   `operation=qualify`. The workflow builds the pinned Buildroot fork and runs
   relocation, time64, ABI and platform bridge checks. It stores the archive,
   resolved configuration, qualification report, exact source provenance and
   checksums as one Actions artifact. Record the run ID.
3. Before promotion, review the archive contents and Buildroot legal-info for
   notices, corresponding source and redistribution terms. Cross-build and
   exercise the downstream artifacts against that exact SDK. Create the
   version tag at its qualified, signed q2-os commit.
4. Dispatch `operation=promote` **on that tag**, with its name, the qualifying
   run ID and redistribution review confirmation. Promotion verifies that the
   successful manual qualification ran in this repository at the signed commit.
   It checks the downloaded files and independently re-runs relocation and
   ABI validation with read-only repository permissions. A separate job
   verifies those exact bytes and creates a release draft with checksums.
   Review the draft before publishing.
5. The player and standalone cross-build consumers lock the promoted SDK's
   q2-os and Buildroot commits and archive SHA-256. Verify the digest before
   extraction, relocate into an empty directory and record the archive hash
   in the consumer build receipt. Contributors can build and test the same
   pinned SDK locally.
