# Email deadline reminders

## Delivery status and handoff

**Local email delivery verified; shared hosting is still pending.** A controlled
Resend test was accepted and receipt confirmed by the account owner. The local
worker was started with 6- and 7-hour preferences. Running the web app alone does
not send emails. This is not an always-on team deployment: a verified sender domain
for other recipients and hosted app/database/worker setup remain pending.
Automated tests use fake providers; live scheduling has not been verified end to end.

Completed in this change:
- Per-user opt-in/opt-out and configurable offsets, including 6 and 7 hours.
- Registered-email recipient selection and assignment/course/deadline/remaining-hours content.
- Completed-work suppression using the planner's existing study-session model.
- Durable delivery ledger, concurrent-worker claims, bounded retries, and sanitized failure logs.
- Resend adapter and a cron-friendly or continuous background worker command.
- Automated coverage: **178 tests passed** across the combined Sprint 3 features, including 28 reminder-specific tests.
  Seven existing SQLAlchemy warnings originate from the pre-existing privacy tests.

Still required before live activation:
1. Verify a sender domain for team delivery; the local test account/key is configured.
2. Deploy/schedule the worker with the same database as the web app.
3. Complete an end-to-end scheduled reminder test; standalone delivery is verified.
4. Verify deployment restart/recovery and provider delivery monitoring.
5. Consider delivery/bounce webhooks and optional SMS integration separately.

Do not mark live delivery verified solely because the settings page works or the
mocked tests pass. No worker should be enabled for real users until the email
provider configuration and deployment are ready.

## Student flow

Sign in and open **Reminders**. Email is opt-in (disabled until saved). Choose one
or more offsets: **1, 3, 6, 7, 12, 24, or 48 hours** before each deadline. Selecting
both 6 and 7 schedules two distinct reminders. Messages go only to the student's
registered email; the preferences form cannot override the recipient or user ID.
Students can disable reminders or change offsets at any time.

Emails contain the assignment title, course, deadline, and remaining estimated
study hours. Remaining hours are the estimate minus completed study-session hours,
clamped to zero. An assignment marked Completed is suppressed even when no study sessions were
logged. Reminders are also suppressed when logged hours cover the estimate.
Zero-hour assignments and assignments without deadlines are also suppressed.

Select a timezone for manual deadlines and email display. Canvas assignments use
their imported timezone to resolve the instant. Existing manually entered dates
represent **midnight at the beginning of the selected day**, not 11:59 PM.
Date/time conversion uses IANA timezone rules, including daylight-saving offsets.
Existing ambiguous local times use Python's default first occurrence (fold=0).

## Administrator setup

Use Python 3.12 and the repository requirements. No additional Python dependency
is required. New tables are created by the existing startup `db.create_all()`;
existing user/assignment rows are not modified. Back up the database before updates.

1. Configure a Resend account and verify a sending domain/address.
2. Set `RESEND_API_KEY` and `STUDYFLOW_REMINDER_FROM` in the server environment.
   Use `.env.reminders.example` as a template, but keep real secrets outside Git.
   This implementation does not automatically load that example file.
3. Set the same **absolute** `STUDYFLOW_DATABASE_URI` for both web app and worker.
4. Start the worker independently from the Flask web process:

```bash
source .venv/bin/activate
export STUDYFLOW_DATABASE_URI='sqlite:////absolute/path/to/instance/studyflow.db'
# Supply RESEND_API_KEY using your deployment secret manager/environment.
export STUDYFLOW_REMINDER_FROM='StudyFlow <reminders@your-verified-domain.example>'
python -m flask --app app reminders --loop --interval 60
```

A single pass is also available for cron/system schedulers:

```bash
python -m flask --app app reminders
```

For cron, run once per minute with the application's absolute working directory,
virtualenv Python, and environment variables. For a persistent deployment, supervise
`--loop` with systemd or another process manager and restart it on failure. The
hosting machine must remain awake and connected. Closing the student's browser
does not stop reminders, but stopping the host/worker does. The feature is **not
live until provider configuration and a scheduled worker are in place**.

