#!/usr/bin/env python3
"""
test_gws_wrapper.py — minimal GWS CLI integration validator.

PURPOSE
    Confirm we can (1) authenticate read-only, (2) verify the cached token
    works without re-prompting, and (3) pull ONE read-only piece of
    tenant/admin config via subprocess. No scoring, no Sheets, no
    benchmarking. This is a plumbing test for the `gws` (Google Workspace CLI)
    integration only.

EXACT `gws` COMMANDS THIS SCRIPT USES
    authenticate():     gws auth login --readonly
    check_auth():       gws auth status
    fetch_one_policy(): gws admin-reports customerUsageReports get \
                            --params '{"date": "<~3 days ago, yyyy-mm-dd>"}'

VERIFIED AGAINST THE REAL BINARY (gws 0.22.5, Homebrew, 2026-06)
    Every command above was confirmed against `gws --help`, `gws auth --help`,
    `gws auth login --help`, and `gws schema admin-reports.customerUsageReports.get`.
    Corrections made after seeing the installed CLI (web docs were incomplete):
      * `gws auth login --readonly` DOES exist and is the correct read-only
        login. (`-s/--services` takes service NAMES like `drive`, not scopes;
        `--scopes` takes custom scope strings; `--full` requests everything.)
      * `gws auth status` returns JSON by default and reports the LOCAL auth
        state. When unauthenticated it returns exit 0 with `auth_method:"none"`,
        so we must inspect that field — exit code alone is not enough.
      * There is NO `gws auth list` in this version (auth subcommands: login,
        setup, status, export, logout). The account email, when present, is read
        from `gws auth status`.
      * There is NO `admin`/Directory service. Known services: drive, sheets,
        gmail, calendar, admin-reports, docs, slides, tasks, people, chat,
        classroom, forms, keep, meet, events, modelarmor, workflow, script.
        The admin surface is `admin-reports` (Admin SDK *Reports* API). The
        read-only tenant-level call used here is `customerUsageReports get`,
        which requires a `date` (path param, yyyy-mm-dd, UTC-8); reports lag a
        couple of days, so we ask for ~3 days ago.
    Ref: https://github.com/googleworkspace/cli

PREREQUISITES FOR A LIVE RUN
    `gws auth setup` must have been run once (needs gcloud + a GCP OAuth client)
    so that `gws auth login --readonly` has a client config to use. Without it,
    login fails with a clear error, which this script surfaces verbatim.

READ-ONLY SAFETY MODEL
    `gws` has no per-command read-only flag for data calls — read-only is the
    OAuth *scope* granted at login (`--readonly`). What this script guarantees:
      * Every data/config command it issues is a read verb (get/list/status),
        enforced at runtime by assert_read_only() (see READ_VERBS below).
      * Login requests read-only scopes only (`--readonly`, never `--full`).
      * The only state-changing call is `gws auth login`, which changes LOCAL
        credential state and grants only the scopes you approve — it does not
        mutate tenant config.
      * `gws auth export` (which prints decrypted credentials) is classified as
        non-read and can never pass the guard. This script never calls it.
    Tokens are never stored or printed; raw auth stdout is not echoed — only
    derived fields (account email, top-level JSON keys) are.

USAGE
    python3 test_gws_wrapper.py            # live run (requires `gws` + auth setup)
    python3 test_gws_wrapper.py --dry-run  # print exact commands, run NOTHING

Python 3.10+, standard library only (argparse, datetime, json, subprocess, sys).
`datetime` is used solely to compute the required report date; no third-party
or Google API libraries are imported.
"""

import argparse
import datetime
import json
import subprocess
import sys

# Binary name. Kept as a constant so there is exactly one place to change it.
GWS = "gws"

# customerUsageReports requires a date; Reports data lags a couple of days, so
# ask for a few days back to avoid an empty/"not yet available" response.
REPORT_LOOKBACK_DAYS = 3

# Command classification. The method verb is the first of these tokens to appear
# in the argv (services/resources are never named like these verbs).
READ_VERBS = {"get", "list", "status", "about", "schema", "describe"}
WRITE_VERBS = {
    "create", "update", "delete", "patch", "insert", "modify", "trash",
    "remove", "set", "add", "move", "copy", "import", "export", "send",
    "watch", "stop", "clear", "batchupdate", "batchUpdate",
}
AUTH_VERBS = {"login", "setup", "logout", "revoke"}


