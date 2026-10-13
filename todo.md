# TODO

Remaining work from the code and performance review on 2026-10-13. Token response
validation, Homie cache checkpoints, delta publication, reduced logging and the
Compose `tmpfs` mount are implemented. Track remaining work and completed follow-ups below.

- [ ] Measure CPU time, physical disk writes, cache/flush calls, MQTT publications
  and log volume on the target host using the same recorded message workload
  before and after optimization. Include idle periods, bursts and reconnects.
- [ ] Add a per-device index from BMW field names to Homie property IDs to avoid
  scanning the complete property registry for every incoming field.
- [ ] Avoid building RAW/Legacy topics when RAW output is disabled; review remaining
  JSON copies, topic construction and repeated formatting against measured CPU costs.
- [ ] Bound the pending Homie update queue and define explicit overload behavior.
  Evaluate coalescing state updates per vehicle/field while preserving receipt time
  and any data that consumers need as events.
- [ ] Evaluate moving token refresh and cache persistence off the main loop, so
  slow HTTP requests or disk flushes cannot delay watchdogs and telemetry processing.
- [ ] Store the token set consistently as one recoverable state. Consider an atomic
  token bundle or versioned generations, with migration from the existing three
  files and tests for failures during commit and restart recovery.
- [ ] Separate configuration, token management, MQTT lifecycle and telemetry into
  independently compilable components; reduce Homie's dependency on bridge globals.
- [x] Correct the remaining openHAB documentation and widget descriptions from
  `telemetry#last-update` to `telemetry#lastUpdate`; check examples against current behavior.
- [ ] Add integration tests with a real local MQTT broker for delta updates,
  discovery, reconnect replay, retained state, Last Will and shutdown acknowledgements.
- [ ] Add failure/load tests for full or read-only storage, interrupted persistence,
  slow HTTP requests, message bursts and sustained operation.
- [ ] Add automated checks for openHAB widget expressions, state mappings, unit
  conversion and missing/invalid Item values, alongside YAML validation.
- [ ] Run the container without root, use a read-only root filesystem, drop
  unnecessary capabilities and enable `no-new-privileges`. Verify authentication,
  token-volume ownership, `tmpfs` access and existing-volume migration first.
