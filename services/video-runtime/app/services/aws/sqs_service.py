"""
AWS SQS任务队列服务
替代Redis Stream的任务队列功能
"""
import logging
import json
import os
import asyncio
from typing import Optional, Dict, Any
import boto3
from botocore.exceptions import ClientError

from ...config import get_settings

logger = logging.getLogger(__name__)


class SQSTaskService:
    """SQS任务队列服务"""
    
    def __init__(self, queue_url: Optional[str] = None, environment: Optional[str] = None):
        """
        初始化SQS服务
        
        Args:
            queue_url: SQS队列URL（如果为None，从环境变量或配置获取）
            environment: 环境名称（local/dev/prod）
        """
        # 获取环境
        if environment is None:
            settings = get_settings()
            env = settings.ENVIRONMENT.value
        else:
            env = environment
        
        # 获取队列URL
        if queue_url:
            self.queue_url = queue_url
        else:
            # 从settings获取（根据ENVIRONMENT自动选择）
            settings = get_settings()
            sqs_queue_url = settings.SQS_QUEUE_URL
            
            if not sqs_queue_url:
                raise ValueError(
                    f"SQS queue URL not configured for environment: {env}. "
                    f"Please set SQS_QUEUE_URL in environment variables or .env file."
                )
            self.queue_url = sqs_queue_url
        
        # 获取AWS配置（使用IAM Role，无需凭证）
        settings = get_settings()
        aws_region = settings.AWS_REGION
        
        # 创建SQS客户端（默认使用IAM Role；配置 SQS_ENDPOINT_URL 时切到 SQS 兼容队列如 elasticmq）
        sqs_endpoint_url = getattr(settings, "SQS_ENDPOINT_URL", None)
        if sqs_endpoint_url:
            self.sqs_client = boto3.client(
                'sqs', region_name=aws_region, endpoint_url=sqs_endpoint_url
            )
        else:
            self.sqs_client = boto3.client('sqs', region_name=aws_region)
        
        logger.info(f"🔧 SQS Task Service初始化: 环境={env}, 队列URL={self.queue_url[:50]}...")
    
    async def add_task_to_queue(self, task_data: dict, delay_seconds: int = 0) -> str:
        """添加任务到队列（异步包装）。delay_seconds: 延迟可见时间 0–900 秒，用于 15s 自动 continue 等。
        
        Returns:
            message_id: SQS消息ID
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._add_task_to_queue_sync(task_data, delay_seconds),
        )
    
    def _add_task_to_queue_sync(self, task_data: dict, delay_seconds: int = 0) -> str:
        """添加任务到队列（同步方法，在executor中运行）
        
        Returns:
            message_id: SQS消息ID
        """
        try:
            # 序列化任务数据
            message_body = json.dumps(task_data, ensure_ascii=False, default=str)
            
            # 检查消息大小（SQS限制256KB）
            message_size = len(message_body.encode('utf-8'))
            if message_size > 256 * 1024:
                raise ValueError(
                    f"任务数据太大 ({message_size} bytes)，超过SQS限制256KB。"
                    f"考虑将大文件存储到S3，在消息中只存储S3 URL。"
                )
            
            send_kw = {
                "QueueUrl": self.queue_url,
                "MessageBody": message_body,
                "MessageAttributes": {
                    'run_id': {
                        'StringValue': task_data.get('run_id', ''),
                        'DataType': 'String'
                    },
                    'user_id': {
                        'StringValue': task_data.get('user_id', ''),
                        'DataType': 'String'
                    },
                    'agent_type': {
                        'StringValue': task_data.get('agent_type', 'video'),
                        'DataType': 'String'
                    }
                }
            }
            if delay_seconds and delay_seconds > 0:
                send_kw["DelaySeconds"] = min(900, delay_seconds)

            response = self.sqs_client.send_message(**send_kw)
            message_id = response['MessageId']
            logger.info(f"✅ 任务已添加到SQS队列: message_id={message_id}, run_id={task_data.get('run_id')}")
            return message_id
            
        except ClientError as e:
            logger.error(f"❌ 添加任务到SQS队列失败: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ 添加任务到SQS队列失败: {e}")
            raise
    
    async def consume_task(self, max_messages: int = 1, wait_time_seconds: int = 20) -> Optional[Dict[str, Any]]:
        """消费任务（长轮询，异步包装）
        
        Args:
            max_messages: 最多接收的消息数（1-10）
            wait_time_seconds: 长轮询等待时间（0-20秒）
        
        Returns:
            {
                "message_id": str,
                "receipt_handle": str,  # 用于删除消息
                "data": dict  # 任务数据
            }
        """
        # 在线程池中运行同步的boto3调用
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            self._consume_task_sync, 
            max_messages, 
            wait_time_seconds
        )
    
    def _consume_task_sync(self, max_messages: int, wait_time_seconds: int) -> Optional[Dict[str, Any]]:
        """消费任务（同步方法，在executor中运行）"""
        try:
            response = self.sqs_client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=min(max_messages, 10),
                WaitTimeSeconds=wait_time_seconds,  # 长轮询
                MessageAttributeNames=['All'],
                AttributeNames=['All']
            )
            
            messages = response.get('Messages', [])
            if not messages:
                return None
            
            # 返回第一条消息
            message = messages[0]
            message_id = message['MessageId']
            receipt_handle = message['ReceiptHandle']
            body = message['Body']
            
            # 解析任务数据
            try:
                task_data = json.loads(body)
            except json.JSONDecodeError as e:
                logger.error(f"❌ 解析任务JSON失败: {e}, body: {body[:100]}")
                # 删除无效消息（同步调用）
                self._delete_task_sync(receipt_handle)
                return None
            
            return {
                "message_id": message_id,
                "receipt_handle": receipt_handle,
                "data": task_data
            }
            
        except ClientError as e:
            logger.error(f"❌ 从SQS队列消费任务失败: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ 从SQS队列消费任务失败: {e}")
            raise
    
    async def extend_visibility_timeout(self, receipt_handle: str, visibility_timeout_seconds: int = 300):
        """
        延长消息的visibility timeout（用于长时间任务）
        
        功能：
        - 在任务执行过程中定期调用，延长消息的visibility timeout
        - 防止消息在任务执行期间重新变为可见
        
        参数：
        - receipt_handle: SQS消息的receipt_handle
        - visibility_timeout_seconds: 新的visibility timeout（秒），默认300秒（5分钟）
        
        注意：
        - 如果receipt_handle已失效（消息已被删除或过期），会抛出异常
        - 应该在异常处理中捕获，不影响任务执行
        """
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._extend_visibility_timeout_sync,
            receipt_handle,
            visibility_timeout_seconds
        )
    
    def _extend_visibility_timeout_sync(self, receipt_handle: str, visibility_timeout_seconds: int):
        """延长消息的visibility timeout（同步方法）"""
        try:
            self.sqs_client.change_message_visibility(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=visibility_timeout_seconds
            )
            logger.debug(f"✅ 已延长消息visibility timeout: {visibility_timeout_seconds}秒")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', '')
            error_msg = e.response.get('Error', {}).get('Message', '')
            if error_code == 'ReceiptHandleIsInvalid':
                # receipt_handle已失效（消息已被删除或过期），这是正常的
                logger.debug(f"⚠️ receipt_handle已失效，消息可能已被删除: {receipt_handle[:20]}...")
            elif error_code == 'InvalidParameterValue' and 'not available for visibility timeout change' in (error_msg or ''):
                # 消息已不存在或不可用（已删除/已过期），与 ReceiptHandleIsInvalid 等价，属正常
                logger.debug(f"⚠️ 消息已不存在或不可用，跳过延长 visibility: {receipt_handle[:20]}...")
            else:
                logger.warning(f"⚠️ 延长visibility timeout失败: {e}")
            # 不抛出异常，因为这是后台任务，不应该影响主任务
        except Exception as e:
            logger.warning(f"⚠️ 延长visibility timeout失败: {e}")
            # 不抛出异常，因为这是后台任务，不应该影响主任务
    
    async def delete_task(self, receipt_handle: str):
        """删除任务（确认完成，异步包装）"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._delete_task_sync, receipt_handle)
    
    def _delete_task_sync(self, receipt_handle: str):
        """删除任务（同步方法，在executor中运行）"""
        try:
            self.sqs_client.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle
            )
            logger.debug(f"✅ 任务已从SQS队列删除: receipt_handle={receipt_handle[:20]}...")
        except ClientError as e:
            logger.error(f"❌ 删除SQS任务失败: {e}")
            raise
    
    async def get_queue_attributes(self) -> Dict[str, Any]:
        """获取队列属性（用于监控，异步包装）"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_queue_attributes_sync)
    
    def _get_queue_attributes_sync(self) -> Dict[str, Any]:
        """获取队列属性（同步方法）"""
        try:
            response = self.sqs_client.get_queue_attributes(
                QueueUrl=self.queue_url,
                AttributeNames=['All']
            )
            return response['Attributes']
        except ClientError as e:
            logger.error(f"❌ 获取SQS队列属性失败: {e}")
            return {}
    
    async def get_queue_length(self) -> int:
        """获取队列中的消息数（近似值，异步包装）"""
        try:
            attributes = await self.get_queue_attributes()
            return int(attributes.get('ApproximateNumberOfMessages', 0))
        except Exception as e:
            logger.error(f"❌ 获取队列长度失败: {e}")
            return 0
