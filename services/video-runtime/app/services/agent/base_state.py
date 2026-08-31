"""
基础状态定义 - 为Workflow和Agent提供共同的状态结构
"""
from typing import Dict, Any, List, Optional, Union
from typing_extensions import Annotated, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from enum import Enum
from pydantic import BaseModel, Field, model_validator

from ...models.user_options import UserOption


# ==================== 任务历史管理模型 ====================

class TaskType(Enum):
    """任务类型枚举"""
    AGENT_PLANNING = "agent_planning"
    WORKFLOW_STORYBOOK = "workflow_storybook" 
    WORKFLOW_VIDEO = "workflow_video"
    # 未来可扩展其他类型


class TaskStatus(Enum):
    """任务状态枚举 - 覆盖所有可能情况"""
    # 进行中状态
    ACTIVE = "active"           # 正在执行
    PLANNING = "planning"       # 规划阶段
    EXECUTING = "executing"     # 执行阶段
    
    # 完成状态
    COMPLETED = "completed"     # 成功完成
    PARTIAL = "partial"         # 部分完成（有产出但不完整）
    
    # 异常状态
    FAILED = "failed"           # 失败（有明确错误）
    STUCK = "stuck"             # 卡壳（多次重试失败）
    TIMEOUT = "timeout"         # 超时
    CANCELLED = "cancelled"     # 用户取消
    INTERRUPTED = "interrupted" # 用户打断开始新任务


# ==================== 步骤链枚举 ====================

class StepActionType(Enum):
    """步骤动作类型枚举"""
    PLAN = "plan"             # 首次规划
    REPLAN = "replan"         # 重新规划
    EXECUTE = "execute"       # 首次执行
    REEXECUTE = "reexecute"   # 重新执行
    VALIDATE = "validate"     # 验证


class StepChainStatus(Enum):
    """步骤链状态枚举"""
    PLANNING = "planning"     # 规划中
    PLANNED = "planned"       # 已规划
    EXECUTING = "executing"   # 执行中
    EXECUTED = "executed"     # 已执行
    VALIDATING = "validating" # 验证中
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 失败





# ==================== 共同的执行结果模型 ====================

class ExecutionResult(BaseModel):
    """统一的执行结果格式 - Workflow和Agent共用"""
    step_id: str = Field(description="步骤ID")
    step_name: str = Field(description="步骤名称")
    status: str = Field(description="执行状态：success/failed/partial")
    structured_output: Optional[Dict[str, Any]] = Field(description="结构化输出", default=None)
    tool_calls_made: Optional[List[str]] = Field(description="调用的工具列表", default=None)
    error_message: Optional[str] = Field(description="错误信息", default=None)
    metadata: Dict[str, Any] = Field(description="元数据", default_factory=dict)
    timestamp: str = Field(description="执行时间戳")
    execution_duration: Optional[float] = Field(description="执行耗时(秒)", default=None)


# ==================== 步骤链式历史记录 ====================

class StepAction(BaseModel):
    """单个步骤动作记录"""
    action_type: StepActionType = Field(description="动作类型")
    action_id: str = Field(description="动作唯一ID")
    timestamp: str = Field(description="动作时间戳")
    
    # 动作相关数据
    step_definition: Optional[Dict[str, Any]] = Field(description="步骤定义（plan/replan时）", default=None)
    execution_result: Optional[Dict[str, Any]] = Field(description="执行结果（execute时）", default=None)
    validation_result: Optional[Dict[str, Any]] = Field(description="验证结果（validate时）", default=None)
    
    # 上下文信息
    reasoning: Optional[str] = Field(description="动作推理", default=None)
    triggered_by: Optional[str] = Field(description="触发原因", default=None)
    metadata: Dict[str, Any] = Field(description="额外元数据", default_factory=dict)