## Scheduling and duplicate prevention

The worker polls; delivery is approximately at the selected time (normally within
one polling interval), subject to network/provider availability. New events can
catch up for 15 minutes after the target time. A longer outage does not send a burst
of old reminders. Already attempted transient failures retry with exponential
backoff, capped at 30 minutes, until the deadline or safe idempotency limit.

`ReminderPreference` stores per-user opt-in, selected hours and timezone.
`DeadlineNotification` is a durable outbox with a unique constraint over assignment,
UTC deadline, offset, and channel. It records status, attempts, timestamps, provider
ID, and a sanitized error code. Database claims use a two-minute lease so overlapping
workers do not send the same event simultaneously.

Each event gets one durable random provider idempotency key and a persisted payload
before sending. Retries reuse both. This covers a worker crash or timeout after the
provider accepted the request but before the database recorded success. Resend
retains idempotency keys for 24 hours; automatic retry stops at 23 hours to keep a
safety margin. An overdue or completed assignment is never retried. Terminal failed,
sent, or cancelled events are not recreated on the next polling pass.

Before each attempt, the worker rechecks ownership, opt-in, offset, deadline, and
completion. If email/title/course/hours/content changed after an ambiguous attempt,
it cancels that retry instead of sending stale content or reusing a key with a new
payload. Email information is labeled with its initial preparation time. An email
already accepted by the provider cannot be recalled when preferences or progress
change. A new deadline creates a new reminder event. Cancellation of an old event
is permanent, even if preferences are later toggled back.

The tables contain notification content/recipient information and should receive
the same access protection and backup policy as the planner database. Do not expose
the ledger through unauthenticated pages. No credentials, recipients, or message
bodies are included in application log messages.

## Failure handling and delivery limits

HTTP rejections, connection failures, malformed provider responses and retry-window
expiry are logged with the notification ID and a safe code. Retryable failures
include timeouts, HTTP 408/429 and server errors; permanent rejections remain failed.
The worker fails clearly before queuing messages if email configuration is missing.
Unexpected process failures are recovered through persisted leases and idempotency.

`sent` means **accepted by the email provider**, not confirmed arrival in an inbox.
Spam filtering, bounces and delayed delivery are outside this implementation's
control. Inspect the provider dashboard for downstream delivery/bounce events;
webhook ingestion is not implemented. A controlled local provider test succeeded; hosted delivery and downstream
monitoring still need verification.

## Future SMS integration

The outbox includes a channel field and the worker calls a delivery adapter through
`send(payload, idempotency_key)`. A future SMS adapter can use the same design, but
must add verified phone numbers, separate opt-in, message formatting, costs/limits,
and equivalent duplicate guarantees. SMS is intentionally not offered in the UI.

## Validation

```bash
python -m pytest -q tests/test_reminders.py
python -m pytest -q
```

Tests use fake providers, isolated databases and fixed clocks. They cover both
6/7-hour offsets, duplicate polls, partial/completed work, preferences and CSRF,
account boundaries, timezone conversion, deadline changes, late polling, provider
failures, ambiguous acceptance, leases/crash recovery and idempotency expiry.
No actual emails are sent by tests.

Provider references:
- https://resend.com/docs/api-reference/emails/send-email
- https://resend.com/docs/dashboard/emails/idempotency-keys

## Activation and operations checklist

The deployment helpers below are available; they do not activate delivery by themselves.
The provider credentials, target host, and controlled recipient must be configured
before the live verification checklist can be completed.

### Preflight and monitoring

Run these commands from the same environment and working directory as the worker:

```bash
.venv/bin/python -m flask --app app reminders-check
.venv/bin/python -m flask --app app reminders-status
```

`reminders-check` checks that credentials are present and reminder tables can be
queried. It does not contact Resend, validate the API key or sender, or send mail.
`reminders-status` shows enabled-account and delivery-state counts without exposing
student email addresses or assignment content. Investigate rising `failed` counts
or persistent `retry`/`sending` records using worker logs and the provider dashboard.
These are manual operational checks, not an automated alerting service.