class ReadOnlyViolation(Exception):
    """Raised when a command that should read would do something else."""


def classify_command(cmd):
    """Return 'read' | 'auth' | 'write' | 'unknown' for a gws argv list.

    Conservative: anything not provably a read verb is NOT treated as read.
    """
    for tok in cmd:
        if tok in READ_VERBS:
            return "read"
        if tok in AUTH_VERBS:
            return "auth"
        if tok in WRITE_VERBS:
            return "write"
    return "unknown"


def assert_read_only(cmd):
    """Guard a *data* command: raise unless it is provably read-only.

    Not used on `gws auth login` (that is an auth grant, handled explicitly).
    """
    kind = classify_command(cmd)
    if kind != "read":
        raise ReadOnlyViolation(
            f"refusing to run non-read command (classified '{kind}'): {' '.join(cmd)}"
        )


# ---- command builders (pure; trivially testable) ---------------------------

def login_cmd():
    # --readonly = request read-only OAuth scopes only (never --full).
    return [GWS, "auth", "login", "--readonly"]


def status_cmd():
    return [GWS, "auth", "status"]


def _report_date():
    today = datetime.date.today()
    return (today - datetime.timedelta(days=REPORT_LOOKBACK_DAYS)).isoformat()


def policy_cmd():
    params = json.dumps({"date": _report_date()})
    return [GWS, "admin-reports", "customerUsageReports", "get", "--params", params]


# ---- subprocess plumbing ---------------------------------------------------

class CmdResult:
    __slots__ = ("ok", "code", "stdout", "stderr")

    def __init__(self, ok, code, stdout, stderr):
        self.ok = ok
        self.code = code
        self.stdout = stdout
        self.stderr = stderr


