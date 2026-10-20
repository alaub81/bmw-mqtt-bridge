# TODO

Remaining work from the code and performance review on 2026-10-13. Token response
validation, Homie cache checkpoints, delta publication, reduced logging and the
Compose `tmpfs` mount are implemented. Track remaining work and completed follow-ups below.

- [x] Collect and evaluate the initial Debian 24-hour before/after baselines for
  CPU, process I/O, RSS and container health. The ten-minute tmpfs control run
  eliminated the recurring process-write increments seen before the mount was
  applied. These observations do not establish physical disk traffic or an
  identical-workload benchmark.
- [ ] Complete a controlled before/after comparison with the same recorded
  message workload, including idle periods, bursts and reconnects. Attribute
  physical disk writes and separately measure cache/flush calls, MQTT
  publications and Docker log volume; the initial host baselines are complete.
- [x] Add a per-device index from BMW field names to Homie property IDs to avoid
  scanning the complete property registry for every incoming field.
- [x] Avoid building RAW/Legacy topics when RAW output is disabled, parse incoming
  payload bytes without a string copy and move parsed fields into the Homie queue.
  Skip the split-field loop when split output is disabled.
- [ ] Profile remaining JSON processing, topic construction and repeated formatting
  against measured CPU costs before further optimization.
- [x] Bound the pending Homie update queue and define explicit overload behavior:
  preserve FIFO order, drop the oldest waiting message on overflow, and log a
  cumulative dropped count. Coalescing was evaluated and deferred to preserve
  field registration order and schema transitions. RAW/split events are unaffected.
- [x] Move runtime token refresh and cache persistence off the main loop. Use
  single-flight jobs, owned snapshots and generation tracking; retain main-thread
  credential adoption/MQTT management and join workers on shutdown.
- [x] Store the token set as one atomic `tokens.json` bundle shared by authentication
  and refresh. Migrate existing flat token files and test failures before/after
  commit, interrupted migration, corrupt state and restart selection.
- Not planned (project decision, 2026-10-20): split the main bridge into
  independently compiled configuration, MQTT and telemetry components. Keep the
  current structure for this small project.
- [x] Correct the remaining openHAB documentation and widget descriptions from
  `telemetry#last-update` to `telemetry#lastUpdate`; check examples against current behavior.
- [x] Add integration tests with a real local MQTT broker for delta updates,
  discovery, reconnect replay, retained state, Last Will and shutdown acknowledgements.
- [ ] Complete a multi-day target-host soak and evaluate recorded results. The
  read-only collector supports up to seven days with service/version attribution;
  the offline summary and 72-hour launch procedure are ready. Implemented: real
  slow HTTP bodies, production timeout, shutdown before headers/during a body,
  storage EROFS/ENOSPC,
  interrupted writes, process exit before commit, flush/rename failure cleanup,
  blocked background work and repeated bursts with checkpointing and delta suppression.
- [x] Add automated checks for openHAB widget expressions, state mappings, unit
  conversion and missing/invalid Item values, alongside YAML validation.
- [x] Run the container without root, use a read-only root filesystem, drop
  unnecessary capabilities and enable `no-new-privileges`. Verify authentication,
  token-volume ownership, `tmpfs` access and existing-volume migration first.
