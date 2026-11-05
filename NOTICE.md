# Third-party notices and source provenance

Sprintwake Studios 0.11 uses an independently authored production engine and
contracts under the [MIT License](LICENSE). Its HTTP interfaces and schema-v1
project formats preserve interoperability; the prior Storyboard implementation,
web client, copied contract modules and director guide are absent from the current
source and app bundle. See [CC_REPLACEMENT.md](CC_REPLACEMENT.md).

Earlier public commits included Storyboard by Peter Chodyra (candco.com.au),
[crashtestoz/storyboard](https://github.com/crashtestoz/storyboard), under
CC BY-NC-SA 4.0. Those historical files are **not relicensed**. Their attribution
and [original license at the last CC baseline](https://github.com/oddmundleikvoll/sprintwake-studios/blob/492a6bf/engine/LICENSE)
remain in Git history. This notice records that history and does not apply its
noncommercial restriction to independently authored replacement code.

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
