# Independently authored engine work area

Do not copy or translate the retired CC engine/contracts implementation from Git history into this area. Author from native call sites, data formats, tests and documented behavior.
Use fresh architecture; retain public IDs and DTO shapes only for interoperability.
This is the active independent engine. The old implementation remains only in Git history and in the user’s separate preserved Storyboard installation.
Do not change installed apps, existing projects, models, private settings or Comfy jobs.

No inference/model download for implementation tests. Tests here are newly authored
API/behavior tests, not copies of upstream engine tests. Keep secrets out of all
profiles, recipes, logs and project exports. All paths must reject symlinks/escape;
use atomic persistence, locks and conditional revisions for data mutations.
LTX process-group permission errors are uncertain until a validated process-table
snapshot proves no live members remain. Preserve its journal and block admission
when that check fails; zombie-only groups hold no model resources.

Run from the app root:

```sh
runtime/bundle-venv/bin/python -B -m unittest discover -s engine/tests -v
```
