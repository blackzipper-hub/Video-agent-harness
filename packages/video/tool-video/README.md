# @cuti-ai/tool-video

English | [中文](README.zh.md)

Stable, high-level tools that let a DeepSeek agent operate a versioned video project without exposing provider-specific calls.

## Model Experience

### Project video tools

#### What the model sees

Twelve `video_*` tools expose project creation and planning, project inspection, structured edit and dependency impact previews, rebuild execution, durable build control, artifact selection, and export. `video_edit_preview` returns cost and affected steps but never executes paid work. Provider credentials, internal tasks, plugin hooks, and raw media operations are absent from the schemas.

#### Token effect

Fixed schema cost while the tool plugin is enabled; results are data-dependent and bounded summaries.

#### KV Cache effect

Prefix-stable while the enabled tool set is unchanged.

## Known Limitations and Deferred Work

- Project ids are explicit tool arguments in the first release; automatic active-project injection into tool arguments remains deferred.
