# Publication check 2026-11-07

The working tree is prepared for review and local commits. No branch push, tag,
release publication or deployment was performed. New documentation and imports
become available at their GitHub links only after the maintainer pushes them.

## Verified

- All project linters pass: ShellCheck, Hadolint, yamllint, actionlint, Ruff,
  Cppcheck, Markdown, dotenv, JSON and text/image hygiene.
- The initial 99 offline regression tests pass, both normally and with ASan/UBSan.
  A new image-lint regression checks valid signatures, mismatched images and
  unknown binary files; the final suite contains 100 tests.
- Alpine production image builds locally; strict compiler warnings are errors.
  Non-root/read-only runtime, fake device authentication and legacy-volume
  ownership migration pass the container-hardening script.
- Isolated real MQTT integration: 2 tests pass. Slow HTTP refresh integration:
  3 tests pass. Both containers run with `--network none`.
- Trivy 0.70.0 scans the exported production image with the CI gate
  `--severity CRITICAL --ignore-unfixed`: no findings at that gate. It warns that
  Alpine 3.24 is absent from its EOL list; this is not a full vulnerability or
  support-lifecycle assurance. No HIGH-severity gate was run.
- Detailed and development Compose templates validate with synthetic required IDs.
  Public GHCR `latest` manifest is readable and lists amd64 and arm64.
- Tar backup and restore reproduce a synthetic file in independent volumes with
  UID 10001 ownership. No production tokens were copied or used.
- Fresh openHAB 5.2.3: first administrator created in a disposable local container,
  introduction skipped, canonical widgets loaded through YAML, synthetic Items
  assigned states, both variants rendered and opening details expanded. Screenshots
  contain only fictional names/values. Temporary page/container and anonymous
  container volumes were removed afterwards.
- MainUI import files are generated from the canonical widget definitions;
  `generate_imports.py --check` is enforced in CI.
- GitHub description/topics updated and private vulnerability reporting enabled
  and read back as enabled. No GitHub releases existed at inspection time;
  existing release text therefore required no edit. Future successful tag builds
  prepare generated notes as a draft only if no release/draft already exists.

## Privacy and repository cleanup

No AGENTS.md or private records were tracked in the initial checkout. Ignore rules
now cover local instructions, private archives, backups and measurements in both
Git and Docker. The duplicate tracked `docker-compose.yml` is removed from the
index only and remains locally; the canonical public deployment example is
`docker-compose.example.yml`. Diagnostic tools remain in `tests/`, next to their
regression tests; production scripts stay in `resources/`. The empty ignored
measurement directory contained no experiment data to archive. Local review
outputs are retained under ignored `private/archive/2026-11-07/`.

A filename review of all available local refs found historical `.env.example`
and `.env.sample`, no `.env`, token bundle or AGENTS.md paths. This is not a
forensic secret scan of every historical blob. No history was rewritten.

## Remaining before publication

- Review and explicitly push these commits before publishing the wiki article:
  its links target the new documentation on `main`.
- A live BMW login and end-to-end first receipt with a real vehicle were not run.
  BMW portal labels, account eligibility, field availability and update frequency
  remain service-dependent; there is no guarantee of continuous fresh data.
- Native MainUI YAML editor paste/save, Homie discovery against a fresh real
  BMW stream and existing-version upgrades remain manual acceptance checks.
  File-based rendering and expression tests do not establish those paths.
- No multi-day soak, amd64 build or Debian alternative build was run in this
  review. The local Docker host built arm64. CI defines the amd64 build check.
- The release-notes workflow is linted but has not run on GitHub. It preserves
  existing releases, including empty/manual drafts, rather than overwriting text.
- Review the article and upload the article image/screenshots to the wiki if
  desired. No wiki upload or publication was performed.
