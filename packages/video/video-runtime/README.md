# @cuti-ai/video-runtime

English | [中文](README.zh.md)

Service definition for the project-oriented video runtime. DeepSeek owns dialogue and tool selection; this service owns projects, immutable versions, artifact dependencies, structured edit previews, incremental rebuilds, validation, and exports.

## Model Experience

### Runtime service

#### What the model sees

Nothing directly. The service owns typed project operations consumed by `@cuti-ai/tool-video`; the tool package owns all model-visible schemas and result text.

#### Token effect

None directly; only the consuming tool's request and bounded result contribute tokens.

#### KV Cache effect

None directly because this package registers no prompt text or tool schema.

## Known Limitations and Deferred Work

- The first provider uses HTTP and delegates durable task recovery to the Python Video Runtime.
