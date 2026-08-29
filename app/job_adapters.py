from collections.abc import Callable

from app.job_contract import JobOutcome, skipped, success


def _facebook_publish_succeeded(code: int, payload: object) -> bool:
    if code != 200:
        return False
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("id") or payload.get("post_id"))


def adapt_publish_job(
    job_fn: Callable[[], object],
    module: object,
    primary_predicate: Callable[[str], bool],
    *,
    allow_skip: bool = False,
) -> Callable[[], JobOutcome]:
    """Turn a legacy publish job's print-and-return behavior into an explicit outcome.

    Only failures of the primary Facebook publish are fatal. Optional follow-up
    operations such as seed comments remain best-effort after the primary post
    has succeeded.
    """

    def adapted() -> JobOutcome:
        original_fb = module.call_fb_api
        original_gemini = module.call_gemini
        primary_attempted = False
        primary_success = False
        primary_failure = None
        gemini_failed_before_publish = False

        def tracked_fb(endpoint, data, files=None):
            nonlocal primary_attempted, primary_success, primary_failure
            code, payload = original_fb(endpoint, data, files=files)
            if primary_predicate(endpoint):
                primary_attempted = True
                if _facebook_publish_succeeded(code, payload):
                    primary_success = True
                    primary_failure = None
                else:
                    primary_failure = (endpoint, code, payload)
            return code, payload

        def tracked_gemini(prompt, timeout=30):
            nonlocal gemini_failed_before_publish
            result = original_gemini(prompt, timeout=timeout)
            if not result and not primary_success:
                gemini_failed_before_publish = True
            return result

        module.call_fb_api = tracked_fb
        module.call_gemini = tracked_gemini
        try:
            job_fn()
        finally:
            module.call_fb_api = original_fb
            module.call_gemini = original_gemini

        if gemini_failed_before_publish and not primary_success:
            raise RuntimeError("Gemini returned no content before primary publish")

        if primary_failure is not None and not primary_success:
            endpoint, code, payload = primary_failure
            raise RuntimeError(
                f"primary Facebook publish failed: endpoint={endpoint}, "
                f"HTTP {code}, response={str(payload)[:500]}"
            )

        if primary_success:
            return success()

        if allow_skip and not primary_attempted:
            return skipped("no primary publish was needed")

        raise RuntimeError("job completed without a primary publish")

    return adapted


def adapt_reply_job(job_fn: Callable[[], object], module: object) -> Callable[[], JobOutcome]:
    """Make Facebook/Gemini failures in the reply job visible to GitHub Actions."""

    def adapted() -> JobOutcome:
        original_get = module.get_fb_api
        original_post = module.call_fb_api
        original_gemini = module.call_gemini
        api_failure = None
        gemini_failure = False

        def tracked_get(endpoint, params=None):
            nonlocal api_failure
            code, payload = original_get(endpoint, params=params)
            if code != 200:
                api_failure = ("GET", endpoint, code, payload)
            return code, payload

        def tracked_post(endpoint, data, files=None):
            nonlocal api_failure
            code, payload = original_post(endpoint, data, files=files)
            if code != 200:
                api_failure = ("POST", endpoint, code, payload)
            return code, payload

        def tracked_gemini(prompt, timeout=30):
            nonlocal gemini_failure
            result = original_gemini(prompt, timeout=timeout)
            if not result:
                gemini_failure = True
            return result

        module.get_fb_api = tracked_get
        module.call_fb_api = tracked_post
        module.call_gemini = tracked_gemini
        try:
            job_fn()
        finally:
            module.get_fb_api = original_get
            module.call_fb_api = original_post
            module.call_gemini = original_gemini

        if api_failure is not None:
            method, endpoint, code, payload = api_failure
            raise RuntimeError(
                f"Facebook reply API failed: {method} {endpoint}, "
                f"HTTP {code}, response={str(payload)[:500]}"
            )
        if gemini_failure:
            raise RuntimeError("Gemini returned no content while generating a reply")
        return success()

    return adapted


def adapt_delivery_job(
    job_fn: Callable[[], object],
    module: object,
    *,
    allow_skip: bool = False,
) -> Callable[[], JobOutcome]:
    """Make generate-and-deliver legacy jobs observable without rewriting them."""

    def adapted() -> JobOutcome:
        original_gemini = module.call_gemini
        original_delivery = module.send_tele
        gemini_failure = False
        delivery_attempted = False
        delivery_success = False

        def tracked_gemini(prompt, timeout=30):
            nonlocal gemini_failure
            result = original_gemini(prompt, timeout=timeout)
            if not result:
                gemini_failure = True
            return result

        def tracked_delivery(message):
            nonlocal delivery_attempted, delivery_success
            delivery_attempted = True
            result = original_delivery(message)
            delivery_success = result is True
            return result

        module.call_gemini = tracked_gemini
        module.send_tele = tracked_delivery
        try:
            job_fn()
        finally:
            module.call_gemini = original_gemini
            module.send_tele = original_delivery

        if gemini_failure and not delivery_success:
            raise RuntimeError("Gemini returned no content before delivery")
        if delivery_attempted and not delivery_success:
            raise RuntimeError("Telegram delivery failed")
        if delivery_success:
            return success()
        if allow_skip:
            return skipped("no delivery was needed")
        raise RuntimeError("job completed without a delivery")

    return adapted
