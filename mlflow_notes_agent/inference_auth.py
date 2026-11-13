"""Read projected service-account tokens for each inference HTTP request."""

from pathlib import Path

import httpx


class TokenFileAuth(httpx.Auth):
    """Follow Kubernetes token rotation without caching the token in the client."""

    def __init__(self, token_file: str):
        self.token_file = Path(token_file)

    def auth_flow(self, request: httpx.Request):
        token = self.token_file.read_text().strip()
        if not token:
            raise ValueError("Inference token file is empty")
        request.headers["Authorization"] = f"Bearer {token}"
        yield request
