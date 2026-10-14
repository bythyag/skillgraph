---
name: release-notes
description: Draft release notes for the next version from git history. Use when asked to prepare release notes or a changelog entry for a release.
---

# Release notes

1. Find the latest release tag with `git describe --tags --abbrev=0`.
2. List the commits since that tag with `git log <tag>..HEAD --format='%h %s%n%b'`.
3. Group commits by type: `feat:` under "Features", `fix:` under "Bug fixes", everything else under "Other". Leave out commits that start with `chore:`.
4. Pick the next version: bump the minor version if there is any `feat:` commit, otherwise bump the patch version.
5. If any commit body contains `BREAKING CHANGE`, bump the major version instead and put a "Breaking changes" section first.
6. Write `RELEASE_NOTES.md` with the heading `## v<version>` followed by the sections.

Never create git tags and never push. The user publishes the release.