class StepChain(BaseModel):
    """单个步骤的完整执行链"""
    step_index: int = Field(description="步骤索引（在rough_plan中的位置）")
    step_id: str = Field(description="当前步骤ID（可能因replan而变化）")
    step_name: str = Field(description="步骤名称")
    
    # 动作历史链
    actions: List[StepAction] = Field(description="按时间顺序的动作历史", default_factory=list)
    
    # 当前状态
    current_status: StepChainStatus = Field(description="当前状态")
    retry_count: int = Field(description="重试次数", default=0)
    replan_count: int = Field(description="重新规划次数", default=0)
    
    # 快速访问当前版本
    current_step_definition: Optional[Dict[str, Any]] = Field(description="当前步骤定义", default=None)
    latest_execution: Optional[Dict[str, Any]] = Field(description="最新执行结果", default=None)
    latest_validation: Optional[Dict[str, Any]] = Field(description="最新验证结果", default=None)
    
    created_at: str = Field(description="创建时间")
    updated_at: str = Field(description="最后更新时间")
    
    def get_current_step_info(self) -> Dict[str, Any]:
        """获取当前步骤的完整信息（快速访问）"""
        return {
            "step_id": self.step_id,
            "step_name": self.step_name,
            "status": self.current_status.value,
            "definition": self.current_step_definition,
            "latest_execution": self.latest_execution,
            "latest_validation": self.latest_validation,
            "retry_count": self.retry_count,
            "replan_count": self.replan_count
        }
    
    def has_execution(self) -> bool:
        """检查是否有执行结果"""
        return self.latest_execution is not None
    
    def has_validation(self) -> bool:
        """检查是否有验证结果"""
        return self.latest_validation is not None
    
    def get_execution_status(self) -> Optional[str]:
        """获取最新执行状态"""
        if self.latest_execution:
            return self.latest_execution.get("status")
        return None


# ==================== 基础状态类 ====================
class FieldSchema(BaseModel):
    """通用字段定义，定义给大模型每一计划步骤的输入输出，用于生成具体的执行步骤"""
    name: str = Field(description="字段名称，请用英文名")
    type: str = Field(
        description="字段类型，支持基础类型和复合类型。基础类型: str, int, bool, float, list[str], list[int]等。复合类型: dict(必须通过properties定义结构), list[dict](必须通过items描述数组元素dict的结构)"
    )
    description: str = Field(description="字段描述")
    required: bool = Field(description="是否必需", default=True)

    # 复合类型支持
    items: Optional['FieldSchema'] = Field(
        description="数组元素结构定义 (仅当 type=list[dict] 时使用)。注意：此字段本身不代表单个字段，而是用来承载properties，描述数组中每个dict元素的完整结构",
        default=None
    )
    properties: Optional[Dict[str, 'FieldSchema']] = Field(
        description="对象属性定义。当type=dict时用于定义dict内部字段；当type=list[dict]时放在items字段中用于定义数组元素dict的内部字段结构",
        default=None
    )

    # 可选：辅助信息（提示模型，但不会强制）
    example: Optional[str] = Field(description="示例值", default=None)
    
    @model_validator(mode='after')
    def validate_complex_types(self):
        """验证复合类型的完整性"""
        # 验证 list[dict] 必须有 items 定义
        if self.type == "list[dict]":
            if not self.items:
                raise ValueError(f"字段 '{self.name}': type='list[dict]' 必须提供 items 字段来描述数组元素的结构")
            if not self.items.properties:
                raise ValueError(f"字段 '{self.name}': list[dict] 的 items 字段必须包含 properties 来定义数组中每个dict元素的内部字段结构")
        
        # 验证 dict 必须有 properties 定义
        if self.type == "dict":
            if not self.properties:
                raise ValueError(f"字段 '{self.name}': type='dict' 必须提供 properties 来定义dict的内部字段结构")
        
        # 不支持 dict[key,value] 形式，只支持带properties的dict
        if self.type.startswith("dict[") and self.type.endswith("]"):
            raise ValueError(f"字段 '{self.name}': 不支持 '{self.type}' 形式，请使用 'dict' 类型并在 properties 中定义内部结构")
        
        return self

# 前向引用支持
FieldSchema.model_rebuild()


class StepOutput(BaseModel):
    """步骤输出定义 - 使用通用字段系统"""
    description: str = Field(description="输出目标描述")
    fields: List[FieldSchema] = Field(description="输出字段定义列表")

