"""Bounded language-service adapters.

SPDX-License-Identifier: MIT
Copyright (c) 2026 Oddmund Leikvoll
"""
from __future__ import annotations
import json, urllib.error, urllib.request
from urllib.parse import urlsplit

KINDS={"openai-compatible","openai-api","ollama","anthropic"}
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, new): raise urllib.error.HTTPError(req.full_url,code,"redirect refused",headers,fp)

class LanguageService:
    def __init__(self, profile, api_key=""):
        self.profile=dict(profile or {}); self.api_key=api_key or ""; self.timeout=min(max(float(self.profile.get("timeout",1200)),.1),3600); self.max_bytes=min(max(int(self.profile.get("maxResponseBytes",4_000_000)),1024),16_000_000); self._endpoint=self._validate_endpoint(self.profile.get("url") or self.profile.get("endpoint"))
    @staticmethod
    def _validate_endpoint(value):
        if not isinstance(value,str): return ""
        p=urlsplit(value)
        if p.scheme not in {"http","https"} or not p.hostname or p.username or p.password or p.query or p.fragment: raise ValueError("endpoint must be an HTTP(S) origin without credentials/query")
        return value.rstrip("/")
    def available(self):
        kind=self.profile.get("kind",self.profile.get("provider"))
        if kind not in KINDS: return False,"unsupported language provider"
        if not self._endpoint: return False,"endpoint is not configured"
        if not isinstance(self.profile.get("model"),str) or not self.profile["model"].strip(): return False,"model is not configured"
        return True,"configured"
    def _request(self,path,body,headers):
        endpoint = self._endpoint + path
        req=urllib.request.Request(endpoint,data=json.dumps(body).encode(),headers=headers,method="POST")
        try:
            with urllib.request.build_opener(_NoRedirect).open(req,timeout=self.timeout) as response:
                raw=response.read(self.max_bytes+1)
                if len(raw)>self.max_bytes: raise RuntimeError("language service response exceeds limit")
                return raw,response.headers.get("Content-Type","")
        except urllib.error.HTTPError as exc:
            detail=exc.read(4096).decode("utf-8","replace"); raise RuntimeError(f"language service HTTP {exc.code}: {detail[:240]}") from exc
        except urllib.error.URLError as exc: raise RuntimeError("language service transport failed") from exc
    def _completion_path(self):
        return "/chat/completions" if self._endpoint.endswith("/v1") else "/v1/chat/completions"
    def _anthropic_path(self):
        return "/messages" if self._endpoint.endswith("/v1") else "/v1/messages"
    def _ollama_path(self):
        return "/chat" if self._endpoint.endswith("/api") else "/api/chat"
    def chat(self,messages,stream=False,json_mode=True,think=None,on_progress=None):
        ok,reason=self.available()
        if not ok: raise RuntimeError(reason)
        if not isinstance(messages,list) or not all(isinstance(m,dict) for m in messages): raise ValueError("messages must be a list of objects")
        kind=self.profile.get("kind",self.profile.get("provider")); kind={"openai-api":"openai-compatible"}.get(kind,kind); headers={"Content-Type":"application/json","Accept":"text/event-stream" if stream else "application/json"}
        if self.api_key: headers.update({"x-api-key":self.api_key,"anthropic-version":"2023-06-01"} if kind=="anthropic" else {"Authorization":"Bearer "+self.api_key})
        if kind=="anthropic":
            system=[m.get("content","") for m in messages if m.get("role")=="system"]; body={"model":self.profile["model"],"messages":[m for m in messages if m.get("role")!="system"],"max_tokens":int(self.profile.get("max_tokens",4096))}; path=self._anthropic_path()
            if system: body["system"]="\n\n".join(str(x) for x in system)
        elif kind=="ollama": body={"model":self.profile["model"],"messages":messages,"stream":bool(stream)}; body.update({"format":"json"} if json_mode else {}); path=self._ollama_path()
        else:
            body={"model":self.profile["model"],"messages":messages,"stream":bool(stream)}; body.update({"response_format":{"type":"json_object"}} if json_mode else {})
            # Accept either an OpenAI-compatible origin or an explicit /v1 base.
            path=self._completion_path()
        raw,ctype=self._request(path,body,headers)
        if stream or "text/event-stream" in ctype: return self._consume_sse(raw,on_progress)
        try: data=json.loads(raw)
        except ValueError as exc: raise RuntimeError("language service returned invalid JSON") from exc
        if kind=="anthropic": return "".join(str(x.get("text","")) for x in data.get("content",[]) if isinstance(x,dict))
        if kind=="ollama": return str(data.get("message",{}).get("content",data.get("response","")))
        choices=data.get("choices",[]); return str(choices[0].get("message",{}).get("content","")) if choices else ""
    @staticmethod
    def _consume_sse(raw,on_progress):
        result=[]
        for line in raw.decode("utf-8","replace").splitlines():
            if not line.startswith("data:"): continue
            payload=line[5:].strip()
            if payload=="[DONE]": continue
            try: obj=json.loads(payload)
            except ValueError: continue
            delta=obj.get("choices",[{}])[0].get("delta",{}).get("content") or obj.get("message",{}).get("content") or ""
            if delta:
                result.append(str(delta))
                if on_progress: on_progress(str(delta))
        return "".join(result)
