# Content moderation — IMAGE

Apply when writing or evaluating T2I / keyframe prompts.

## nano_banana_halo (Nano Banana / Nano Banana 2 / Nano Banana Pro)

```xml
<rule name="Nano Banana Halo Physics">
  <definition>
    在描述光影效果时，应极其谨慎地使用 "halo" 或其近义词。
  </definition>
  <reason>
    模型对 "halo" 等词汇的理解倾向于具象化，容易将预期的"环境氛围光"误生成为宗教性质或实体性质的"头部光环"，造成视觉风格偏离。
  </reason>
  <action_protocol>
    - 词汇规避: 除非明确需要头部光环效果，否则严禁使用 "halo", "angelic glow", "sacred light", "celestial glow"
    - 环境光替代: 推荐使用 "Volumetric lighting" (体积光), "Cinematic soft lighting" (电影感柔光), "Ethereal ambient glow" (超凡环境光)
  </action_protocol>
</rule>
```

## aesthetic_and_anatomical_integrity

```xml
<rule name="Aesthetic and Anatomical Integrity">
  <definition>
    防止解剖结构不连续和过度的生物学细节描述，避免引起视觉不适或恐怖谷效应。
  </definition>
  <reason>
    AI 模型可能会生成漂浮的肢体或过于写实的生物纹理，违反审美标准并造成感官不适。
  </reason>
  <action_protocol>
    - 解剖连贯性: 即使是特写镜头也要包含连接点（脖子、肩膀），防止出现"漂浮的头"。确保主体牢牢扎根于环境中
    - 洁净美学: 避免显微镜级的生物细节（如舌头颗粒、唾液、粘性残留物）。保持干净、优雅的电影级表面质量
    - 比例平衡: 优先考虑符合审美的比例，而非原始的生物学准确性。保持柔和、吸引人的特征
  </action_protocol>
</rule>
```

## spatial_scale_and_perspective

```xml
<rule name="Spatial Scale and Perspective Integrity">
  <definition>
    防止尺寸失真，即主体和背景元素的比例在视觉上异常接近，违反现实世界的比例。
  </definition>
  <reason>
    AI 倾向于平衡所有提到元素的尺寸，使它们同等可见，导致空间层级崩溃和错误的比例关系。
  </reason>
  <action_protocol>
    - 主体比例: 指定主体在画面中的视觉比例，以锚定其预期的突出地位
    - 参考缩放: 引入已知尺寸的参考物体以建立比例逻辑
    - 地平线逻辑: 定义地平线高度（高、低、平视）以控制透视深度
  </action_protocol>
</rule>
```
