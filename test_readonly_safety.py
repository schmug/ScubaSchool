#!/usr/bin/env python3
"""Unit tests for the read-only safety invariant of test_gws_wrapper.

These are the security-critical tests: they prove (a) the command classifier
rejects anything that could mutate tenant config, and (b) --dry-run executes
ZERO subprocess calls. Run with:  python3 -m unittest test_readonly_safety -v
"""
import unittest

import test_gws_wrapper as wrapper


class ClassifyCommandTests(unittest.TestCase):
    def test_read_verbs_classified_read(self):
        self.assertEqual(wrapper.classify_command(["gws", "auth", "status"]), "read")
        self.assertEqual(
            wrapper.classify_command(
                ["gws", "admin-reports", "customerUsageReports", "get", "--params", "{}"]
            ),
            "read",
        )
        self.assertEqual(
            wrapper.classify_command(["gws", "admin-reports", "activities", "list"]),
            "read",
        )

    def test_login_classified_auth_not_read(self):
        self.assertEqual(
            wrapper.classify_command(["gws", "auth", "login", "--readonly"]),
            "auth",
        )

    def test_write_verbs_classified_write(self):
        for verb in ("create", "update", "delete", "patch", "insert", "modify"):
            cmd = ["gws", "drive", "files", verb, "--params", "{}"]
            self.assertEqual(
                wrapper.classify_command(cmd), "write", f"{verb} must be write"
            )

    def test_auth_export_is_not_read(self):
        # `gws auth export` dumps credentials -> must NEVER pass the read guard.
        self.assertNotEqual(
            wrapper.classify_command(["gws", "auth", "export"]), "read"
        )


class AssertReadOnlyTests(unittest.TestCase):
    def test_read_command_passes(self):
        wrapper.assert_read_only(
            ["gws", "admin-reports", "customerUsageReports", "get"]
        )  # no raise

    def test_write_command_raises(self):
        with self.assertRaises(wrapper.ReadOnlyViolation):
            wrapper.assert_read_only(["gws", "drive", "files", "delete"])

    def test_auth_command_raises(self):
        # login is an auth grant, not a config read; the data guard must reject it.
        with self.assertRaises(wrapper.ReadOnlyViolation):
            wrapper.assert_read_only(["gws", "auth", "login"])


class BuiltCommandsAreReadOnlyTests(unittest.TestCase):
    def test_status_cmd_is_read(self):
        self.assertEqual(wrapper.classify_command(wrapper.status_cmd()), "read")

    def test_policy_cmd_is_read(self):
        self.assertEqual(wrapper.classify_command(wrapper.policy_cmd()), "read")

    def test_login_cmd_is_readonly_not_full(self):
        cmd = wrapper.login_cmd()
        self.assertIn("--readonly", cmd, "login must request read-only scopes")
        self.assertNotIn("--full", cmd, "login must never request all scopes")


class DryRunMakesNoSubprocessCalls(unittest.TestCase):
    """The whole point of --dry-run: nothing may touch the tenant."""

    def setUp(self):
        def _boom(*args, **kwargs):
            raise AssertionError("subprocess was invoked during --dry-run")

        self._orig = wrapper.subprocess.run
        wrapper.subprocess.run = _boom

    def tearDown(self):
        wrapper.subprocess.run = self._orig

    def test_authenticate_dry_run(self):
        self.assertTrue(wrapper.authenticate(dry_run=True))

    def test_check_auth_dry_run(self):
        self.assertTrue(wrapper.check_auth(dry_run=True))

    def test_fetch_one_policy_dry_run(self):
        self.assertTrue(wrapper.fetch_one_policy(dry_run=True))

    def test_main_dry_run_exits_zero(self):
        self.assertEqual(wrapper.main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
