# Release downloads

Tag builds publish these assets alongside the npm tarball:

| Platform | Release asset | Contents |
| --- | --- | --- |
| Windows | `Gloss-windows-x64.exe` and `Gloss.exe` | Standalone desktop application |
| Linux / macOS | `gloss-local-<version>.tgz` | Run the local browser app through npm or Bun |

The Windows executables are portable: download the matching `.exe` and open it
directly—no archive extraction, terminal, or browser tab is needed. `Gloss.exe`
is kept as an x64 compatibility download. The portable desktop app bundles its Chromium-based Electron shell.
The Windows build targets x64; Windows on ARM runs it through x64 emulation.

## Version channels

On the Gloss repository, Git tags that contain a prerelease suffix, such as `v2.0.0-beta.1`, create GitHub prereleases. For npm publication, dispatch the release workflow for the exact version tag and enable `publish_npm`. The workflow publishes prereleases to `next` and stable builds to `latest`.

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
