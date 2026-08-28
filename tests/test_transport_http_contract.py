import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from zenmoney import config, transport
from zenmoney.errors import ApiRequestError, AuthenticationError


class _Response:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://api.zenmoney.ru/test")
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError(
            f"HTTP {self.status_code}",
            request=request,
            response=response,
        )

    def json(self) -> dict:
        return {}


class TransportHttpContractTests(unittest.TestCase):
    def _call(self, endpoint: str, status_code: int):
        client = AsyncMock()
        client.post.return_value = _Response(status_code)
        with patch.object(config, "TOKEN", "test-token"), \
             patch.object(transport, "_get_client", return_value=client):
            return asyncio.run(transport._api_post(endpoint, {}))

    def test_diff_401_remains_authentication_failure(self):
        with self.assertRaises(AuthenticationError) as caught:
            self._call("/v8/diff/", 401)

        self.assertEqual(caught.exception.code, "AUTHENTICATION_FAILED")
        self.assertEqual(caught.exception.endpoint, "/v8/diff/")
        self.assertEqual(caught.exception.status_code, 401)

    def test_any_401_is_authentication_failure(self):
        with self.assertRaises(AuthenticationError) as caught:
            self._call("/some/endpoint/", 401)

        self.assertEqual(caught.exception.code, "AUTHENTICATION_FAILED")
        self.assertEqual(caught.exception.endpoint, "/some/endpoint/")
        self.assertEqual(caught.exception.status_code, 401)

    def test_network_failure_preserves_endpoint_without_auth_reclassification(self):
        client = AsyncMock()
        request = httpx.Request("POST", "https://api.zenmoney.ru/v8/diff/")
        client.post.side_effect = httpx.ConnectError("network down", request=request)
        with patch.object(config, "TOKEN", "test-token"), \
             patch.object(transport, "_get_client", return_value=client), \
             self.assertRaises(ApiRequestError) as caught:
            asyncio.run(transport._api_post("/v8/diff/", {}))

        self.assertEqual(caught.exception.code, "API_REQUEST_FAILED")
        self.assertEqual(caught.exception.endpoint, "/v8/diff/")
        self.assertIsNone(caught.exception.status_code)


if __name__ == "__main__":
    unittest.main()
