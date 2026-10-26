"""Production security configuration (environment-driven).

No secrets are hardcoded. Defaults are safe for local development;
a server deployment sets the variables listed in docs/ARCHITECTURE.md
(Security model) and deploy/ducklab.env.template.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityConfig:
    # Mark the session cookie Secure (HTTPS only). REQUIRED in production.
    secure_cookies: bool = False
    # Honor X-Forwarded-For from private/loopback proxies (rate limiting,
    # logging). Only enable behind a trusted reverse proxy.
    trust_proxy: bool = False
    # Extra Origin values allowed to call the session endpoint when they
    # differ from the backend Host — needed in dev where Vite serves the
    # frontend on :3000 and the backend on :8000. Empty in production.
    allowed_frontend_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "SecurityConfig":
        raw = os.environ.get("DUCKLAB_FRONTEND_ORIGINS", "")
        origins = tuple(o.strip().rstrip("/") for o in raw.split(",")
                        if o.strip())
        return cls(
            secure_cookies=os.environ.get("DUCKLAB_SECURE_COOKIES", "") == "1",
            trust_proxy=os.environ.get("DUCKLAB_TRUST_PROXY", "") == "1",
            allowed_frontend_origins=origins,
        )
