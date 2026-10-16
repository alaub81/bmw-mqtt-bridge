# TODO

Remaining work from the code and performance review on 2026-10-13. Token response
validation, Homie cache checkpoints, delta publication, reduced logging and the
Compose `tmpfs` mount are implemented. Track remaining work and completed follow-ups below.

- [ ] Measure CPU time, physical disk writes, cache/flush calls, MQTT publications
  and log volume on the target host using the same recorded message workload
  before and after optimization. Include idle periods, bursts and reconnects.
  The read-only target-host collector (`tests/resource_soak.py`) and launch
  instructions are ready; the Debian baseline, analysis and complementary
  publication/cache/log measurements remain open.
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