def run(cmd, timeout=120):
    """Run a gws command. Never raises; returns a CmdResult.

    On failure, the real gws stderr is preserved in result.stderr so the caller
    can surface it for debugging auth/scope issues.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CmdResult(proc.returncode == 0, proc.returncode, proc.stdout, proc.stderr)
    except FileNotFoundError:
        return CmdResult(
            False, 127, "",
            f"'{GWS}' not found on PATH — is the Google Workspace CLI installed?",
        )
    except subprocess.TimeoutExpired:
        return CmdResult(False, 124, "", f"timed out after {timeout}s: {' '.join(cmd)}")
    except OSError as exc:
        return CmdResult(False, 1, "", f"failed to exec {' '.join(cmd)}: {exc}")


def _classify_tag(cmd):
    kind = classify_command(cmd)
    return {
        "read": "[READ-ONLY]",
        "auth": "[AUTH — interactive, requests read-only scopes only]",
        "write": "[!! NOT READ-ONLY — WOULD MUTATE !!]",
        "unknown": "[?? UNCLASSIFIED — review before running]",
    }[kind]


def _show_dry(cmd):
    """Print a copy-pasteable command line with its read-only classification."""
    print(f"  {_classify_tag(cmd)}")
    print(f"  $ {shell_join(cmd)}")


def shell_join(cmd):
    """Quote argv into a single copy-pasteable shell line."""
    out = []
    for tok in cmd:
        if tok and all(c.isalnum() or c in "-_=:./" for c in tok):
            out.append(tok)
        else:
            out.append("'" + tok.replace("'", "'\\''") + "'")
    return " ".join(out)


# ---- the three validation steps --------------------------------------------

def authenticate(dry_run=False):
    """Step 1: read-only OAuth login. Returns True on success.

    NOTE: `gws auth login --readonly` is interactive (opens a browser consent
    flow) and needs `gws auth setup` to have configured an OAuth client. Tokens
    are handled and encrypted at rest by gws; this function never stores or
    prints them — only the exit code and (on failure) stderr.
    """
    cmd = login_cmd()
    if dry_run:
        print("authenticate() would run:")
        _show_dry(cmd)
        return True

    result = run(cmd, timeout=300)  # generous: waits on human browser consent
    if not result.ok:
        print(f"  gws stderr: {result.stderr.strip() or '(none)'}", file=sys.stderr)
    return result.ok


def check_auth(dry_run=False):
    """Step 2: verify the cached auth state WITHOUT re-prompting.

    `gws auth status` reports local auth state as JSON and never prompts. Prints
    only the authenticated account email (when gws reports one). Raw status JSON
    is not echoed.
    """
    cmd = status_cmd()
    assert_read_only(cmd)

    if dry_run:
        print("check_auth() would run:")
        _show_dry(cmd)
        return True

    status = run(cmd)
    if not status.ok:
        print(f"  gws stderr: {status.stderr.strip() or '(none)'}", file=sys.stderr)
        return False

    if not _is_authenticated(status.stdout):
        print("  not authenticated (auth_method=none) — run authenticate() first",
              file=sys.stderr)
        return False

    email = _extract_email(status.stdout)
    print(f"  authenticated account: {email or '(email not reported by gws)'}")
    return True


def fetch_one_policy(dry_run=False):
    """Step 3: pull ONE read-only piece of tenant config; print top-level keys."""
    cmd = policy_cmd()
    assert_read_only(cmd)  # defense in depth: never let this become a write

    if dry_run:
        print("fetch_one_policy() would run:")
        _show_dry(cmd)
        return True

    result = run(cmd)
    if not result.ok:
        print(f"  gws stderr: {result.stderr.strip() or '(none)'}", file=sys.stderr)
        return False

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"  could not parse gws JSON output: {exc}", file=sys.stderr)
        return False

    if not isinstance(data, dict):
        print(f"  expected a JSON object, got {type(data).__name__}", file=sys.stderr)
        return False

    # Print STRUCTURE only (top-level keys), not values — confirms shape without
    # dumping tenant data.
    print(f"  tenant config top-level keys: {sorted(data.keys())}")
    return True


# ---- small parsing helpers (tolerant of unknown gws JSON shapes) -----------

def _is_authenticated(stdout):
    """True if `gws auth status` JSON indicates a usable credential.

    Real unauthenticated shape: {"auth_method":"none","credential_source":"none",
    "storage":"none", "token_cache_exists":false, ...}.
    """
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    for key in ("auth_method", "credential_source", "storage"):
        val = data.get(key)
        if isinstance(val, str) and val.lower() not in ("", "none"):
            return True
    # Fall back to presence of cached/stored credentials.
    return bool(data.get("token_cache_exists") or data.get("encrypted_credentials_exists"))


def _extract_email(stdout):
    """Best-effort pull of an account email from gws JSON, tolerant of shape."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    return _find_email(data)


def _find_email(node):
    if isinstance(node, dict):
        for key in ("email", "account", "default_account", "default", "user"):
            val = node.get(key)
            if isinstance(val, str) and "@" in val:
                return val
        for val in node.values():
            found = _find_email(val)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_email(item)
            if found:
                return found
    elif isinstance(node, str) and "@" in node:
        return node
    return None


# ---- orchestration ---------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate GWS CLI read-only integration (auth + one config read)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the exact gws commands that WOULD run; execute nothing.",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        print("=" * 64)
        print("DRY RUN — no commands executed")
        print("Every line below is read-only unless explicitly flagged otherwise.")
        print("=" * 64)

    steps = (
        ("authenticate", authenticate),
        ("check_auth", check_auth),
        ("fetch_one_policy", fetch_one_policy),
    )

    results = []
    for name, fn in steps:
        try:
            ok = bool(fn(dry_run=args.dry_run))
        except ReadOnlyViolation as exc:
            print(f"  SAFETY ABORT: {exc}", file=sys.stderr)
            ok = False
        except Exception as exc:  # surface, never swallow
            print(f"  unexpected error in {name}: {exc}", file=sys.stderr)
            ok = False
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}")
        results.append(ok)
        print()

    all_ok = all(results)
    print("=" * 64)
    print(f"RESULT: {'ALL PASS' if all_ok else 'FAILURES PRESENT'}"
          f"{'  (dry run — nothing touched the tenant)' if args.dry_run else ''}")
    print("=" * 64)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
