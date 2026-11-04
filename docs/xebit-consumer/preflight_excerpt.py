# COPIED FROM xebit-api api/services/preflight.py (47f5189), lines 666-747 and 1162-1168: the pre-launch health check against GET /bot/.
# Reference only: this is the code that READS what our service sends. Do not edit; do not import.

    async def _check_recall(self) -> CheckResult:
        """Recall.ai key validity + concurrent-bot headroom, in one request.

        Recall exposes no credit-balance endpoint, so "workspace credits" is not something we can
        read — the honest substitutes are (a) the API answering an authenticated request and
        (b) how many bots the workspace has in a call right now, which is the limit customers
        actually hit.

        The filter is load-bearing, not a nicety: a plain ``GET /bot/`` returns the FULL bot
        history — measured at 795 KB / 7.9s on this workspace (``limit`` is not a parameter Recall
        honours here) — which blew the check's own timeout. ``status=in_call_recording`` answers
        in ~0.5s with just the live bots.
        """
        from api.services.recall import RECALL_BASE

        key = (settings.recall_api_key or "").strip()
        if not key:
            return CheckResult(
                name="recall",
                status=CheckStatus.CRITICAL,
                error="XEBIT_RECALL_API_KEY is not set",
                mitigation="Set XEBIT_RECALL_API_KEY, or launch on the LiveKit transport instead.",
            )

        assert self._client is not None
        # Headers are built here (not imported from recall.py) because that module snapshots
        # the key at import time — a rotated key would otherwise be invisible to this check.
        resp = await self._client.get(
            f"{RECALL_BASE}/bot/",
            headers={"Authorization": f"Token {key}", "Accept": "application/json"},
            params={"status": "in_call_recording"},
        )
        detail: dict[str, Any] = {"http_status": resp.status_code, "region": settings.recall_region}
        detail.update(_ratelimit_headers(resp.headers))
        active = _result_count(resp)
        if active is not None:
            detail["in_call_bots"] = active
            detail["max_concurrent_bots"] = settings.recall_max_concurrent_bots or None

        if resp.status_code in (401, 403):
            return CheckResult(
                name="recall",
                status=CheckStatus.CRITICAL,
                detail=detail,
                error=f"Recall.ai rejected the API key ({resp.status_code})",
                mitigation="Rotate XEBIT_RECALL_API_KEY / confirm XEBIT_RECALL_REGION matches the workspace.",
            )
        if resp.status_code == 429:
            return CheckResult(
                name="recall",
                status=CheckStatus.WARNING,
                detail=detail,
                error="Recall.ai is rate limiting this workspace (429)",
                mitigation="Stagger launches; Recall throttles bot creation per workspace.",
            )
        if resp.status_code >= 400:
            return CheckResult(
                name="recall",
                status=CheckStatus.WARNING,
                detail=detail,
                error=f"Unexpected Recall.ai response ({resp.status_code})",
                mitigation="Check status.recall.ai before launching.",
            )

        cap = settings.recall_max_concurrent_bots
        if cap and active is not None and active >= cap:
            return CheckResult(
                name="recall",
                status=CheckStatus.CRITICAL,
                detail=detail,
                error=f"All {cap} concurrent bot slots are in use ({active} active)",
                mitigation="Wait for a meeting to end or raise XEBIT_RECALL_MAX_CONCURRENT_BOTS / the Recall plan.",
            )
        if cap and active is not None and active >= cap - 1:
            return CheckResult(
                name="recall",
                status=CheckStatus.WARNING,
                detail=detail,
                error=f"Last bot slot ({active}/{cap} in use)",
                mitigation="Concurrent launches will fail until a meeting ends.",
            )
        return CheckResult(name="recall", status=CheckStatus.HEALTHY, detail=detail)

def _result_count(resp: httpx.Response) -> int | None:
    """Length of a paginated ``results`` array. None when the body isn't that shape."""
    try:
        results = resp.json().get("results")
    except Exception:
        return None
    return len(results) if isinstance(results, list) else None
