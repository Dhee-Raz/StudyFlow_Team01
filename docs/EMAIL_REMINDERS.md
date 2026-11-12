# Email deadline reminders

## Delivery status and handoff

**Implemented and tested; live email delivery is not activated.** The current
web app can save reminder preferences, but running the web app alone sends no
emails. Automated tests use fake providers; no real email delivery has been tested.

Completed in this change:
- Per-user opt-in/opt-out and configurable offsets, including 6 and 7 hours.
- Registered-email recipient selection and assignment/course/deadline/remaining-hours content.
- Completed-work suppression using the planner's existing study-session model.
- Durable delivery ledger, concurrent-worker claims, bounded retries, and sanitized failure logs.
- Resend adapter and a cron-friendly or continuous background worker command.
- Automated coverage: **146 tests passed**, including 25 reminder-specific tests.
  Seven existing SQLAlchemy warnings originate from the pre-existing privacy tests.

Deferred until the next implementation session:
1. Configure a Resend account, verified sender domain, and private API key.
2. Deploy/schedule the worker with the same database as the web app.
3. Perform an authorized live-email smoke test with a controlled recipient.
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
clamped to zero. Under the current planner's completion model, an assignment is
complete when those hours cover its estimate; completed assignments are suppressed.
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
webhook ingestion is not implemented. No live-provider verification has been
performed without a configured sender/key.

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
