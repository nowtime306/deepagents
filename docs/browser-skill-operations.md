# BrowserSkill dedicated Windows runtime

BrowserSkill is an optional capability. Do not add its check to core chat readiness or
automatically start it during platform deployment. No production deployment was performed.

## Prepare and start

Use PowerShell 7 and Node 24+. Run `scripts/install-browser-skill.ps1` once to install
the hash-pinned official CLI and extension 0.4.1. Supply an already installed Playwright
Chromium 125+ executable; the scripts never install Chrome, alter system PATH/proxy,
register a service, modify `.env`, or copy a personal profile, cookies, or history.

```powershell
./scripts/start-browser-skill.ps1 -DataDir <PLATFORM_DATA_DIR> -Chromium <absolute-chromium-exe>
./scripts/check-browser-skill.ps1 -DataDir <PLATFORM_DATA_DIR>
./scripts/stop-browser-skill.ps1 -DataDir <PLATFORM_DATA_DIR>
```

`DataDir` defaults to the process's `PLATFORM_DATA_DIR`, then the API Settings value.
API and Work must use that exact same data directory. Explicit `-Executable`,
`-Extension`, and `-Playwright` paths support already prepared official dependencies.
The Playwright default is this checkout's `apps/web/node_modules/@playwright/test/index.js`;
an existing project installation may be supplied read-only. Nothing modifies that installation.

The supervisor launches hidden and owns a foreground daemon plus an independent Chromium
process with a new `<DataDir>/browser-skill/profile`. Chromium uses `--no-startup-window`;
the supervisor opens no web pages. Only an explicit user recording/session start opens
the BrowserSkill interaction window. Setup configures only this extension's local port.

Fixed loopback ports are 52983 (BrowserSkill) and 52982 (supervisor-only Chromium CDP).
The default official extension initially tries 52800 before its dedicated preference loads;
startup therefore also refuses any existing listener on 52800. Ports cannot be adopted,
and a conflict must be resolved before retrying. These ports are local control interfaces,
so retain the normal account boundary for the data directory. Do not expose them externally.

## Configuration and ownership

`BSK_HOME` is always `<DataDir>/browser-skill/home`, with `BSK_AUTO_START=0`,
`BSK_AUTO_UPDATE=off`, and `BSK_BROWSER_WAIT_MS=0`. After CLI, extension, protocol and
unique peer checks, the supervisor atomically writes this non-secret configuration:

```json
{
  "schema_version": 1,
  "executable": "<absolute managed bsk.exe>",
  "instance_id": "<8 lowercase hex digits>",
  "cli_version": "0.4.1",
  "extension_version": "0.4.1",
  "protocol_version": "1.8"
}
```

`connection.json` contains exactly these six fields; API and Work read the same file.
`state.json` separately records PIDs, executable paths, ports and startup/exit status.
`launch.json` and `profile.owner` bind the private profile to this runtime. Existing
unowned profiles and linked profile directories are refused.

Start, stop and check validate CIM process identity, executable, parent PID, command
line, dedicated user-data-dir, extension manifest, daemon discovery PID and loopback
listener ownership. Stop sends a request to this supervisor, closes this browser via
CDP (normally closing its contexts/sessions), then stops only its own daemon. It does
not kill Chrome/bsk by process name or touch any unrelated daemon. During failed
startup, if CDP never connects or graceful close fails, the supervisor may terminate
the exact still-live Chromium child it created before stopping its own daemon.
It selects the BrowserSkill worker by the official background script and verifies its
runtime manifest before setting the port; Chromium component workers are excluded.
Repeated normal
start/stop is supported; a crashed or unverified instance requires operator inspection.

On failure, inspect `state.json`, `launcher-error.log`, `daemon.log` and `chromium.log`. Failed startup
never publishes a ready connection; failed cleanup reports `CLEANUP_REQUIRED` and retains
ownership evidence. Do not delete that evidence or adopt a surviving browser. The helper
does not automatically restart an interrupted recording. BrowserSkill's finite 24-hour
daemon idle limit also fails closed rather than silently launching a replacement.

Deployment admission rejects 081 recordings in `starting`, `recording` or `stopping`;
completed `review` drafts do not block deployment. Existing Work run admission already
protects running replay tasks.

## Validation performed

In the feature worktree's private `.local/ops-validation` data directory, official CLI
and extension 0.4.1 connected with protocol 1.8 and a single extension instance. Both
listeners were bound to 127.0.0.1. Start/check succeeded with zero sessions; normal stop
removed the connection file, exited the owned Chromium/daemon, and released both ports.
The independent `scripts/test-browser-skill-operations.ps1` runner also checks an empty
profile, repeated start, PID mismatch refusal, port conflict refusal and failure before
CDP setup. Initial wrong-worker failures remain in private QA evidence directories and
never published a ready connection.
No production/UI page, login, personal browser state, or model was used. This operational
check is separate from the backend's toy recording/replay and pending human UI acceptance.

The root additionally executed the official installer in this worktree's ignored
`.local/browser-skill-dependencies` directory. Both pinned ZIP SHA256 checks passed,
the upstream license was saved, and the installed CLI reported `bsk 0.4.1`. Evidence:
`.local/browser-skill-install-validation.log`. Installation did not start a connection
or browser and did not modify existing user environment files or credentials.
