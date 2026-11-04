# Sprintwake Studios distribution — 2026-11-04

> Historical implementation plan. Standalone installation and local signed Sparkle update/relaunch passed in 0.9.0. Public signing/publication remains separate.
> Current goal, status and remaining work: [PROJECT.md](PROJECT.md), [STATUS.md](STATUS.md).

User outcome: one self-contained macOS app and one GitHub repository, with signed
Sparkle updates from Releases. Preserve existing productions/model configuration;
finish active work before replacing/relaunching. No source/release publication.

Current source-linked launcher is replaced with an embedded standalone Python,
native Raven/MoltenVK/media/update bridges, FFmpeg and the production engine.
Models/Comfy services stay external. The working native checkout remains in its
existing directory; engine source is synchronized into engine/ for the single
repository, excluding personal config/media/models/build trees.

Implementation order:
1. Pin Sparkle and generate a project-scoped EdDSA signing key in Keychain;
   create only an empty private oddmundleikvoll/sprintwake-studios repository.
2. Add authenticated idle reservation to the owned engine and isolate every
   mutable config/timing/lock path outside the read-only bundle.
3. Add native Sparkle bridge and wait/save/drain/handoff state; keep new work
   blocked while handoff proceeds. Never approve replacement from stale/unknown
   status or before the owned child actually exits.
4. Build and audit a relocatable app, preserve legacy settings on local install,
   and produce only local signed ZIP/appcast artifacts for eventual Releases.
5. Verify pure state/concurrency/migration tests, complete existing checks,
   relocated read-only bundle with clean data and no source dependencies, and
   a local signed update fixture. Review independently before installing.

Brand: Sprintwake Projects' existing vector geometry, new charcoal/plum field,
copper route and pale-gold endpoint. Preserve application Copper typography/color
system and legacy bundle/data/Keychain identities. Public Developer ID signing
and notarization remain conditional on a certificate unavailable on this Mac.
