# Changelog

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
