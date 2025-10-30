"""
Real API Client - No Mocking
Makes real HTTP requests to actual running API with 307 redirect detection
"""

import os
import httpx
import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from .auth_helper import RealKeycloakAuth, AuthTokens


@dataclass
class RedirectInfo:
    """Information about a detected redirect"""
    method: str
    original_url: str
    redirect_url: str
    status_code: int
    timestamp: datetime
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class ApiResponse:
    """Real API response with redirect detection"""
    status_code: int
    data: Any
    headers: Dict[str, str]
    url: str
    redirects: List[RedirectInfo] = field(default_factory=list)
    execution_time: float = 0.0


class RealApiClient:
    """Real API client that makes actual HTTP requests to running backend"""

    def __init__(self):
        self.base_url = os.getenv("TEST_API_BASE_URL")
        self.timeout = float(os.getenv("TEST_TIMEOUT", "30"))
        self.max_retries = int(os.getenv("TEST_MAX_RETRIES", "3"))
        self.log_redirects = os.getenv("TEST_LOG_REDIRECTS", "true").lower() == "true"

        if not self.base_url:
            raise ValueError("TEST_API_BASE_URL not configured")

        self.logger = logging.getLogger(__name__)
        self.auth = RealKeycloakAuth()

        # Track all detected redirects for reporting
        self.detected_redirects: List[RedirectInfo] = []

    async def request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        auth_tokens: Optional[AuthTokens] = None,
        follow_redirects: bool = False
    ) -> ApiResponse:
        """Make real HTTP request to actual API with redirect detection"""

        # Ensure endpoint starts with /
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint

        url = f"{self.base_url}{endpoint}"

        # Prepare headers
        request_headers = headers or {}
        if auth_tokens:
            request_headers.update(self.auth.get_auth_headers(auth_tokens))

        self.logger.debug(f"Making {method} request to: {url}")

        start_time = datetime.now()
        redirects = []

        async with httpx.AsyncClient(
            verify=False,
            timeout=self.timeout,
            follow_redirects=follow_redirects
        ) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    json=data if data else None,
                    params=params,
                    headers=request_headers
                )

                execution_time = (datetime.now() - start_time).total_seconds()

                # Check for redirects (307, 301, 302, 303, 308)
                if response.status_code in [301, 302, 303, 307, 308]:
                    redirect_info = RedirectInfo(
                        method=method,
                        original_url=url,
                        redirect_url=response.headers.get("location", ""),
                        status_code=response.status_code,
                        timestamp=datetime.now(),
                        headers=dict(response.headers)
                    )
                    redirects.append(redirect_info)
                    self.detected_redirects.append(redirect_info)

                    if self.log_redirects:
                        self.logger.warning(
                            f"REDIRECT DETECTED: {method} {url} -> "
                            f"{response.status_code} -> {redirect_info.redirect_url}"
                        )

                # Parse response data
                try:
                    if response.headers.get("content-type", "").startswith("application/json"):
                        response_data = response.json()
                    else:
                        response_data = response.text
                except Exception:
                    response_data = response.content

                return ApiResponse(
                    status_code=response.status_code,
                    data=response_data,
                    headers=dict(response.headers),
                    url=str(response.url),
                    redirects=redirects,
                    execution_time=execution_time
                )

            except httpx.RequestError as e:
                self.logger.error(f"Request error for {method} {url}: {e}")
                raise Exception(f"Request failed: {e}")

    async def get(self, endpoint: str, params: Optional[Dict] = None, auth_tokens: Optional[AuthTokens] = None) -> ApiResponse:
        """Make GET request to real API"""
        return await self.request("GET", endpoint, params=params, auth_tokens=auth_tokens)

    async def post(self, endpoint: str, data: Optional[Dict] = None, auth_tokens: Optional[AuthTokens] = None) -> ApiResponse:
        """Make POST request to real API"""
        return await self.request("POST", endpoint, data=data, auth_tokens=auth_tokens)

    async def put(self, endpoint: str, data: Optional[Dict] = None, auth_tokens: Optional[AuthTokens] = None) -> ApiResponse:
        """Make PUT request to real API"""
        return await self.request("PUT", endpoint, data=data, auth_tokens=auth_tokens)

    async def delete(self, endpoint: str, auth_tokens: Optional[AuthTokens] = None) -> ApiResponse:
        """Make DELETE request to real API"""
        return await self.request("DELETE", endpoint, auth_tokens=auth_tokens)

    async def patch(self, endpoint: str, data: Optional[Dict] = None, auth_tokens: Optional[AuthTokens] = None) -> ApiResponse:
        """Make PATCH request to real API"""
        return await self.request("PATCH", endpoint, data=data, auth_tokens=auth_tokens)

    async def test_endpoint_for_redirects(self, endpoint: str, auth_tokens: Optional[AuthTokens] = None) -> List[RedirectInfo]:
        """Test specific endpoint for 307 redirects with different URL variations"""
        redirect_results = []

        # Test variations that commonly cause 307 redirects
        test_urls = [
            endpoint,  # Original endpoint
            endpoint + "/",  # With trailing slash
            endpoint.rstrip("/"),  # Without trailing slash
        ]

        # Remove duplicates while preserving order
        seen = set()
        unique_urls = []
        for url in test_urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)

        for test_url in unique_urls:
            try:
                response = await self.get(test_url, auth_tokens=auth_tokens)
                redirect_results.extend(response.redirects)
            except Exception as e:
                self.logger.debug(f"Error testing {test_url}: {e}")

        return redirect_results

    def get_all_detected_redirects(self) -> List[RedirectInfo]:
        """Get all redirects detected during testing session"""
        return self.detected_redirects

    def generate_redirect_report(self) -> str:
        """Generate a report of all detected 307 redirects"""
        if not self.detected_redirects:
            return "No redirects detected during testing."

        report = ["=== 307 REDIRECT DETECTION REPORT ===\n"]

        # Group by status code
        by_status = {}
        for redirect in self.detected_redirects:
            status = redirect.status_code
            if status not in by_status:
                by_status[status] = []
            by_status[status].append(redirect)

        for status_code, redirects in by_status.items():
            report.append(f"\n{status_code} Redirects ({len(redirects)} found):")
            report.append("-" * 50)

            for redirect in redirects:
                report.append(f"Method: {redirect.method}")
                report.append(f"Original: {redirect.original_url}")
                report.append(f"Redirect: {redirect.redirect_url}")
                report.append(f"Time: {redirect.timestamp}")
                report.append("")

        report.append(f"\nTotal redirects detected: {len(self.detected_redirects)}")
        return "\n".join(report)

    async def health_check(self) -> bool:
        """Check if the real API is accessible"""
        try:
            # Try to access a simple endpoint
            response = await self.get("/performance/health")
            return response.status_code == 200
        except Exception as e:
            self.logger.error(f"API health check failed: {e}")
            return False