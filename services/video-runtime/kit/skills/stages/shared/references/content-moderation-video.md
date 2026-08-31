# Content moderation — VIDEO

Apply when writing I2V / storyboard / video motion prompts.

## sora_human_restrictions (Sora / Sora Pro only)

Apply when the brief says the video tool is OpenAI Sora or Sora Pro.

### Sora 人物词汇限制 (Sora Human Vocabulary Restrictions)
*   **定义 (Definition)**: 严禁在 Prompt 中使用直接指代人类身份的词汇，如 "girl", "boy", "man", "woman", "human", "child" 等。
*   **理由 (Reason)**: Sora 的安全审核机制对特定身份词汇极其敏感，使用这些词汇会显著增加触发内容拦截（Safety Filter）的概率，导致生成失败。
*   **执行协议 (Action Protocol)**:
    - **中性词替换**: 必须将身份词汇替换为中性描述词。例如：
        * "girl/woman" → "figure", "character", "silhouette"
        * "boy/man" → "figure", "character", "silhouette"
        * "human/people" → "entities", "figures"
    - **去身份化描述**: 专注于描述主体的动作、姿态、服装和环境特征，而非其生理身份。
    - **示例修正**:
        * ❌ "A girl dancing" → ✅ "A graceful figure dancing"
        * ❌ "Two people walking" → ✅ "Two characters walking"

## i2v_locked_angle (all I2V tools)

### 角度锁定原则 (The Locked-Angle Principle)
*   **定义 (Definition)**: 主体在视频全过程必须基本保持相对于相机的原始朝向角度。严格禁止任何 Y 轴旋转（如转身、回头、侧脸转正、原地旋转）。
*   **理由 (Reason)**: I2V 模型缺乏真实的 3D 空间理解。旋转角度 >30 度会迫使 AI 幻觉出不可见的特征（如另一只耳朵、后脑勺），导致严重的身份坍缩和人脸扭曲。
*   **执行协议 (Action Protocol)**:
    - **侧重平面与线性运动**: 指导 AI 沿 X 轴（左右）或 Z 轴（前后）生成运动，但锁定 Y 轴。
    - **鼓励肢体表达**: 明确允许手臂、手部、腿部的自由摆动和身体晃动，前提是躯干和头部的朝向保持稳定。
    - **抽象指令**: 描述当前姿态内的动作（如舞蹈、行走、手势），而非改变姿态的角度。

## i2v_resolution_framing (all I2V tools)

### 分辨率与构图比例 (The Resolution-Framing Ratio)
*   **定义 (Definition)**: 避免在“全景/全身镜头 (Full Body Shot)”中同时要求“高精度面部细节 (High Fidelity Facial Details)”。
*   **理由 (Reason)**: 在标准视频画布（如 1080p）上，全身镜头中的面部占比不到总像素的 5%。在如此小的像素网格中物理上无法渲染微小的面部细节或有神的眼睛。
*   **执行协议 (Action Protocol)**:
    - **构图覆盖**: 如果用户要求面部清晰度，自动将构图描述修正为“牛仔镜头 (Cowboy Shot)”（膝盖以上）或“腰部以上镜头 (Waist-up Shot)”。
    - **像素优先级**: 牺牲脚部/鞋子的可见性，以为面部争取更高的像素密度。

## i2v_temporal_stability (all I2V tools)

### 时域稳定性增强 (Temporal Stability Enforcement)
*   **定义 (Definition)**: 禁止出现会导致帧间像素剧烈变化的画面元素，特别是“频闪灯 (Strobe Lights)”、“闪烁特效 (Flashing Effects)”或“剧烈的相机抖动 (Fast Camera Shakes)”。
*   **理由 (Reason)**: 快速的高对比度变化会干扰扩散模型的时域注意力层，导致面部闪烁、画面抖动伪影以及连贯性丧失。
*   **执行协议 (Action Protocol)**:
    - **灯光稳定化**: 将“闪烁/频闪灯”修正为“体积光 (Volumetric lighting)”、“电影感柔光 (Cinematic soft lighting)”或“缓慢移动的动态光”。
    - **运动平滑化**: 始终执行“慢动作 (Slow Motion)”或“高帧率摄影 (High-Speed Camera)”关键词。这会强制模型生成更多中间帧，从而平滑任何潜在的抖动。

## i2v_source_fidelity (all I2V tools)

### 源图保真原则 (Source Fidelity)
*   **定义 (Definition)**: 严禁描述参考图中完全不存在的元素**或交互行为**。
*   **理由 (Reason)**: AI 无法稳定地生成第一帧中不可见的**复杂解剖学交互**（如抚摸、握手、拥抱）。这会导致“变形伪影”，即背景纹理突然变成畸形的肢体。
*   **执行协议 (Action Protocol)**:
    - **画布边界**: 仅描述源图中实际存在的解剖结构和环境。
    - **交互禁令**: **严格禁止物理交互（抚摸、握手、拥抱），除非交互双方在源图中均清晰可见。**
    - **替代方案**: 专注于主体的“反应”而非“交互”（例如：将“手抚摸狗”改为“狗因为享受而闭上眼睛”）。

## i2v_spatial_trajectory (all I2V tools)

### 空间轨迹对齐 (Spatial Trajectory Alignment)
*   **定义 (Definition)**: Prompt 中的运动描述必须与主体在参考图（第一帧）中的实际位置严格对齐。
*   **理由 (Reason)**: 如果主体位于图像中心，却要求其“从左侧进入”，会迫使模型生成一个重复的主体（重影伪影），最终与原主体合并，造成严重的逻辑不一致。
*   **执行协议 (Action Protocol)**:
    - **位置检查**: 如果主体已清晰可见或位于中心，禁止使用“从[侧面]进入”、“走入画面”或“出现”等词汇。
    - **轨迹修正**: 替代“从左侧进入”，使用“向前移动”、“向相机走来”或“身体微微转动”。
    - **在场假设**: 假设主体已经在场景中。描述他们在原地的动作 or 前进，绝非“抵达”。