class IntentAnalysis(BaseModel):
    """意图分析结果"""
    raw_analysis: str = Field(description="AI的详细理解分析")
    user_goal: str = Field(description="用户的主要目标")
    context_understanding: str = Field(description="对上下文的理解")
    complexity_level: str = Field(description="任务复杂度：simple/medium/complex")

    # 是否需要反问
    understanding_confidence: float = Field(description="理解置信度 0-1")
    clarification_needed: bool = Field(description="是否需要澄清")
    clarification_questions: List[str] = Field(description="需要澄清的问题")
    
    # 新增：工作流判断
    requires_workflow: bool = Field(description="是否需要使用工作流服务（完整绘本创建）")
    is_modification_request: bool = Field(description="是否是对现有内容的修改请求")
    workflow_confidence: float = Field(description="工作流需求置信度 0-1", default=0.0)


class ComprehensiveSummaryResult(BaseModel):
    """综合总结结果 - 包含任务总结和更新的上下文摘要"""
    task_summary: str = Field(description="任务总结，150-250字，包括主要目标、关键成果、重要步骤、生成资源等")
    updated_context_summary: str = Field(description="更新后的上下文摘要，300字左右，整合新任务成果并保留重要历史信息")


class RoughStep(BaseModel):
    """粗略步骤定义 - 第一级规划（不包含step_id，将在详细规划时生成）"""
    step_name: str = Field(description="步骤名称")
    description: str = Field(description="详细描述")
    dependencies: List[str] = Field(description="依赖的前置步骤", default_factory=list)
    key_considerations: List[str] = Field(description="关键考虑点", default_factory=list)


class RoughPlan(BaseModel):
    """粗略执行计划 - 第一级规划"""
    strategy: str = Field(description="总体执行策略")
    rough_steps: List[RoughStep] = Field(description="粗略步骤列表")
    required_capabilities: List[str] = Field(description="所需能力")
    tool_recommendations: List[str] = Field(description="推荐工具")
    risk_assessment: List[str] = Field(description="风险评估")


class DetailedStep(RoughStep):
    """详细步骤定义 - 继承RoughStep并添加详细信息"""
    step_id: str = Field(description="步骤唯一标识（在详细规划时生成）")
    step_output: StepOutput = Field(description="步骤输出定义")
    reasoning: str = Field(description="规划推理过程")
    adjustments: List[str] = Field(description="基于前面步骤的调整", default_factory=list)

class BaseStorybookState(TypedDict):
    """绘本服务基础状态 - 包含所有共同字段"""
    
    # ==== 核心标识字段 ====
    user_id: str                                     # 用户ID
    thread_id: str                                   # 线程ID
    conversation_id: int                             # 对话ID
    user_input: str                                  # 用户输入
    images: Optional[List[str]]                      # 图片列表
    
    # ==== 用户选项 ====
    user_option: Optional[UserOption]                # 用户选择的工具和配置
    
    # ==== LangGraph消息系统 ====
    messages: Annotated[List[BaseMessage], add_messages]  # 消息历史
    
    # ==== 共同执行状态 ====
    global_state_dict: Optional[Dict[str, Any]]     # 全局状态字典，维护整个执行过程的累积状态
    detailed_steps: Optional[List[DetailedStep]]    # 详细步骤列表
    current_step_index: Optional[int]               # 当前处理的步骤索引
    current_detailed_step: Optional[DetailedStep]    # 当前正在处理的详细步骤

    all_steps_complete: Optional[bool]              # 是否完成所有步骤
    
    # ==== 步骤输出追踪 ====
    step_outputs_mapping: Optional[Dict[str, Dict[str, Any]]]  # 字段名 -> 步骤输出定义的映射
    field_descriptions: Optional[Dict[str, str]]               # 字段名 -> 字段描述的映射
    output_model_schemas: Optional[Dict[int, Dict[str, Any]]]  # 步骤索引 -> 输出模型schema的映射
    
    # ==== 步骤链式历史记录 ====
    step_chains: Optional[List[Dict[str, Any]]]     # 步骤链列表（序列化后的StepChain）
    
    # ==== 重试控制 ====
    max_retries_per_step: Optional[int]             # 每个步骤最大重试次数
    max_replans_per_step: Optional[int]             # 每个步骤最大重新规划次数
    
    # ==== 系统状态 ====
    workflow_status: str                            # 工作流状态
    
    # ==== 任务历史管理字段 ====
    current_task_id: Optional[str]                   # 当前任务ID
    current_task_type: Optional[TaskType]            # 当前任务类型
    task_started_at: Optional[str]                   # 任务开始时间
    task_history: Optional[List[Dict[str, Any]]]     # 历史任务链（序列化的TaskHistory）
    
    # ==== 历史上下文系统 ====
    context_summary: Optional[str]                   # 所有历史任务的总结
    related_task_histories: Optional[List[Dict[str, Any]]]  # 与当前任务相关的历史任务
    related_task_reasons: Optional[List[str]]         # 选择相关任务的原因


