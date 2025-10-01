"""
Self-contained operators for workflow execution.
Each operator is agnostic to other steps and uses context from previous executions.
"""

from .base import BaseOperator, TaskResult, TaskState, TaskStatus
from .python import PythonOperator, ShortCircuitOperator
from .user_input import UserInputOperator
from .approval import ApprovalOperator, ConditionalApprovalOperator
from .external_api import ExternalAPIOperator, HTTPOperator
from .airflow_operator import AirflowOperator
from .s3_upload import S3UploadOperator
from .entity_operators import (
    EntityCreationOperator,
    EntityValidationOperator,
    EntityRequirementOperator,
    EntityRelationshipOperator
)
from .document_operators import DocumentCreationOperator, DocumentRequirementOperator

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
    "HTTPOperator",
    "AirflowOperator",

    # Storage operators
    "S3UploadOperator",

    # Entity operators
    "EntityCreationOperator",
    "EntityValidationOperator",
    "EntityRequirementOperator",
    "EntityRelationshipOperator",

    # Document operators
    "DocumentCreationOperator",
    "DocumentRequirementOperator"
]