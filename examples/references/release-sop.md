# Release notes process

Our team's process for drafting release notes before a release. Release managers
review the notes and publish the release themselves.

```mermaid
flowchart TD
    start([Release requested]) --> tag[Find the latest release tag]
    tag --> commits[List the commits since that tag]
    commits --> any{Are there any commits since the tag?}
    any -->|no| nothing([Report that there is nothing to release])
    any -->|yes| breaking{Does any commit contain BREAKING CHANGE?}
    breaking -->|yes| major[Bump the major version]
    breaking -->|no| feat{Is there at least one feat: commit?}
    feat -->|yes| minor[Bump the minor version]
    feat -->|no| patch[Bump the patch version]
    major --> migration[Put a Breaking changes section first, with migration notes for users]
    migration --> group
    minor --> group
    patch --> group
    group[Group commits into Features, Bug fixes, and Other, leaving out chore: commits] --> credit[Credit each commit's author]
    credit --> write[Write RELEASE_NOTES.md headed with the new version]
    write --> done([Release notes are ready for review])
```

## Rules

- Never create git tags or push; release managers publish the release.
- Link every entry to its commit hash.