# ==================== 任务历史记录 ====================

class TaskHistory(BaseStorybookState):
    """任务历史记录 - 强制继承BaseStorybookState + 历史相关字段"""
    
    # ==== 历史特有字段 ====
    task_id: str = Field(description="任务唯一标识")
    task_type: TaskType = Field(description="任务类型")
    task_status: TaskStatus = Field(description="最终状态")
    
    # 时间信息
    started_at: str = Field(description="开始时间")
    completed_at: Optional[str] = Field(description="结束时间", default=None)
    
    # 结果摘要
    completion_percentage: float = Field(description="完成度百分比", default=0.0)
    quality_score: Optional[float] = Field(description="质量评分", default=None)
    
    # 历史总结（AI生成的任务整体摘要）
    task_summary: Optional[str] = Field(description="AI生成的任务整体摘要，用于历史回顾", default=None)


# ==================== 历史任务工具函数 ====================

def create_task_history_from_state(state: "BaseStorybookState", task_id: str, 
                                   task_type: TaskType, task_status: TaskStatus,
                                   started_at: str, completion_percentage: float = 0.0) -> TaskHistory:
    """从当前状态创建任务历史记录"""
    from datetime import datetime
    
    return TaskHistory(
        # 继承BaseStorybookState的所有字段
        **state,
        task_id=task_id,
        task_type=task_type,
        task_status=task_status,
        started_at=started_at,
        completed_at=datetime.now().isoformat(),
        completion_percentage=completion_percentage
    )


# ==================== 状态工具函数 ====================

def get_global_context_info(state: "BaseStorybookState") -> Dict[str, Any]:
    """从基础状态提取全局上下文信息"""
    global_state_dict = state.get("global_state_dict", {})
    
    context_info = {
        "user_id": state.get("user_id", ""),
        "thread_id": state.get("thread_id", ""),
        "conversation_id": state.get("conversation_id", 0),
        "user_input": state.get("user_input", ""),
        "images": state.get("images", []),
        "current_step_index": state.get("current_step_index", 0),
        "all_steps_complete": state.get("all_steps_complete", False),
        **global_state_dict
    }
    
    return context_info


