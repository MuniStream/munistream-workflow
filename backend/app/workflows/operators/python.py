"""
Python Operator for executing Python functions.
"""
from typing import Dict, Any, Callable, Optional
import asyncio
import inspect

from .base import BaseOperator, TaskResult


class PythonOperator(BaseOperator):
    """
    Executes a Python function - completely self-contained.
    The operator doesn't know about other steps in the workflow.
    """
    
    def __init__(
        self, 
        task_id: str, 
        python_callable: Callable,
        op_args: Optional[list] = None,
        op_kwargs: Optional[dict] = None,
        **kwargs
    ):
        """
        Initialize Python operator.
        
        Args:
            task_id: Unique identifier for this task
            python_callable: Python function to execute
            op_args: Positional arguments for the callable
            op_kwargs: Keyword arguments for the callable
            **kwargs: Additional configuration
        """
        super().__init__(task_id, **kwargs)
        self.python_callable = python_callable
        self.op_args = op_args or []
        self.op_kwargs = op_kwargs or {}
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Execute the Python function.
        
        Args:
            context: Execution context with data from previous steps
            
        Returns:
            TaskResult with function output
        """
        try:
            # Prepare arguments
            # The callable can accept context as a parameter
            sig = inspect.signature(self.python_callable)
            
            # Check if function accepts context
            accepts_context = 'context' in sig.parameters
            
            # Execute the function
            if asyncio.iscoroutinefunction(self.python_callable):
                # Handle async functions
                if accepts_context:
                    result = asyncio.run(
                        self.python_callable(*self.op_args, context=context, **self.op_kwargs)
                    )
                else:
                    result = asyncio.run(
                        self.python_callable(*self.op_args, **self.op_kwargs)
                    )
            else:
                # Handle sync functions
                if accepts_context:
                    result = self.python_callable(*self.op_args, context=context, **self.op_kwargs)
                else:
                    result = self.python_callable(*self.op_args, **self.op_kwargs)
            
            # Convert result to dict if needed
            if result is None:
                output_data = {}
            elif isinstance(result, dict):
                output_data = result
            else:
                output_data = {"result": result}
            
            return TaskResult(
                status="continue",
                data=output_data
            )
            
        except Exception as e:
            return TaskResult(
                status="failed",
                error=str(e)
            )


class ShortCircuitOperator(PythonOperator):
    """
    Python operator that can short-circuit the workflow.
    If the callable returns False, downstream tasks are skipped.
    """
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Execute and potentially short-circuit.
        
        Args:
            context: Execution context
            
        Returns:
            TaskResult with skip status if callable returns False
        """
        try:
            # Execute the function
            sig = inspect.signature(self.python_callable)
            accepts_context = 'context' in sig.parameters
            
            if asyncio.iscoroutinefunction(self.python_callable):
                if accepts_context:
                    result = asyncio.run(
                        self.python_callable(*self.op_args, context=context, **self.op_kwargs)
                    )
                else:
                    result = asyncio.run(
                        self.python_callable(*self.op_args, **self.op_kwargs)
                    )
            else:
                if accepts_context:
                    result = self.python_callable(*self.op_args, context=context, **self.op_kwargs)
                else:
                    result = self.python_callable(*self.op_args, **self.op_kwargs)
            
            # Check if we should short-circuit
            if result is False:
                return TaskResult(
                    status="skip",
                    data={"short_circuited": True}
                )
            
            # Continue normally
            output_data = {"result": result} if not isinstance(result, dict) else result
            
            return TaskResult(
                status="continue",
                data=output_data
            )
            
        except Exception as e:
            return TaskResult(
                status="failed",
                error=str(e)
            )