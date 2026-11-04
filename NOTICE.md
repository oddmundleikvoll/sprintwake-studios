# Third-party notices and source provenance

Sprintwake Studios includes a modified copy of **Storyboard**, originally from
[crashtestoz/storyboard](https://github.com/crashtestoz/storyboard).
Copyright (c) 2026 Peter Chodyra (candco.com.au).
The original copyright notice and full **CC BY-NC-SA 4.0** terms are preserved in
[engine/LICENSE](engine/LICENSE). This requires attribution, noncommercial use
and sharing adaptations on the same terms; it is not an MIT or Apache license.
Sprintwake Studios additions use the same terms in [LICENSE](LICENSE), as chosen
for the first public version. Separately licensed dependencies retain their own
terms; this notice does not relicense them.

The copy in `engine/` has been modified for the native studio, Qwen/LTX and Comfy
workflow providers, production review and continuity, director/editorial
contracts, private settings, process ownership and safe application updates.
`contracts/` contains copies of engine modules and its director guide. Those
copies retain the engine's licensing and provenance. See
[engine/UPSTREAM.md](engine/UPSTREAM.md) and [contracts/README.md](contracts/README.md).

## Dependencies downloaded during setup

These dependencies are not committed as runtime binaries or vendored source in
this repository. Setup fetches them at the versions recorded in
[runtime/versions.json](runtime/versions.json) and the dependency locks.

| Component | Source | License information |
| --- | --- | --- |
| Raven native UI | [oscar-soirey/Raven](https://github.com/oscar-soirey/Raven), revision `abed7e6a695524d41ec73b5e29b46343d5293cb3` | Apache-2.0; two local Cocoa patches are in `runtime/patches/`. |
| MoltenVK | [KhronosGroup/MoltenVK](https://github.com/KhronosGroup/MoltenVK), v1.4.2 | Apache-2.0 and its accompanying third-party notices. |
| Sparkle | [sparkle-project/Sparkle](https://github.com/sparkle-project/Sparkle), 2.10.1 | MIT-style main license and bundled external licenses in its `LICENSE` file. |
| Python and Python packages | `uv.lock` and `runtime/requirements-bundle.txt` | Each distribution retains its own license; installing a package does not relicense it. |
| FFmpeg | Binary supplied by the locked `imageio-ffmpeg` package | The wrapper's license and the FFmpeg binary's license are distinct. Review the exact binary's build options, license and corresponding-source obligations before distributing an app. |

The app builder gathers available runtime license files and records the exact
FFmpeg version/build configuration and SHA-256. That collection alone is not a
completed binary redistribution review. A downloadable app additionally needs
that review, Developer ID/notarization and release qualification; see
[DISTRIBUTION.md](DISTRIBUTION.md).

Model weights, model services and inference runtimes are external to the source
repository and have their own terms. Connecting one does not grant any extra
rights to its weights or generated media. No model weights are distributed here.
