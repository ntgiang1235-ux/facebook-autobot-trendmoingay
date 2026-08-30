from collections.abc import Callable

from app.job_contract import JobOutcome, skipped, success


_PREPUBLISH_SKIP_ID = "__prepublish_skipped__"


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
    on_published: Callable[[str, dict, dict], None] | None = None,
    before_publish: Callable[[str, dict], object] | None = None,
    on_published_intelligence: Callable[[str, dict, dict, object], None] | None = None,
) -> Callable[[], JobOutcome]:
    """Turn a legacy publish job's print-and-return behavior into an explicit outcome.

    Only failures of the primary Facebook publish are fatal. Optional follow-up
    operations such as seed comments remain best-effort after the primary post
    has succeeded. When supplied, ``before_publish`` can reject or rewrite the
    outbound primary request before any Facebook network call. ``on_published``
    receives only a real successful Facebook publish. The optional
    ``on_published_intelligence`` callback additionally receives the accepted
    pre-publish decision so the canonical ledger can persist quality metadata.

    Publication metadata is captured inside the Facebook wrapper but callbacks
    themselves run only after the legacy job returns. This boundary is
    intentional: some legacy image helpers catch exceptions from a publish call
    and retry as text. A Turso/ledger exception must never be mistaken for a
    Facebook upload exception and trigger a duplicate publish.

    A pre-publish rejection is represented to legacy code as a synthetic 200 so
    image helpers do not fall back to a second publish. Once rejected, follow-up
    Facebook/Gemini calls, success Telegram messages, and legacy ``save_posted``
    history writes are suppressed until the job returns; the adapter then emits
    an explicit skipped outcome.
    """

    def adapted() -> JobOutcome:
        original_fb = module.call_fb_api
        original_gemini = module.call_gemini
        original_delivery = getattr(module, "send_tele", None)
        original_save_posted = getattr(module, "save_posted", None)
        primary_attempted = False
        primary_success = False
        primary_failure = None
        published_metadata = None
        accepted_decision = None
        gemini_failed_before_publish = False
        prepublish_rejected = None

        def reject_prepublish(detail: str) -> tuple[int, dict]:
            nonlocal prepublish_rejected
            prepublish_rejected = detail or "pre-publish check rejected content"
            return 200, {"id": _PREPUBLISH_SKIP_ID}

        def tracked_fb(endpoint, data, files=None):
            nonlocal primary_attempted, primary_success, primary_failure
            nonlocal published_metadata, accepted_decision

            # Once the primary request has been rejected, suppress every legacy
            # follow-up call (seed comments, fallbacks, etc.) until the job exits.
            if prepublish_rejected is not None:
                return 200, {"id": _PREPUBLISH_SKIP_ID}

            request_data = dict(data) if isinstance(data, dict) else {}
            outbound_data = data
            is_primary = primary_predicate(endpoint)

            if is_primary:
                primary_attempted = True
                if before_publish is not None:
                    try:
                        decision = before_publish(endpoint, request_data)
                        publish = getattr(decision, "publish", None)
                        detail = str(getattr(decision, "detail", "")).strip()
                        if publish is not True:
                            if publish is not False:
                                detail = detail or "pre-publish check returned invalid decision"
                            return reject_prepublish(detail or "pre-publish check rejected content")
                        rewritten = getattr(decision, "request_data", None)
                        if not isinstance(rewritten, dict):
                            return reject_prepublish("pre-publish check returned invalid request data")
                        outbound_data = dict(rewritten)
                        request_data = dict(rewritten)
                        accepted_decision = decision
                    except Exception as error:
                        return reject_prepublish(f"pre-publish check failed: {error}")

            code, payload = original_fb(endpoint, outbound_data, files=files)
            if is_primary:
                if _facebook_publish_succeeded(code, payload):
                    primary_success = True
                    primary_failure = None
                    if published_metadata is None:
                        published_metadata = (
                            endpoint,
                            request_data,
                            dict(payload),
                        )
                elif not primary_success:
                    primary_failure = (endpoint, code, payload)
            return code, payload

        def tracked_gemini(prompt, timeout=30):
            nonlocal gemini_failed_before_publish
            if prepublish_rejected is not None:
                return None
            result = original_gemini(prompt, timeout=timeout)
            if not result and not primary_success:
                gemini_failed_before_publish = True
            return result

        def tracked_delivery(message):
            if prepublish_rejected is not None:
                return True
            return original_delivery(message)

        def tracked_save_posted(link, title):
            if prepublish_rejected is not None:
                return None
            return original_save_posted(link, title)

        module.call_fb_api = tracked_fb
        module.call_gemini = tracked_gemini
        if callable(original_delivery):
            module.send_tele = tracked_delivery
        if callable(original_save_posted):
            module.save_posted = tracked_save_posted
        try:
            job_fn()
        finally:
            module.call_fb_api = original_fb
            module.call_gemini = original_gemini
            if callable(original_delivery):
                module.send_tele = original_delivery
            if callable(original_save_posted):
                module.save_posted = original_save_posted

        if prepublish_rejected is not None:
            return skipped(prepublish_rejected)

        if gemini_failed_before_publish and not primary_success:
            raise RuntimeError("Gemini returned no content before primary publish")

        if primary_failure is not None and not primary_success:
            endpoint, code, payload = primary_failure
            raise RuntimeError(
                f"primary Facebook publish failed: endpoint={endpoint}, "
                f"HTTP {code}, response={str(payload)[:500]}"
            )

        if primary_success:
            if on_published is not None and published_metadata is not None:
                on_published(*published_metadata)
            if (
                on_published_intelligence is not None
                and published_metadata is not None
                and accepted_decision is not None
            ):
                on_published_intelligence(*published_metadata, accepted_decision)
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
