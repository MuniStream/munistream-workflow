"""
External API Operator for integrating with external services.
Generic implementation without business logic.
"""
from typing import Dict, Any, Optional, Callable
import aiohttp
import asyncio
import json
from datetime import datetime

from .base import BaseOperator, TaskResult


class ExternalAPIOperator(BaseOperator):
    """
    Generic operator for calling external APIs.
    Self-contained but uses context data passed from previous steps.
    Completely agnostic - doesn't know where data came from or what happens next.
    """
    
    def __init__(
        self,
        task_id: str,
        endpoint: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        context_to_payload: Optional[Dict[str, str]] = None,
        response_processor: Optional[Callable] = None,
        timeout: int = 30,
        retries: int = 3,
        **kwargs
    ):
        """
        Initialize external API operator.
        
        Args:
            task_id: Unique identifier
            endpoint: API endpoint URL (supports {placeholder} syntax)
            method: HTTP method
            headers: Request headers
            context_to_payload: Map context keys to API payload keys
            response_processor: Function to process API response
            timeout: Request timeout in seconds
            retries: Number of retries on failure
            **kwargs: Additional configuration
        """
        super().__init__(task_id, **kwargs)
        self.endpoint = endpoint
        self.method = method.upper()
        self.headers = headers or {}
        self.context_to_payload = context_to_payload or {}
        self.response_processor = response_processor
        self.timeout = timeout
        self.retries = retries
    
    def execute(self, context: Dict[str, Any]) -> TaskResult:
        """
        Call external API using data from context.
        
        Args:
            context: Execution context with data from previous steps
            
        Returns:
            TaskResult with API response
        """
        try:
            # Build the API request from context
            url = self.build_url(context)
            payload = self.build_payload(context)
            headers = self.build_headers(context)
            
            # Make the API call
            response = asyncio.run(
                self.make_api_call(url, payload, headers)
            )
            
            # Process response
            if response["success"]:
                # Apply custom response processor if provided
                processed_data = response["data"]
                if self.response_processor:
                    processed_data = self.response_processor(response["data"])
                
                return TaskResult(
                    status="continue",
                    data={
                        "api_response": processed_data,
                        "api_status_code": response["status_code"],
                        "api_endpoint": url,
                        "api_timestamp": datetime.utcnow().isoformat()
                    }
                )
            else:
                # API call failed
                if response.get("retryable", False):
                    return TaskResult(
                        status="retry",
                        error=f"API error (retryable): {response['error']}"
                    )
                else:
                    return TaskResult(
                        status="failed",
                        error=f"API error: {response['error']}"
                    )
                    
        except Exception as e:
            return TaskResult(
                status="failed",
                error=f"Error calling API: {str(e)}"
            )
    
    def build_url(self, context: Dict[str, Any]) -> str:
        """
        Build URL from template and context.
        Supports placeholder replacement: {key} or {nested.key}
        
        Args:
            context: Execution context
            
        Returns:
            Formatted URL
        """
        url = self.endpoint
        
        # Replace placeholders in URL with context values
        if "{" in url:
            import re
            placeholders = re.findall(r'\{([^}]+)\}', url)
            
            for placeholder in placeholders:
                value = self.get_from_context(context, placeholder)
                if value:
                    url = url.replace(f"{{{placeholder}}}", str(value))
        
        return url
    
    def build_payload(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Build API payload from context using mapping.
        
        Args:
            context: Execution context
            
        Returns:
            API payload or None for GET requests
        """
        if self.method == "GET":
            return None
        
        payload = {}
        
        # Map context values to payload
        for context_key, payload_key in self.context_to_payload.items():
            value = self.get_from_context(context, context_key)
            if value is not None:
                payload[payload_key] = value
        
        return payload if payload else None
    
    def build_headers(self, context: Dict[str, Any]) -> Dict[str, str]:
        """
        Build headers, potentially using context values.
        
        Args:
            context: Execution context
            
        Returns:
            Request headers
        """
        headers = self.headers.copy()
        
        # Add content-type for body requests
        if self.method in ["POST", "PUT", "PATCH"] and "Content-Type" not in headers:
            headers["Content-Type"] = "application/json"
        
        return headers
    
    def get_from_context(self, context: Dict[str, Any], key: str) -> Any:
        """
        Get value from context, supporting nested keys.
        
        Args:
            context: Execution context
            key: Key to retrieve (supports dot notation: "parent.child")
            
        Returns:
            Value from context or None
        """
        if "." in key:
            # Handle nested keys
            parts = key.split(".")
            value = context
            for part in parts:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return None
            return value
        else:
            # Direct key
            return context.get(key)
    
    async def make_api_call(
        self,
        url: str,
        payload: Optional[Dict[str, Any]],
        headers: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Make the actual API call with retries and error handling.
        
        Args:
            url: API endpoint
            payload: Request payload
            headers: Request headers
            
        Returns:
            Response dictionary with success/error info
        """
        attempt = 0
        last_error = None
        
        while attempt < self.retries:
            attempt += 1
            
            try:
                async with aiohttp.ClientSession() as session:
                    kwargs = {
                        "headers": headers,
                        "timeout": aiohttp.ClientTimeout(total=self.timeout)
                    }
                    
                    if payload is not None:
                        kwargs["json"] = payload
                    
                    async with session.request(self.method, url, **kwargs) as response:
                        response_data = await response.text()
                        
                        # Try to parse JSON
                        try:
                            response_json = json.loads(response_data)
                        except:
                            response_json = {"raw_response": response_data}
                        
                        if response.status >= 200 and response.status < 300:
                            return {
                                "success": True,
                                "status_code": response.status,
                                "data": response_json
                            }
                        else:
                            # Non-success status code
                            last_error = f"HTTP {response.status}: {response_data[:200]}"
                            
                            # Check if retryable (server errors, rate limits)
                            retryable = response.status >= 500 or response.status == 429
                            
                            if retryable and attempt < self.retries:
                                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                                continue
                            
                            return {
                                "success": False,
                                "status_code": response.status,
                                "error": last_error,
                                "retryable": retryable
                            }
                            
            except asyncio.TimeoutError:
                last_error = f"Timeout after {self.timeout} seconds"
                if attempt < self.retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
            except Exception as e:
                last_error = str(e)
                if attempt < self.retries:
                    await asyncio.sleep(2 ** attempt)
                    continue
        
        # All retries exhausted
        return {
            "success": False,
            "error": f"Failed after {self.retries} attempts: {last_error}",
            "retryable": True
        }


class HTTPOperator(ExternalAPIOperator):
    """
    Simplified HTTP operator with common configurations.
    """
    
    def __init__(
        self,
        task_id: str,
        url: str,
        method: str = "GET",
        json_data: Optional[Dict[str, Any]] = None,
        **kwargs
    ):
        """
        Initialize HTTP operator with simpler interface.
        
        Args:
            task_id: Unique identifier
            url: Full URL to call
            method: HTTP method
            json_data: Static JSON data to send
            **kwargs: Additional configuration
        """
        super().__init__(
            task_id=task_id,
            endpoint=url,
            method=method,
            **kwargs
        )
        self.json_data = json_data
    
    def build_payload(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Override to use static JSON data if provided"""
        if self.json_data:
            return self.json_data
        return super().build_payload(context)