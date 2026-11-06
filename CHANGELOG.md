# Changelog

## 0.11.4 — 2026-11-06

- Remove automatic Trash scanning and app-registration cleanup from native
  startup and update handoff. Denied access to Trash no longer prevents the
  studio window and production backend from starting.
- Remove the global Trash reconciliation helper, including its use by Sparkle
  QA. Disposable QA and installation still retire only their own known bundles.
- Preserve exact bundle/build handoff validation, single-instance ownership,
  active-process locks, transactional rollback and verified recovery archives.
- Keep projects, model installations, private connections and existing
  Keychain credentials outside the application bundle.
- Verify the startup/install change with 58 passing focused CPU tests and one
  skipped opt-in macOS probe. No model inference or public Mac release is part
  of this repair.

## 0.11.3 — 2026-11-05

- Prevent duplicate app entries from future managed builds, installs and QA runs.
  Retire obsolete bundles as verified recovery ZIPs and preserve one canonical
  installed application path.
- Keep duplicate cleanup in transactional installation and after a completed
  update handoff. Ordinary app launches no longer scan or clean owned Trash apps.
  Complete cleanup before consuming the update marker, retaining it for retry if
  cleanup fails. Backend boot failures and reconnects cannot lose or repeat it.
- Preserve existing projects, private connections, model installations and
  Keychain credentials. Local packaging and installation use signature and
  bundle-content checks; automated tests, smoke tests and model generation were
  intentionally not run for this update, as requested.
- Publish source and English release notes only; no downloadable Mac release
  or Sparkle appcast is published.

## 0.11.2 — 2026-11-05

### Film direction and continuity

- Give each Qwen reference image an explicit role in the same order as the
  deduplicated image inputs: character identity, location, prop or visual style.
- Build scene frames from the current shot's staging, selected cast, location,
  props and physical state instead of appending the entire film synopsis.
- Carry planned before/after state and prop details into natural LTX direction.
  Distinguish visible characters from offscreen continuity references while
  retaining the complete camera-group identity descriptions.
- Ask the director for purposeful cinematic coverage and carry a bounded summary
  of recent planned states between manuscript batches. Validate new continuity,
  prop, camera-group and dialogue-source fields before saving the draft.
- Add clip-detail controls for planned before/after state, speaker, spoken
  language and delivery. Native dialogue uses an explicit speaker or the single
  visible character; recorded dialogue remains a separate operation.
- Keep a separately planned film score out of native clip music generation.
- Compare the actual video ending with the next planned opening during human
  review. Require the correct boundary images and retain optional observations
  bound to the reviewed clip hashes.
- Retain resolved prompts and reference hashes in production recipes. Archive
  superseded production journals and track native take freshness using prompt,
  direction-version and reference-pixel fingerprints.

### Compatibility and validation

- Existing media, take history and original recipes are preserved. Existing film
  image plans require explicit preparation and review under the new direction
  version. Older takes remain available for deliberate selection.
- External ComfyUI workflow prompt bindings and the native model installations
  retain their existing contracts.
- Automated tests, smoke tests and model generation were intentionally not run
  for this update, as requested. Source changes received static code review;
  local packaging and installation use signature and bundle-content checks.
- This update publishes source and release notes only. It does not publish a
  downloadable Mac release or a Sparkle appcast.

## 0.11.0 — 2026-11-05

- Replace the former CC engine and copied contracts with an independently
  authored MIT production engine and pure contracts.
- Use the installed MIT LTX CLI for local video and native ComfyUI Qwen nodes
  with an independent synchronous memory-release barrier.
- Preserve external projects, private model settings and Keychain credentials.

Historical versions retain their original licenses and attribution. Model,
dependency and hosted-provider terms remain separate from the application license.
