"""
Verifies that transient Gemini failures no longer kill an auto-reply.

Regression context, two rounds of the same bug:
  1. Google returned 503 UNAVAILABLE and _call_gemini_rest only retried 429s.
  2. Google stopped responding entirely (requests ReadTimeout) — an *exception*,
     not a status code, so it escaped the status-based retry added in round 1.
Cross-provider failover could not rescue either because OPENAI_API_KEY is empty,
so the customer got no reply at all.

Run:  python test_gemini_overload_retry.py
"""
import os
import sys

import django
import requests

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from trendycrm import ai_router


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        return self._body


OVERLOADED = (503, {'error': {'status': 'UNAVAILABLE',
                              'message': 'This model is currently experiencing high demand.'}})
OK = (200, {'candidates': [{'content': {'parts': [{'text': 'Namaste! How can I help?'}]},
                            'finishReason': 'STOP'}]})


TIMEOUT = requests.exceptions.ReadTimeout('Read timed out. (read timeout=45)')
REFUSED = requests.exceptions.ConnectionError('Connection aborted.')


def run_case(name, responses, expect_reply=None, expect_error=None, seconds_per_call=0):
    """
    Feeds `responses` to _call_gemini_rest in order, asserting the outcome.
    An entry may be a (status, body) tuple or an exception instance to raise.
    The last entry repeats if there are more attempts than entries.

    Real time never passes: sleeps are recorded instead of taken, and the clock
    advances only by `seconds_per_call` per attempt plus whatever was slept — which
    is what lets the retry-budget case be exercised without a 100s test run.
    """
    calls = []
    sleeps = []
    clock = [0.0]

    def fake_post(url, json=None, timeout=None):
        clock[0] += seconds_per_call
        outcome = responses[min(len(calls), len(responses) - 1)]
        if isinstance(outcome, Exception):
            calls.append(type(outcome).__name__)
            raise outcome
        status, body = outcome
        calls.append(status)
        return FakeResponse(status, body)

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    real_post, real_sleep = ai_router.requests.post, ai_router.time.sleep
    real_monotonic = ai_router.time.monotonic
    ai_router.requests.post = fake_post
    ai_router.time.sleep = fake_sleep
    ai_router.time.monotonic = lambda: clock[0]
    try:
        reply, error = None, None
        try:
            reply = ai_router._call_gemini_rest(
                ai_router.GEMINI_FLASH_MODEL, 'system', 'hello', 'fake-key')
        except Exception as exc:
            error = str(exc)
    finally:
        ai_router.requests.post = real_post
        ai_router.time.sleep = real_sleep
        ai_router.time.monotonic = real_monotonic

    if expect_reply is not None:
        assert reply == expect_reply, f"{name}: got reply {reply!r}, expected {expect_reply!r}"
        assert error is None, f"{name}: unexpected error {error!r}"
    if expect_error is not None:
        assert error and expect_error in error, f"{name}: got error {error!r}"
        assert reply is None, f"{name}: unexpected reply {reply!r}"

    print(f"  OK  {name}")
    print(f"      attempts={len(calls)} statuses={calls} slept={sleeps}")
    return calls, sleeps


BACKOFF = list(ai_router.GEMINI_RETRY_BACKOFF)
MAX_ATTEMPTS = 1 + len(BACKOFF)

print("Gemini transient-failure handling")

# 1. Overloaded once, then recovers — the reply must survive.
calls, sleeps = run_case(
    "503 then success -> reply recovered",
    [OVERLOADED, OK],
    expect_reply='Namaste! How can I help?')
assert len(calls) == 2, "should have retried exactly once"
assert sleeps == BACKOFF[:1], "should back off before retrying"

# 2. Overloaded for the whole backoff schedule — fails, but only after real retries.
calls, sleeps = run_case(
    "sustained 503 -> raises after retries",
    [OVERLOADED],
    expect_error='UNAVAILABLE')
assert len(calls) == MAX_ATTEMPTS, f"expected {MAX_ATTEMPTS} attempts, got {len(calls)}"
assert sleeps == BACKOFF, f"unexpected backoff {sleeps}"

# 3. A 400 is the operator's problem, not capacity — must not be retried.
calls, _ = run_case(
    "400 bad request -> no retry",
    [(400, {'error': {'status': 'INVALID_ARGUMENT', 'message': 'bad model'}})],
    expect_error='INVALID_ARGUMENT')
assert len(calls) == 1, "client errors must fail immediately"

# 4. ReadTimeout is an exception, not a status — the round-2 regression.
calls, sleeps = run_case(
    "ReadTimeout then success -> reply recovered",
    [TIMEOUT, OK],
    expect_reply='Namaste! How can I help?')
assert len(calls) == 2, "a timeout must be retried, not propagated"
assert sleeps == BACKOFF[:1], "should back off after a timeout"

# 5. Sustained timeouts must surface as a transient provider error, so that
#    _gen_text_with_failover routes them to the other provider when one exists.
calls, sleeps = run_case(
    "sustained ReadTimeout -> transient provider error",
    [TIMEOUT],
    expect_error='unreachable')
assert len(calls) == MAX_ATTEMPTS, f"expected {MAX_ATTEMPTS} attempts, got {len(calls)}"
err = ValueError(f"Gemini unreachable: ReadTimeout after {MAX_ATTEMPTS} attempts")
assert ai_router._is_transient_provider_error(err), "timeout must be classified transient"

# 6. Connection errors behave the same way.
calls, _ = run_case(
    "sustained ConnectionError -> transient provider error",
    [REFUSED],
    expect_error='unreachable')
assert len(calls) == MAX_ATTEMPTS
assert ai_router._is_transient_provider_error(
    ValueError(f"Gemini unreachable: ConnectionError after {MAX_ATTEMPTS} attempts"))

# 7. Mixed failures: overload, then a timeout, then recovery.
calls, sleeps = run_case(
    "503 -> timeout -> success",
    [OVERLOADED, TIMEOUT, OK],
    expect_reply='Namaste! How can I help?')
assert len(calls) == 3 and sleeps == BACKOFF, f"unexpected sequence {calls} {sleeps}"

# 8. The retry budget must bound total wall time: when every attempt burns the full
#    read timeout, GEMINI_TOTAL_DEADLINE cuts the schedule short instead of pinning
#    the auto-reply thread for minutes.
calls, sleeps = run_case(
    "deadline caps retries when every attempt stalls",
    [TIMEOUT], expect_error='unreachable', seconds_per_call=45)
assert len(calls) < MAX_ATTEMPTS, "deadline should have stopped the retry schedule early"
assert sum(sleeps) + 45 * len(calls) <= ai_router.GEMINI_TOTAL_DEADLINE, (
    f"exceeded the {ai_router.GEMINI_TOTAL_DEADLINE}s budget: {calls} {sleeps}")

print("\nAll checks passed.")
sys.exit(0)
