
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

import httpx

from .errors import OpenStackError

logger = logging.getLogger(__name__)


class OpenStackClient:

    def __init__(
        self,
        auth_token: str,
        compute_url: str,
        network_url: str,
        image_url: str = "",
        volume_url: str = "",
        timeout: float = 30.0,
    ):
        self.auth_token = auth_token
        self.compute_url = compute_url.rstrip("/")
        self.network_url = network_url.rstrip("/")
        self.image_url = image_url.rstrip("/") if image_url else ""
        self.volume_url = volume_url.rstrip("/") if volume_url else ""
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(self,method: str,url: str,*,json: Any = None,headers: Optional[dict] = None,max_retries: int = 3,) -> httpx.Response:
        client = await self._get_client()

        _headers = {"X-Auth-Token": self.auth_token}
        if headers:
            _headers.update(headers)
        if json is not None:
            _headers.setdefault("Content-Type", "application/json")

        last_response: Optional[httpx.Response] = None

        for attempt in range(max_retries):
            try:
                response = await client.request(
                    method, url, headers=_headers, json=json
                )
                last_response = response

                if response.status_code < 500:
                    return response

                # 5xx — retry with backoff
                logger.warning(
                    "OpenStack %s %s → %s (attempt %d/%d), retrying…",
                    method, url, response.status_code, attempt + 1, max_retries,
                )
            except httpx.TimeoutException:
                logger.warning(
                    "OpenStack %s %s timed out (attempt %d/%d), retrying…",
                    method, url, attempt + 1, max_retries,
                )
            except httpx.NetworkError as exc:
                logger.warning(
                    "OpenStack %s %s network error (attempt %d/%d): %s",
                    method, url, attempt + 1, max_retries, exc,
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

        status = last_response.status_code if last_response else 0
        body = None
        try:
            body = last_response.json() if last_response else None
        except Exception:
            body = last_response.text if last_response else None

        raise OpenStackError(status, f"OpenStack API call failed after {max_retries} attempts", body)


    async def compute_request(
        self, method: str, path: str, **kwargs
    ) -> httpx.Response:
        url = f"{self.compute_url}/{path.lstrip('/')}"
        return await self._request(method, url, **kwargs)

    async def network_request(
        self, method: str, path: str, **kwargs
    ) -> httpx.Response:
        url = f"{self.network_url}/{path.lstrip('/')}"
        return await self._request(method, url, **kwargs)

    async def image_request(
        self, method: str, path: str, **kwargs
    ) -> httpx.Response:
        url = f"{self.image_url}/{path.lstrip('/')}"
        return await self._request(method, url, **kwargs)

    async def volume_request(
        self, method: str, path: str, **kwargs
    ) -> httpx.Response:
        url = f"{self.volume_url}/{path.lstrip('/')}"
        return await self._request(method, url, **kwargs)