def update_execution_result(state: "BaseStorybookState", result: ExecutionResult) -> Dict[str, Any]:
    """更新执行结果到状态（通过步骤链）"""
    # 更新全局状态
    global_state_dict = state.get("global_state_dict", {})
    if result.structured_output:
        global_state_dict.update(result.structured_output)
    
    # 同步更新字段追踪信息
    field_tracking_update = {}
    if result.structured_output:
        # 获取当前步骤信息来更新字段追踪
        current_detailed_step = state.get("current_detailed_step")
        if current_detailed_step:
            tracking_update = update_step_output_tracking(state, current_detailed_step)
            field_tracking_update.update(tracking_update)
    
    # 通过步骤链管理执行结果
    current_step_index = state.get("current_step_index", 0)
    step_chains = state.get("step_chains", [])
    
    # 确定执行类型
    current_chain = get_current_step_chain(step_chains, current_step_index)
    if current_chain:
        # 检查是否有之前的执行动作
        execute_actions = [a for a in current_chain.actions if a.action_type == StepActionType.EXECUTE]
        action_type = StepActionType.REEXECUTE if len(execute_actions) > 0 else StepActionType.EXECUTE
    else:
        action_type = StepActionType.EXECUTE
    
    # 将ExecutionResult转换为字典
    if hasattr(result, 'model_dump'):
        execution_result_dict = result.model_dump()
    elif hasattr(result, 'dict'):
        execution_result_dict = result.dict()
    else:
        execution_result_dict = result
    
    # 添加执行动作到步骤链
    add_step_action(
        step_chains=step_chains,
        step_index=current_step_index,
        action_type=action_type,
        execution_result=execution_result_dict,
        reasoning=f"执行{'重新' if action_type == StepActionType.REEXECUTE else ''}完成: {result.status}"
    )
    
    return {
        "global_state_dict": global_state_dict,
        "step_chains": step_chains,
        **field_tracking_update  # 包含字段追踪更新
    }