### Persistent Linux deployment

An example service is provided at `deploy/studyflow-reminders.service`. Adjust its
user, group, working directory and virtualenv paths to the actual web deployment.
Create `/etc/studyflow/reminders.env` outside the repository, readable only by the
administrator, with the three variables in `.env.reminders.example`. Use the exact
same absolute database URI as the web process; ensure the service user can write
the database and its containing directory. Install the service only after the
controlled live test succeeds:

```bash
sudo cp deploy/studyflow-reminders.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now studyflow-reminders
sudo systemctl status studyflow-reminders
sudo journalctl -u studyflow-reminders --since '1 hour ago'
```

The service checks local setup before starting and restarts on failure, with a
restart limit to avoid looping indefinitely on bad configuration. After correcting
configuration, use `sudo systemctl reset-failed studyflow-reminders` and restart
it. To stop delivery: `sudo systemctl disable --now studyflow-reminders`.
This template is for Linux, not macOS. A Mac demonstration can use the foreground
worker command above but requires the Mac to stay awake and the terminal process
to remain running.

### Controlled live verification

Use an isolated test database and an account whose registered email belongs to the
tester; do not test by enabling all production users. Do not publish credentials,
message contents, or student addresses in screenshots or logs.

1. Configure a verified sender and API key, then run `reminders-check`.
2. Enable a 7-hour reminder for the test account. Create a test assignment due
   seven hours from now with remaining work; set an explicit time and timezone.
3. Run one worker pass. Check `reminders-status`, provider acceptance, and receipt
   in the tester's inbox (including spam). Check title, course, deadline and hours.
4. Repeat the pass: confirm the same notification is not sent again. Repeat with
   a separate assignment due six hours from now and a 6-hour preference.
5. Disable reminders and verify a newly eligible assignment sends nothing. Enable
   them again and verify completed study hours suppress a different due reminder.
6. Restart the worker against the same test database and confirm sent records
   remain deduplicated. Automated tests separately exercise provider failures,
   retry leases and uncertain responses without sending real mail.
7. Record the date, environment, results and provider message IDs privately. Only
   then activate the supervised worker against the web app's database.

Provider dashboard delivery/bounce monitoring remains necessary: successful API
acceptance is not evidence of inbox delivery. A standalone local delivery test succeeded; the complete hosted scheduling
checklist has not yet been verified.

### Recommended first setup on a Mac

Start with a controlled test to your own address using Resend's test sender. Create
an account at https://resend.com and a sending API key in its dashboard. You do not
need a domain for this own-address test. The default `onboarding@resend.dev` sender
can send only to the email address associated with your Resend account. The test
student's registered StudyFlow email must match it. For other students, verify a
domain you control and configure a sender on that domain first.

In your own terminal, from the repository directory, run:

```bash
.venv/bin/python scripts/reminder_setup.py configure --database /absolute/path/to/the/webapps/studyflow.db
.venv/bin/python scripts/reminder_setup.py check
```

Replace the database path with the existing database used by the running web app;
the helper refuses a nonexistent file. Enter the API key at the hidden prompt,
not in chat or a command argument. Configuration is stored in
`~/.config/studyflow/reminders.json` with owner-only permissions, outside Git.
The helper does not change the web app's database settings. Use an isolated test
web app/database for the controlled verification described above.

Once the correct test account, reminder preferences, and assignment are ready:

```bash
.venv/bin/python scripts/reminder_setup.py start
```

This starts real delivery for eligible opted-in accounts in the configured database.
Keep the Mac awake and the terminal running. Stop with Control-C. In a second
terminal, use `scripts/reminder_setup.py status` with the same virtualenv Python.
This foreground demo is not an always-on deployment; use a supervised hosted worker
and persistent shared database for reminders that continue while the Mac is off.

Reference: https://resend.com/docs/knowledge-base/403-error-resend-dev-domain
