import unittest

from app.job_contract import JobOutcome, run_job, skipped


class Recorder:
    def __init__(self, fail_on=None):
        self.events = []
        self.fail_on = fail_on

    def __call__(self, status, detail=""):
        self.events.append((status, detail))
        if status == self.fail_on:
            raise RuntimeError("recorder failed")


class Notifier:
    def __init__(self, should_fail=False):
        self.errors = []
        self.should_fail = should_fail

    def __call__(self, action, error):
        self.errors.append((action, str(error)))
        if self.should_fail:
            raise RuntimeError("notifier failed")


class JobContractTests(unittest.TestCase):
    def test_normal_return_is_success(self):
        recorder = Recorder()
        notifier = Notifier()

        outcome = run_job("post", lambda: None, recorder, notifier)

        self.assertEqual(outcome, JobOutcome("success", ""))
        self.assertEqual(recorder.events, [("success", "")])
        self.assertEqual(notifier.errors, [])

    def test_explicit_skipped_outcome_is_preserved(self):
        recorder = Recorder()
        notifier = Notifier()

        outcome = run_job("summary", lambda: skipped("not enough news"), recorder, notifier)

        self.assertEqual(outcome, JobOutcome("skipped", "not enough news"))
        self.assertEqual(recorder.events, [("skipped", "not enough news")])

    def test_exception_is_recorded_notified_and_reraised(self):
        recorder = Recorder()
        notifier = Notifier()

        with self.assertRaisesRegex(RuntimeError, "facebook failed"):
            run_job("video", lambda: (_ for _ in ()).throw(RuntimeError("facebook failed")), recorder, notifier)

        self.assertEqual(recorder.events, [("failed", "facebook failed")])
        self.assertEqual(notifier.errors, [("video", "facebook failed")])

    def test_observability_failure_does_not_hide_original_exception(self):
        recorder = Recorder(fail_on="failed")
        notifier = Notifier(should_fail=True)

        with self.assertRaisesRegex(ValueError, "original failure"):
            run_job("reply", lambda: (_ for _ in ()).throw(ValueError("original failure")), recorder, notifier)


if __name__ == "__main__":
    unittest.main()
