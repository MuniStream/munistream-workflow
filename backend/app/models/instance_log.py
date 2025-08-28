"""
Instance-specific logging model for workflow debugging.
"""
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum
from pydantic import Field
from beanie import Document, Indexed


class LogLevel(str, Enum):
    """Log severity levels"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class LogType(str, Enum):
    """Types of log entries"""
    TASK_START = "task_start"
    TASK_COMPLETE = "task_complete"
    TASK_FAILED = "task_failed"
    TASK_WAITING = "task_waiting"
    STATUS_CHANGE = "status_change"
    DATA_UPDATE = "data_update"
    ERROR = "error"
    USER_ACTION = "user_action"
    SYSTEM = "system"


class InstanceLog(Document):
    """
    Individual log entry for a workflow instance.
    Provides complete audit trail and debugging information.
    """
    
    # Instance reference
    instance_id: Indexed(str)
    workflow_id: str
    
    # Log details
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    level: LogLevel
    log_type: LogType
    
    # Content
    task_id: Optional[str] = None
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    
    # Error information
    error_message: Optional[str] = None
    error_traceback: Optional[str] = None
    
    # Context
    user_id: Optional[str] = None
    context_snapshot: Optional[Dict[str, Any]] = None  # Optional context state at time of log
    
    class Settings:
        name = "instance_logs"
        indexes = [
            "instance_id",
            "timestamp",
            "level",
            "log_type",
            [("instance_id", 1), ("timestamp", -1)]  # Compound index for efficient queries
        ]
    
    @classmethod
    async def log(
        cls,
        instance_id: str,
        workflow_id: str,
        level: LogLevel,
        log_type: LogType,
        message: str,
        task_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
        user_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        """
        Create and save a log entry.
        Convenience method for logging.
        """
        import traceback
        
        log_entry = cls(
            instance_id=instance_id,
            workflow_id=workflow_id,
            level=level,
            log_type=log_type,
            message=message,
            task_id=task_id,
            details=details or {},
            user_id=user_id
        )
        
        # Add error details if provided
        if error:
            log_entry.error_message = str(error)
            log_entry.error_traceback = traceback.format_exc()
        
        # Add context snapshot if provided (limit size)
        if context:
            # Only store essential context to avoid huge logs
            log_entry.context_snapshot = {
                k: str(v)[:500] if isinstance(v, (str, bytes)) else v
                for k, v in list(context.items())[:20]  # Limit to 20 keys
            }
        
        await log_entry.create()
        return log_entry
    
    @classmethod
    async def get_instance_logs(
        cls,
        instance_id: str,
        level: Optional[LogLevel] = None,
        log_type: Optional[LogType] = None,
        limit: int = 100
    ):
        """Get logs for a specific instance"""
        query = {"instance_id": instance_id}
        
        if level:
            query["level"] = level
        if log_type:
            query["log_type"] = log_type
        
        return await cls.find(query).sort(cls.timestamp).limit(limit).to_list()
    
    def to_display_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for display"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "type": self.log_type,
            "task": self.task_id,
            "message": self.message,
            "details": self.details,
            "error": self.error_message
        }