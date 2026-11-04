"""The installed director's oMLX contract, without changing the signed app."""
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

MODEL = "Qwen3.8-27B-Uncensored-MLX"
REPO = "orcarouter/" + MODEL
REVISION = "34c32b5b1b2c24181e121a5cba3df8924fa66bce"
MODEL_DIR = Path.home() / ".omlx/models" / REPO
CONTEXT = 262144
OUTPUT = 32768


class DirectorOMLX:
    def __init__(self, url="http://127.0.0.1:8000", key=None):
        self.url = url
        self.key = key

    def api_key(self):
        if self.key is None:
            self.key = json.loads((Path.home() / ".omlx/settings.json").read_text())["auth"]["api_key"]
        return self.key

    def request(self, path, payload=None, method=None, timeout=4):
        data = None if payload is None else json.dumps(payload).encode()
        request = Request(self.url + path, data=data, method=method,
                          headers={"Authorization": "Bearer " + self.api_key(),
                                   "Content-Type": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)

    def models(self):
        return self.request("/admin/api/models")["models"]

    def entry(self):
        return next(item for item in self.models() if item["id"] == MODEL)

    def load(self):
        self.request(f"/admin/api/models/{MODEL}/load", {}, timeout=720)

    def release(self, timeout=70):
        """Use oMLX's idle-only TTL eviction; admin unload aborts requests.

        The pool checks in_use and engine activity under its own lock. A
        concurrent direct oMLX request therefore finishes without cancellation.
        A failed acknowledgment leaves the gateway alive and draining.
        """
        deadline = time.monotonic() + timeout
        path = f"/admin/api/models/{MODEL}/settings"
        self.request(path, {"ttl_seconds": 1}, method="PUT")
        try:
            while True:
                entry = self.entry()
                if not entry["loaded"] and not entry["is_loading"]:
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("oMLX bekreftet ikke at regissørmodellen er frigjort")
                time.sleep(.2)
        finally:
            self.request(path, {"ttl_seconds": 300}, method="PUT")


def validate_checkpoint(path=MODEL_DIR):
    config = json.loads((path / "config.json").read_text())
    quant = config.get("quantization")
    if quant != {"bits": 4, "group_size": 64, "mode": "affine"}:
        raise RuntimeError("Regissøren krever vanlig MLX affine 4-bit/group-64")
    index = json.loads((path / "model.safetensors.index.json").read_text())
    if not all((path / file).is_file() for file in set(index["weight_map"].values())):
        raise RuntimeError("Regissørmodellens MLX-vekter er ufullstendige")
    if not config.get("vision_config") or not (path / "preprocessor_config.json").is_file():
        raise RuntimeError("Regissørmodellens synsmodul mangler")
    return config
