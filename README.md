# ScubaSchool

A minimal, read-only validation harness for the [Google Workspace CLI](https://github.com/googleworkspace/cli)
(`gws`). It confirms one thing and one thing only: that we can authenticate
**read-only** and pull tenant/admin configuration by shelling out to `gws` via
`subprocess`.

This is deliberately **not** a baseline scanner. There is no scoring, no Google
Sheets export, and no benchmarking here. It is the plumbing test that has to
pass before any of that gets built. The longer-term goal is to adapt
[CISA's ScubaGoggles](https://github.com/cisagov/ScubaGoggles) baseline checks
onto the `gws` CLI; this harness just proves the CLI integration works.

> ⚠️ Not an officially supported Google or CISA product. `gws` itself prints
> "This is not an officially supported Google product."

## What it checks

`test_gws_wrapper.py` runs three steps in sequence, with PASS/FAIL output per step:

1. **`authenticate()`** — `gws auth login --readonly` (requests read-only OAuth
   scopes only). Captures exit code and stderr; never stores or prints tokens.
2. **`check_auth()`** — `gws auth status` to verify the cached credential
   without re-prompting. Prints only the authenticated account email.
3. **`fetch_one_policy()`** — pulls one read-only piece of tenant config and
   prints its **top-level JSON keys only** (structure, not values).

## Read-only safety model

`gws` has no per-command "read-only" switch for data calls — read-only is the
OAuth **scope** you grant at login. This harness enforces read-only three ways:

- It logs in with `--readonly` and **never** `--full`.
- Every data/config command it issues is a read verb (`get` / `list` /
  `status`), enforced at runtime by `assert_read_only()`. A command that
  classifies as anything else raises `ReadOnlyViolation` before it runs.
- `gws auth export` (which prints decrypted credentials) is classified as
  non-read and can never pass the guard. The harness never calls it.

Tokens are never stored or printed, and raw auth stdout is not echoed — only
derived fields (the account email, top-level keys) are shown.

## `--dry-run` — review every command before it touches the tenant

```bash
python3 test_gws_wrapper.py --dry-run
```

Dry-run executes **no** subprocess calls. It prints each exact `gws` command it
*would* run as a copy-pasteable line, tagged with its read-only classification,
so the commands can be reviewed for correctness and read-only safety first:

```
================================================================
DRY RUN — no commands executed
Every line below is read-only unless explicitly flagged otherwise.
================================================================
authenticate() would run:
  [AUTH — interactive, requests read-only scopes only]
  $ gws auth login --readonly
[PASS] authenticate

check_auth() would run:
  [READ-ONLY]
  $ gws auth status
[PASS] check_auth

fetch_one_policy() would run:
  [READ-ONLY]
  $ gws admin-reports customerUsageReports get --params '{"date": "2026-06-17"}'
[PASS] fetch_one_policy
```

## Exact `gws` commands used

Verified against **`gws` 0.22.5** (`gws --help`, `gws auth login --help`,
`gws schema admin-reports.customerUsageReports.get`):

| Step | Command |
| --- | --- |
| `authenticate()` | `gws auth login --readonly` |
| `check_auth()` | `gws auth status` |
| `fetch_one_policy()` | `gws admin-reports customerUsageReports get --params '{"date": "<~3 days ago>"}'` |

Notes on the CLI (it builds its command surface dynamically from Google's
Discovery Service, so verify with `gws schema <service>.<resource>.<method>`):

- There is **no `--readonly`-less** way to scope login here; `--readonly` is the
  flag. `-s/--services` takes service *names* to narrow the scope picker, not
  scope URLs.
- There is **no `gws auth list`** and **no `admin`/Directory service** in this
  version. The admin surface is `admin-reports` (Admin SDK *Reports* API).
- `customerUsageReports get` requires a `date` (`yyyy-mm-dd`, UTC-8); report
  data lags a couple of days, so the harness asks for ~3 days ago.

## Prerequisites

- **Python 3.10+** — standard library only (`argparse`, `datetime`, `json`,
  `subprocess`, `sys`). No `gspread`, no Google API libraries.
- **`gws`** installed:
  ```bash
  brew install googleworkspace-cli   # provides the `gws` binary
  ```
- For a **live** run, run `gws auth setup` once first (needs `gcloud` and a GCP
  OAuth client) so `gws auth login --readonly` has a client config to use.
  Without setup, login fails with a clear error, which the harness surfaces
  verbatim.

## Usage

```bash
# Safe: print the exact commands, run nothing
python3 test_gws_wrapper.py --dry-run

# Live: login (read-only) -> verify -> pull one config (requires gws auth setup)
python3 test_gws_wrapper.py
```

Exit code is `0` only if all three steps PASS.

## Testing

The read-only guard is the security-critical invariant, so it has unit tests
(standard-library `unittest`):

```bash
python3 -m unittest test_readonly_safety -v
```

These prove (a) the classifier rejects anything that could mutate config, and
(b) `--dry-run` makes zero subprocess calls.

## Layout

```
test_gws_wrapper.py      # the validator: authenticate / check_auth / fetch_one_policy / main
test_readonly_safety.py  # unit tests for the read-only guard and dry-run safety
```
