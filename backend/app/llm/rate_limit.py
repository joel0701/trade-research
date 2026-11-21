"""Rate limits (HTTP 429) from cloud model vendors.

Per-minute limits clear on their own: wait (the vendor usually says how long) and retry, telling
the UI. Daily or billing quotas don't clear by waiting: stop and say so plainly.
"""
import re
from typing import Any

class RateLimited(RuntimeError):
    """A rate limit that waiting and retrying didn't fix; the message says what to do."""


MAX_WAIT = 65      # seconds per wait; a longer suggested wait means waiting won't help soon
MAX_RETRIES = 3


def is_rate_limit(e: Exception) -> bool:
    return getattr(e, "status_code", None) == 429 or type(e).__name__ == "RateLimitError"


def _body(e: Exception) -> str:
    body: Any = getattr(e, "body", None)
    return f"{e} {body}" if body else str(e)


def retry_after(e: Exception) -> float | None:
    """Seconds to wait before retrying, if the vendor said."""
    resp = getattr(e, "response", None)
    header = resp.headers.get("retry-after") if resp is not None and hasattr(resp, "headers") else None
    try:
        if header:
            return float(header)
    except ValueError:
        pass
    m = re.search(r"retry(?:Delay| in)['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)\s*s", _body(e), re.I)
    return float(m.group(1)) if m else None


def is_daily_or_billing(e: Exception) -> bool:
    text = _body(e).lower()
    return any(k in text for k in ("perday", "per day", "per_day", "daily", "billing", "insufficient_quota",
                                   "free_tier", "free tier", "limit: 0"))


def explain(e: Exception, provider: str, model_id: str) -> str:
    """A plain message for a rate limit that retries didn't fix."""
    if is_daily_or_billing(e):
        why = (f"{provider} says the quota for {model_id} is used up (a daily limit, a free-tier limit of zero "
               "for this model, or billing not set up). Waiting a minute won't help.")
    else:
        why = f"{provider} kept rate-limiting {model_id} (too many requests or tokens per minute), even after retries."
    return (f"{why} Options: use another model for this question (model picker), try again later, or raise your "
            f"limits in the {provider} console (new models often start with low limits). Details: {_body(e)[:400]}")
