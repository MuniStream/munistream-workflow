"""
Self-contained operators for workflow execution.
Each operator is agnostic to other steps and uses context from previous executions.
"""

from .base import BaseOperator, TaskResult, TaskState, TaskStatus
from .python import PythonOperator, ShortCircuitOperator
from .user_input import UserInputOperator
from .approval import ApprovalOperator, ConditionalApprovalOperator
from .external_api import ExternalAPIOperator, HTTPOperator

__all__ = [
    # Base classes
    "BaseOperator",
    "TaskResult", 
    "TaskState",
    "TaskStatus",
    
    # Python operators
    "PythonOperator",
    "ShortCircuitOperator", 
    
    # User interaction operators
    "UserInputOperator",
    
    # Approval operators
    "ApprovalOperator",
    "ConditionalApprovalOperator",
    
    # External integration operators
    "ExternalAPIOperator",
    "HTTPOperator"
]