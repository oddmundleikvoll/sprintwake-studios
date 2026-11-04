# Portable model connections — 2026-11-04

> Historical implementation plan for the 0.8.0 model connections. The standalone runtime/distribution work followed in 0.9.0; qualified model limits still apply.
> Current goal, status and remaining work: [PROJECT.md](PROJECT.md), [STATUS.md](STATUS.md).

Goal: users can configure installation paths, own language-model services and
explicit Comfy image/video workflow profiles. Existing local Qwen/LTX/oMLX setup
remains the discovered default; existing productions/configuration are preserved.
No publication, model downloads or model inference for qualification.

Seams and implementation:
1. Private versioned native connection settings, home-relative defaults, explicit
   engine/runtime/workspace paths and portable CLI. Ship pure versioned director/
   storage contracts so the setup UI opens before an engine is configured.
2. Core CLI accepts separate service settings directory and optional media profile
   document; existing engine-root settings remain default. Registry can compose
   explicit Comfy workflow image/video backends or disabled providers. Native LTX
   long-film controls remain exclusive to their qualified Qwen/LTX profile.
3. Native connection/profile forms with passive tests and private key storage;
   selected profiles drive new project defaults and generation. Changes apply on
   ordinary restart, without moving existing data or changing active jobs.
4. Provide schema/examples, CPU runtime setup instructions and a clean-install
   smoke using synthetic media and inert Comfy/LLM fixtures (no personal paths).
5. Focused contracts/network/secret/isolation tests; existing full Python/native
   checks, real-window connection flow and independent correctness/security review.

Done: alternate runtime/engine/root and Comfy URL work; own OpenAI-compatible
chat and declared image/video workflows run through real HTTP adapters with fixture
services; unsupported features fail before generation; readiness never generates;
secrets stay out of projects/recipes/logs and default local configuration retains
its values. Preserve take/trim/history, queues and owned-process shutdown contracts.
