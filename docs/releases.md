# Release downloads

Tag builds attach these assets to a draft GitHub release alongside the npm tarball:

| Platform | Release asset | Contents |
| --- | --- | --- |
| Windows | `Gloss-windows-x64.exe` and `Gloss.exe` | Standalone desktop application |
| Linux / macOS | `gloss-local-<version>.tgz` | Run the local browser app through npm or Bun |

The Windows executables are portable: download the matching `.exe` and open it
directly—no archive extraction, terminal, or browser tab is needed. `Gloss.exe`
is kept as an x64 compatibility download. The portable desktop app bundles its Chromium-based Electron shell.
The Windows build targets x64; Windows on ARM runs it through x64 emulation.

## Version channels

On the Gloss repository, Git tags that contain a prerelease suffix, such as `v2.0.0-beta.1`, create draft GitHub prereleases. Review the assets and release notes, then publish the draft in GitHub. For npm publication, dispatch the release workflow for the exact version tag and enable `publish_npm`. The workflow publishes prereleases to `next` and stable builds to `latest`.

## Linux and macOS

Linux and macOS share the same supported distribution path: run Gloss locally
through npm or Bun, then open the local URL it prints.

```bash
npx gloss-local@latest
# or
bunx gloss-local@latest
```

This path needs Node.js 18+ and Python 3.11+. It does not require an `.exe`,
`.app`, or `.dmg`. Debian / Ubuntu users may need `python3-venv`:
`sudo apt install python3-venv`.

## Preparing the 2.0 Beta

1. Merge the reviewed glossX-integration application branch into the existing Gloss repository's main branch.
2. Merge the glossX-integration website branch after the application branch. This deploys the updated homepage through the existing GitHub Pages workflow.
3. Check CI for the main commit. Tag that commit v2.0.0-beta.1 and push the tag.
4. The release workflow builds the source archive, npm tarball, and Windows portable executable and attaches them to a draft prerelease. Copy the reviewed text from [the Beta release notes](release-notes/v2.0.0-beta.1.md) into the draft.
5. Check the asset names and open the portable application, then publish the draft with the prerelease flag enabled.
6. To publish npm, dispatch the release workflow for the v2.0.0-beta.1 tag with publish_npm enabled. It publishes to the next dist-tag. Configure NPM_TOKEN or npm trusted publishing on the Gloss repository first.
7. Confirm the GitHub download and npm next version before changing the homepage's source-first install section to package downloads.

The current 1.x npm latest tag and the existing GitHub latest stable release continue to serve users until a stable 2.0 release is explicitly published. For stable 2.0, bump all runtime/package versions to 2.0.0 before tagging v2.0.0.
