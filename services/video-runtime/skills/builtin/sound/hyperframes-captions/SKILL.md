---
name: hyperframes-captions
description: 成片上叠词和标题。转写后由你写 HyperFrames HTML，交给 media.hyperframes_caption 渲染。用户要动态字幕、标题、karaoke、花字时用。
---

# HyperFrames Captions

成片已经有了。你要做的是**在画面上叠字**：唱词、对白、标题、花字。不是另做一条片子。

**你不是模板填充器。** 字长什么样由你写进 HTML。判断就是流程。

## 怎么走

1. 对着**已经叠好音的成片**跑 `media.transcribe`。时间戳跟着成片走；用户没说翻译就保持原语言。
2. 自己看画面、歌、剧情。需要合同和字号时再读：
   - `video_skill_load("hyperframes-core")` — `data-*`、轨道、子合成
   - `video_skill_load("hyperframes-media")` → `video_skill_read_resource("hyperframes-media", "references/captions/authoring.md")` — 分组、位置、字号量级
   - 要动再 `video_skill_load("hyperframes-animation")`；registry 组件当起点再改时 `video_skill_load("hyperframes-registry")`
3. **写出 caption HTML**（把 transcript 的词和时间写进去），通过 `media.hyperframes_caption` 的 `caption_html` 交出去。成片垫在底下。你写的是这一层：根跟着成片走（多长、多宽、多高，转写和画面已经告诉你），句跟着它唱的那几秒。GSAP 这份工程里已经有了。不要自己跑 `npx hyperframes`。没有 `caption_html` 这次叠字就不会跑。
4. 同一条成片、同一份 transcript，一次叠完。

用户说不要字，这一步就停。静态硬烧另走 `video_skill_load("subtitle-authoring")`。

## 你自己定

词跟着唱还是整句出、要不要 karaoke、要不要标题、底栏还是花字、抄哪个 registry 组件再改——全由你定。用户点了样子就听用户的。抄演示页打开再改的时候，时长和画幅换成你面前这条成片。同一位置一次看见一句。

每步读不读、写多花、改几轮，也由你定。

## 质量红线

1. 中文必须能显示。运行时已经带 CJK 字体；不要换成只会写英文的网字。
2. 字是给人读的，不要把主体挡到看不清。
3. registry 里的 `caption-*` 是起点，打开再改。海报级大字（一层 200px 背景词）不要原样灌整段歌词。
4. 缺 Node / Chrome / GSAP / CLI 就把报错原样端上来，不要偷偷改成静态字幕。
5. 必须写出完整 HTML 放进 `caption_html`。
