"""Provider facades for native Qwen/LTX workflow profiles.

SPDX-License-Identifier: MIT
Copyright (c) 2026 Oddmund Leikvoll
"""
from .comfy_workflow import ComfyProvider

class NativeCapabilityError(RuntimeError): pass

def _inputs(info, name):
    spec=info.get(name) or {}; inputs=spec.get("input",{})
    return set((inputs.get("required",{}) or {})) | set((inputs.get("optional",{}) or {}))

def qwen_graph(object_info, prompt, width, height, seed, reference_paths=()):
    if len(reference_paths)>10: raise ValueError('Qwen supports at most ten selected reference images')
    required={"UNETLoader","CLIPLoader","VAELoader","TextEncodeQwenImage21","EmptyLatentImage","RandomNoise","ManualSigmas","KSamplerSelect","QwenImage21Cache","CFGGuider","SamplerCustomAdvanced","VAEDecode","SaveImage"}
    missing=sorted(required-set(object_info))
    if missing: raise NativeCapabilityError("native Qwen graph nodes unavailable: "+", ".join(missing))
    g={"1":{"class_type":"UNETLoader","inputs":{"unet_name":"qwen_image_2.1_turbo_bf16.safetensors","weight_dtype":"default"}},"2":{"class_type":"CLIPLoader","inputs":{"clip_name":"qwen3vl_8b_bf16.safetensors","type":"qwen_image","device":"default"}},"3":{"class_type":"VAELoader","inputs":{"vae_name":"qwen_image_2.1_vae_bf16.safetensors"}},"4":{"class_type":"TextEncodeQwenImage21","inputs":{"clip":["2",0],"prompt":prompt,"negative_prompt":"","vae":["3",0],"resolution":width}},"5":{"class_type":"EmptyLatentImage","inputs":{"width":width,"height":height,"batch_size":1}},"6":{"class_type":"RandomNoise","inputs":{"noise_seed":seed}},"7":{"class_type":"ManualSigmas","inputs":{"sigmas":"1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"}},"8":{"class_type":"KSamplerSelect","inputs":{"sampler_name":"euler"}},"9":{"class_type":"QwenImage21Cache","inputs":{"model":["1",0],"device":"cpu","dtype":"default"}},"10":{"class_type":"CFGGuider","inputs":{"model":["9",0],"positive":["4",0],"negative":["4",1],"cfg":1.0}},"11":{"class_type":"SamplerCustomAdvanced","inputs":{"noise":["6",0],"guider":["10",0],"sampler":["8",0],"sigmas":["7",0],"latent_image":["5",0]}},"12":{"class_type":"VAEDecode","inputs":{"samples":["11",0],"vae":["3",0]}},"13":{"class_type":"SaveImage","inputs":{"images":["12",0],"filename_prefix":"sprintwake/qwen-image-2.1-turbo"}}}
    for index,path in enumerate(list(reference_paths),14):
        g[str(index)]={"class_type":"LoadImage","inputs":{"image":path}}
        g["4"]["inputs"][f"images.image_{index-13}"]=[str(index),0]
    return g

def local_qwen_profile(workflow, bindings=None, output=None, capabilities=None):
    """Wrap an actual API-exported Qwen graph; no graph or node is guessed."""
    if bindings is None or output is None:
        raise ValueError("Qwen profile requires the exported graph's bindings and output declaration")
    return {"version":1,"id":"qwen-image-2.1-turbo","label":"Qwen Image 2.1 Turbo","kind":"image","workflow":workflow,"bindings":bindings,"output":output,"capabilities":capabilities or {}}

def local_ltx_profile(workflow, bindings=None, output=None, capabilities=None):
    """Wrap an actual LTX API graph and mark the synchronous release boundary."""
    if bindings is None or output is None:
        raise ValueError("LTX profile requires the exported graph's bindings and output declaration")
    caps={"fps":24,"maxFrames":121,"sizeAlign":64}; caps.update(capabilities or {})
    return {"version":1,"id":"ltx-2.5-mlx","label":"LTX-2.5 Q8","kind":"video","workflow":workflow,"bindings":bindings,"output":output,"capabilities":caps}

__all__=["ComfyProvider","local_qwen_profile","local_ltx_profile","NativeComfyProvider","NativeCapabilityError","qwen_graph"]

class NativeComfyProvider(ComfyProvider):
    def object_info(self): return self._request("GET","/object_info")
    def local_qwen_profile(self, prompt="", width=768, height=512, seed=0, references=()):
        info=self.object_info(); graph=qwen_graph(info,prompt,width,height,seed,references)
        return local_qwen_profile(graph,{"prompt":{"node":"4","input":"prompt"},"width":{"node":"5","input":"width"},"height":{"node":"5","input":"height"},"seed":{"node":"6","input":"noise_seed"}}, {"node":"13","key":"images"},{"sizeAlign":64})
