"""
Webhook security middleware and token verification utilities.

Ensures strict validation of the `X-Telegram-Bot-Api-Secret-Token` header using
constant-time string comparison to prevent timing side-channel attacks.
"""
from __future__ import annotations

import hmac
import logging
from typing import Awaitable, Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


def verify_telegram_secret_token(
    received_token: str | None, expected_token: str
) -> bool:
    """
    Validate incoming Telegram secret token header against expected secret.

    Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks.

    Parameters
    ----------
    received_token : str | None
        Value of 'X-Telegram-Bot-Api-Secret-Token' header from incoming request.
    expected_token : str
        Secret token configured during set_webhook call.

    Returns
    -------
    bool
        True if tokens match identically, False otherwise.
    """
    if not received_token or not expected_token:
        return False
    return hmac.compare_digest(received_token, expected_token)


class WebhookSecurityMiddleware(BaseHTTPMiddleware):
    """
    FastAPI / Starlette middleware enforcing Telegram webhook token security.

    Intercepts requests directed to the webhook path and verifies the secret token header.
    Non-webhook paths (e.g., /health) are bypassed.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[Response]],
        webhook_path: str,
        secret_token: str,
    ) -> None:
        super().__init__(app)
        self._webhook_path = webhook_path
        self._secret_token = secret_token

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process incoming HTTP request and validate secret token if target is webhook."""
        if request.url.path == self._webhook_path and request.method == "POST":
            header_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
            if not verify_telegram_secret_token(header_token, self._secret_token):
                client_ip = request.client.host if request.client else "unknown"
                logger.warning(
                    "Rejected unauthorized webhook request from %s (invalid or missing secret token).",
                    client_ip,
                )
                raise HTTPException(
                    status_code=403,
                    detail="Forbidden: invalid X-Telegram-Bot-Api-Secret-Token",
                )

        return await call_next(request)
