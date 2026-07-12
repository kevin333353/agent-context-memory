# Python Resolver Hotfix Design

## Problem

On Windows PowerShell, `Get-Command python -CommandType Application` can return more than one executable. The v0.2.0 installer reads `.Source` directly and passes the resulting string array to the call operator. PowerShell renders the array as one space-separated command name, so installation fails before the managed virtual environment is created. v0.2.1 retains the same behavior.

## Scope

- Resolve a single usable Python 3.9+ executable when multiple `python` applications are present on `PATH`.
- Use the same safe selection behavior in the installer and the hook runtime fallback.
- Preserve the managed `.venv` as the runtime's first choice.
- Add regression coverage for duplicate `python` applications on `PATH`.
- Publish the change as v0.2.2 without moving or replacing existing tags.

## Design

The installer will enumerate all `python` application commands, deduplicate their source paths case-insensitively, and probe them in discovery order. A candidate is usable only when invoking it with a version check exits successfully and reports Python 3.9 or newer. The first usable candidate is returned as one scalar path. Failed candidates, including inactive WindowsApps aliases, are ignored while the resolver continues.

If no candidate passes, installation will stop with an actionable error stating that Python 3.9 or newer is required. The installer will use the resolved scalar path for both the version check and virtual environment creation.

`Get-ContextMemoryPythonPath` will continue to prefer `.venv\Scripts\python.exe`. When that managed runtime is absent, its fallback will use the same candidate enumeration and probe rules instead of returning every `Get-Command` result.

No new public installer parameter or dependency will be introduced.

## Error Handling

- Candidate invocation errors and nonzero exits reject only that candidate.
- Duplicate paths are probed once.
- An unavailable or incompatible candidate does not prevent later candidates from being checked.
- No usable Python produces a clear terminal installer error; hook fallback returns no runtime and retains its existing fail-open behavior.

## Testing

The PowerShell protocol suite will prepend two `python` command shims to a temporary `PATH` and run the real installer. The test must fail against v0.2.1 because the installer receives multiple command sources, then pass after the resolver returns one usable path.

Additional assertions will cover scalar selection and the existing managed-runtime installation. The full Python test suite, PowerShell protocol tests, compile checks, installer parser checks, and release-string checks must pass before publishing.

## Release

Update `VERSION`, `CHANGELOG.md`, README pinned installer commands, installer version messages, and release assertions to v0.2.2. Commit on `fix/python-resolver-v0.2.2`, push it, open a draft pull request, verify checks, merge, tag the merge commit as `v0.2.2`, and publish a GitHub release with the corrected one-line installer command.