def update_step_output_tracking(state: "BaseStorybookState", detailed_step: DetailedStep, 
                               output_model_schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """更新步骤输出追踪信息"""
    step_outputs_mapping = state.get("step_outputs_mapping", {})
    field_descriptions = state.get("field_descriptions", {})
    output_model_schemas = state.get("output_model_schemas", {})
    current_step_index = state.get("current_step_index", 0)
    
    # 更新字段映射和描述
    for field in detailed_step.step_output.fields:
        step_outputs_mapping[field.name] = {
            "step_id": detailed_step.step_id,
            "step_name": detailed_step.step_name,
            "step_index": current_step_index,
            "field_type": field.type,
            "required": field.required,
            "description": field.description,
            "example": field.example
        }
        field_descriptions[field.name] = field.description
    
    # 保存输出模型schema（如果提供）
    if output_model_schema:
        output_model_schemas[current_step_index] = output_model_schema
    
    return {
        "step_outputs_mapping": step_outputs_mapping,
        "field_descriptions": field_descriptions,
        "output_model_schemas": output_model_schemas
    }


def get_field_context_info(state: "BaseStorybookState", field_name: str) -> Optional[Dict[str, Any]]:
    """获取字段的上下文信息，包括来源步骤和描述"""
    step_outputs_mapping = state.get("step_outputs_mapping", {})
    return step_outputs_mapping.get(field_name)


# ==================== 步骤链管理工具函数 ====================

def get_step_chain(step_chains: List[StepChain], step_index: int) -> Optional[StepChain]:
    """获取指定步骤的步骤链"""
    for chain in step_chains:
        if chain.step_index == step_index:
            return chain
    return None


def create_step_chain(step_chains: List[StepChain], step_index: int, step_id: str, step_name: str) -> StepChain:
    """创建新的步骤链"""
    from datetime import datetime
    
    # 检查是否已存在
    existing_chain = get_step_chain(step_chains, step_index)
    if existing_chain:
        return existing_chain
    
    # 创建新的步骤链
    now = datetime.now().isoformat()
    new_chain = StepChain(
        step_index=step_index,
        step_id=step_id,
        step_name=step_name,
        current_status=StepChainStatus.PLANNING,
        created_at=now,
        updated_at=now,
        actions=[]
    )
    step_chains.append(new_chain)
    return new_chain


def get_or_create_step_chain(step_chains: List[StepChain], step_index: int) -> StepChain:
    """获取或创建指定步骤的步骤链（临时方法，为了兼容性）"""
    import uuid
    from datetime import datetime
    
    # 查找现有步骤链
    existing_chain = get_step_chain(step_chains, step_index)
    if existing_chain:
        return existing_chain
    
    # 创建临时步骤链
    return create_step_chain(step_chains, step_index, str(uuid.uuid4()), f"步骤_{step_index + 1}")


def update_step_chain(step_chains: List[StepChain], step_index: int, status: StepChainStatus) -> StepChain:
    """更新步骤链状态"""
    from datetime import datetime
    
    chain = get_or_create_step_chain(step_chains, step_index)
    chain.current_status = status
    chain.updated_at = datetime.now().isoformat()
    return chain


def add_step_action(step_chains: List[StepChain], step_index: int, action_type: StepActionType, 
                   result_data: Optional[Dict[str, Any]] = None, 
                   reasoning: Optional[str] = None,
                   execution_result: Optional[Dict[str, Any]] = None) -> StepChain:
    """向步骤链添加新的行动"""
    import uuid
    from datetime import datetime
    
    chain = get_or_create_step_chain(step_chains, step_index)
    
    action = StepAction(
        action_id=str(uuid.uuid4()),
        action_type=action_type,
        timestamp=datetime.now().isoformat(),
        result_data=result_data,
        reasoning=reasoning,
        execution_result=execution_result
    )
    
    chain.actions.append(action)
    chain.updated_at = datetime.now().isoformat()
    
    # 更新快速访问字段和计数
    if action_type in [StepActionType.PLAN, StepActionType.REPLAN]:
        # 规划动作：更新current_step_definition
        chain.current_step_definition = result_data
        if action_type == StepActionType.REPLAN:
            chain.replan_count += 1
    elif action_type in [StepActionType.EXECUTE, StepActionType.REEXECUTE]:
        # 执行动作：更新latest_execution
        chain.latest_execution = execution_result
        if action_type == StepActionType.REEXECUTE:
            chain.retry_count += 1
    elif action_type == StepActionType.VALIDATE:
        # 验证动作：更新latest_validation
        chain.latest_validation = result_data
    
    return chain


def get_step_chain_context(step_chains: List[StepChain], max_step_index: int) -> str:
    """获取指定步骤索引之前（包含）的所有步骤链上下文"""
    context_parts = []
    
    # 按步骤索引排序，获取所有小于等于max_step_index的步骤
    relevant_chains = [chain for chain in step_chains if chain.step_index <= max_step_index]
    relevant_chains.sort(key=lambda x: x.step_index)
    
    for chain in relevant_chains:
        context_parts.append(f"步骤 {chain.step_index + 1}: {chain.step_name}")
        context_parts.append(f"  当前状态: {chain.current_status.value}")
        
        # 显示最近几个动作
        if chain.actions:
            context_parts.append("  动作历史:")
            for action in chain.actions[-3:]:  # 只显示最近3个动作
                action_desc = f"    - {action.action_type.value} ({action.timestamp[:19]})"
                if action.reasoning:
                    action_desc += f": {action.reasoning}..."
                context_parts.append(action_desc)
        
        context_parts.append("")  # 空行分隔
    
    return "\n".join(context_parts)


def get_current_step_chain(step_chains: List[StepChain], step_index: int) -> Optional[StepChain]:
    """获取当前步骤的步骤链"""
    for chain in step_chains:
        if chain.step_index == step_index:
            return chain
    return None


def get_step_chain_counts(step_chains: List[StepChain], step_index: int) -> Dict[str, int]:
    """从步骤链中获取重试和重新规划计数"""
    chain = get_step_chain(step_chains, step_index)
    if chain:
        return {
            "retry_count": chain.retry_count,
            "replan_count": chain.replan_count
        }
    return {
        "retry_count": 0,
        "replan_count": 0
    }


def get_previous_execution_context(state: "BaseStorybookState") -> str:
    """获取之前执行步骤的上下文（使用步骤链）"""
    current_step_index = state.get("current_step_index", 0)
    step_chains = state.get("step_chains", [])
    
    # 使用步骤链上下文
    context = get_step_chain_context(step_chains, current_step_index - 1)
    
    if not context.strip():
        return "这是第一个步骤，没有前面的执行记录。"
    
    return f"前面步骤的执行历史：\n{context}"


# ==================== 通用展示系统 ====================

class DisplayFieldType(str, Enum):
    """展示字段类型枚举"""
    TEXT = "text"                    # 简单文本
    LONG_TEXT = "long_text"         # 长文本（需要折叠）
    IMAGE = "image"                 # 图片URL
    LIST = "list"                   # 简单列表
    OBJECT = "object"               # 复杂对象（需要展开）
    TABLE = "table"                 # 表格数据
    JSON = "json"                   # JSON格式展示
    MARKDOWN = "markdown"           # Markdown格式





class StepInfo(BaseModel):
    """步骤信息定义"""
    step_id: str = Field(description="步骤ID")
    step_name: str = Field(description="步骤名称")
    step_index: int = Field(description="步骤索引")
    status: str = Field(description="步骤状态")
    start_time: Optional[str] = Field(description="开始时间", default=None)
    end_time: Optional[str] = Field(description="结束时间", default=None)
    duration: Optional[float] = Field(description="执行时长（秒）", default=None)
    retry_count: int = Field(description="重试次数", default=0)
    error_message: Optional[str] = Field(description="错误信息", default=None)


class DisplayField(BaseModel):
    """单个展示字段"""
    key: str = Field(description="字段标识")
    label: str = Field(description="显示标签")
    value: Any = Field(description="字段值")
    field_type: DisplayFieldType = Field(description="字段类型")
    description: Optional[str] = Field(description="字段描述", default=None)
    is_important: bool = Field(description="是否重要字段", default=False)
    
    # 嵌套结构支持
    children: Optional[List['DisplayField']] = Field(description="子字段（用于对象和列表）", default=None)
    
    # 显示控制
    is_collapsible: bool = Field(description="是否可折叠", default=False)
    is_collapsed: bool = Field(description="默认是否折叠", default=False)
    max_preview_length: Optional[int] = Field(description="预览最大长度", default=None)


class DisplaySection(BaseModel):
    """展示区块"""
    section_id: str = Field(description="区块ID")
    title: str = Field(description="区块标题")
    description: Optional[str] = Field(description="区块描述", default=None)
    fields: List[DisplayField] = Field(description="区块内的字段列表")
    is_collapsible: bool = Field(description="区块是否可折叠", default=True)
    is_collapsed: bool = Field(description="区块默认是否折叠", default=False)
    priority: int = Field(description="显示优先级（数字越小越重要）", default=100)


class DisplayResult(BaseModel):
    """完整的展示结果"""
    result_id: str = Field(description="结果ID")
    title: str = Field(description="展示标题")
    summary: Optional[str] = Field(description="结果摘要", default=None)
    sections: List[DisplaySection] = Field(description="展示区块列表")
    timestamp: str = Field(description="生成时间")
    step_info: Optional[StepInfo] = Field(description="步骤信息（如果是中间结果）", default=None)


class DisplayAnalysisResult(BaseModel):
    """LLM分析用户界面展示需求的结果，用于指导如何将数据最佳地呈现给用户"""
    
    display_title: str = Field(
        ..., 
        description="为整个数据展示生成的简洁有吸引力的标题，应该反映主要内容或任务类型"
    )
    
    display_summary: str = Field(
        ..., 
        description="数据的简要摘要说明，帮助用户快速了解这次执行的主要结果和价值"
    )
    
    important_fields: List[str] = Field(
        ..., 
        description="最重要的字段名列表，这些字段包含用户最关心的核心结果，应该优先显示且不折叠"
    )
    
    resource_fields: List[str] = Field(
        ..., 
        description="包含资源内容的字段名列表，如图片URL、文件路径、音频链接等，需要特殊的展示方式"
    )
    
    summary_fields: List[str] = Field(
        ..., 
        description="适合用作概览的字段名列表，这些字段内容相对简短，适合在摘要区域展示"
    )
    
    hidden_fields: List[str] = Field(
        ..., 
        description="应该隐藏或默认折叠的字段名列表，通常是技术性字段、调试信息或不重要的元数据"
    )
    
    sections_config: List[Dict[str, Any]] = Field(
        ..., 
        description="区块划分配置列表，每个dict必须包含：section_id(字符串), title(字符串), description(字符串), field_keys(字符串列表)"
    )


# 前向引用支持
DisplayField.model_rebuild()
