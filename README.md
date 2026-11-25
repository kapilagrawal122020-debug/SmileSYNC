# SmileSYNC backend

Django REST Framework API for SmileSYNC: a marketplace of dental clinics. Every clinic is an organisation with its own team,
patients, stock and billing, and everything is scoped to the clinic you sign in to. Plain Django + DRF, token auth, SQLite in development.

## Run it

```bash
python -m venv venv
venv\Scripts\activate          # Windows (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # demo clinic with a clinic admin, doctor, assistant, stock, one patient
python manage.py runserver
python manage.py test             # 16 tests covering the flows in the brief
```

Demo logins (after `seed_demo`): clinic admin `9000000001` / `doctor123`, doctor `9000000004` / `doctor123`, assistant `9000000002` / `assist123`,
patient `9111111111` (code login, shown in the response while `DEBUG=1`). **Every login names the clinic** (`"clinic": <id>`), except the platform admin.

## Clinics as organisations

SmileSYNC is a marketplace: many clinics, each its own organisation. Nobody creates a clinic themselves.

| Who | What they are | What they can do |
|-----|---------------|------------------|
| **Super admin** (`super_admin`, a Django superuser) | runs the platform | add and edit clinics, create clinic admins, approve anyone, edit the master lists, read the audit log. Sees counts only, **never patient records** |
| **Clinic admin** (`clinic_admin`) | runs one clinic | its team (add, approve, edit roles, switch off), clinic profile and public page, billing corrections and refunds, stock, documents, broadcasts, dashboard. Can do everything a doctor can |
| Doctor, consultant | treat patients | clinical records. A doctor also reads private notes and reviews hygiene photos |
| Assistant | front desk | patients, appointments, images, hygiene photos, stock in |
| Patient | a patient of one or more clinics | their own records, **in the clinic they signed in to** |

Onboarding a clinic:
1. The super admin adds the clinic in the dashboard (Clinics, or `POST /api/platform/clinics/`), with its location, point of contact and, optionally,
   its first clinic admin (`admin_name`, `admin_phone`, `admin_password`). More admins: `POST /api/platform/clinics/{id}/admins/`.
2. The clinic appears on the marketplace (`GET /api/public/clinics/`) with a web address from its name, such as `/c/smile-care-pune`.
3. Its **team** asks to join: `POST /api/auth/register/` with `role` (doctor, consultant or assistant) and `clinic`. The account is created switched off.
   The **clinic admin** approves it (Team page, or `POST /api/users/{id}/approve/`). Until then login answers 403 "waiting for approval".
   The super admin can approve anyone too.
4. **Patients** register at a clinic with `role: "patient"`, `clinic` and `consent_data`, or are registered by staff. They sign in with a WhatsApp code.

Signing in (all per clinic):
* Staff: `POST /api/auth/login/ {phone, password, clinic}`. The wrong clinic gets the same 401 as a wrong password. The super admin sends no clinic.
* Patients: `POST /api/auth/otp/request/ {phone, clinic}` then `/otp/verify/ {phone, code, clinic}`. A code is only sent to a number that is a patient of that clinic.
* A session (`AuthToken`) belongs to one person **and one clinic**, so the same phone can be a patient at two clinics with two separate sessions,
  and each shows only its own clinic's data. Logging out ends only that session.

Not supported yet: one staff account working at several clinics. Today a staff member belongs to one clinic.

## Try the API in Swagger

Start the server and open **http://localhost:8000/api/docs/** (raw OpenAPI schema: `/api/schema/`).

1. Open `POST /api/auth/login/`, click *Try it out*, send `{"phone": "9000000001", "password": "doctor123", "clinic": 1}` and copy the `token`.
2. Click **Authorize** at the top and enter `Token <the token>`.
3. Every endpoint now works from the page. Swagger remembers the token across reloads.

For the patient app flow use `/api/auth/otp/request/` then `/api/auth/otp/verify/` (the code is in the response while `DEBUG=1`).

## Settings and environment

Settings read environment variables, and a `.env` file next to `manage.py` (copy `.env.example`).
Production is the default: with `DEBUG` unset the app needs `ALLOWED_HOSTS`. `SECRET_KEY` is optional: if you don't set it,
the app creates a random key on first start and saves it in `.secret_key` (ignored by git). Back that file up if you use the admin site.
For local development create a `.env` containing `DEBUG=1`. Never edit `settings.py` on the server.

## Deploy on PythonAnywhere

Needs Python 3.12 or newer (Django 6). Run these in a Bash console on PythonAnywhere:

```bash
git clone https://github.com/kapilagrawal122020-debug/SmileSYNC.git && cd SmileSYNC
mkvirtualenv --python=/usr/bin/python3.13 smilesync-env
pip install -r requirements.txt
cp .env.example .env && nano .env     # DEBUG=0, ALLOWED_HOSTS=<username>.pythonanywhere.com, CORS_ALLOWED_ORIGINS=<frontend url>
python manage.py migrate
python manage.py createsuperuser      # the super admin. Add clinics (and their admins) from the dashboard
python manage.py collectstatic --noinput
```

On the **Web** tab: add a web app with *Manual configuration* and the same Python version, then set

* **Virtualenv**: `/home/<username>/.virtualenvs/smilesync-env`
* **Static files**: URL `/static/` -> `/home/<username>/SmileSYNC/staticfiles`
* **Force HTTPS**: on
* **WSGI file**: replace the contents with

```python
import os, sys

path = "/home/<username>/SmileSYNC"
if path not in sys.path:
    sys.path.insert(0, path)
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

Then press **Reload**. Updating later: `git pull`, `python manage.py migrate`, `collectstatic`, Reload.
The `.env` file stays on the server because git ignores it.

If the site shows Django's `Using the URLconf defined in mysite.urls` 404 page, the web app is still running
PythonAnywhere's sample project: fix the WSGI file (`DJANGO_SETTINGS_MODULE = "config.settings"`, path to `SmileSYNC`).
A web frontend on another domain (Netlify and so on) must be listed in `.env` as `CORS_ALLOWED_ORIGINS=https://your-app.netlify.app`.

If the site shows `502 backend`, open **Web > Log files > Error log**: the last lines name the cause
(a missing `SECRET_KEY` or `ALLOWED_HOSTS`, a wrong WSGI path, or a virtualenv with the wrong Python version).
The default SQLite database and the `media/` folder live in the project folder, which PythonAnywhere keeps between restarts.
Schedule `python manage.py run_job <name>` under the **Tasks** tab (see `messaging/jobs.py`).

## Layout

| App         | What it holds |
|-------------|---------------|
| `accounts`  | User (phone login, roles), clinic-scoped sessions (`AuthToken`), staff password login, patient code login, role helpers in `access.py` |
| `clinic`    | Clinic profile and marketplace page, chairs, document vault (expiry alerts), dashboard |
| `patients`  | Patients, visits, treatments, images, invoices, payments, feedback |
| `inventory` | Item master, usage rules, stock-in, auto-deduct, alerts, monthly count |
| `hygiene`   | Photo-verified checks, doctor review, chair unlock, compliance score |
| `messaging` | WhatsApp sender, message log, broadcasts, reply webhook, scheduled jobs |
| `masterdata` | Master lists (dropdown values) the super admin edits: lists, values, import, export, seed |
| `audit`     | Append-only log of sign-ins, list changes, account changes, history reviews |
| `platform_admin` | The super admin dashboard API (`/api/platform/*`) |

All routes are in `config/urls.py`.

## How access works

Send `Authorization: Token <token>`. Every view lists the roles that may read and write
(`read_roles`, `write_roles` in `accounts/access.py`). The role groups are `OWNER` (clinic admin), `CLINICAL`, `REVIEWERS`, `STAFF`, `ALL`.

* Staff only ever see rows of their own clinic. A patient only sees their own rows in the clinic of their session.
* A related object from another clinic (patient, visit, item...) is rejected on write.
* Private notes: doctors and the clinic admin. Treatment plans: assistants view, clinical roles edit.
* A clinic always keeps at least one active clinic admin. Nobody can turn off, demote or remove themselves.
* Files are never public. Every download is an API endpoint that checks role and clinic.
* The marketplace (`/api/public/clinics/`) is open and only holds what a clinic publishes: name, place, address, phone, email, links, about,
  timings and its doctors' names and specialities. Never the point of contact, internal notes or registration numbers.

## Platform admin (super admin) dashboard

One person per platform (you, the product owner) manages the lists, clinics and accounts. Everything is under `/api/platform/`
and guarded by a single permission, `IsSuperAdmin` (`accounts/access.py`). Anonymous callers get 401. Doctors, consultants,
assistants and patients get 403. The web app shows the **Admin** menu only to this user and sends everyone else away from `/admin`.

* **Create the first admin** on the server: `python manage.py createsuperuser` (phone number and password). That user gets the
  `super_admin` role. Nobody can become one through `/api/users/`, a role change or sign-up. Another admin can be added from
  the dashboard (`POST /api/platform/admins/`).
* **A platform admin cannot read clinic data.** Patients, visits, treatments and invoices return 403 for this role; the overview
  shows counts only.
* **Clinic directory** (`/api/platform/clinics/`, list, add, edit, `?search=`, `?active=`): name, city and a point of contact
  (name and mobile) are required; state, pincode, address, map link, phone, email, registration and GST numbers, the contact's role
  and email, and internal notes are optional. The same name in the same city is refused. Clinics are never deleted, only turned off
  (`is_active`): a turned-off clinic disappears from the sign-up list and its existing staff keep working. The point of contact and
  internal notes are never returned by the clinic-facing `/api/clinics/`, and a clinic's name can only be changed here.
* **Sign-up approval**: `GET /api/platform/users/?pending=true` lists everyone waiting for approval, and the overview counts them. Clinic admins approve
  their own team, so most requests never reach the super admin. Choosing a clinic is not enough to get in: someone must approve the account.
* `GET /api/platform/overview/`, `clinics/`, `users/` (filter, turn on or off, `set-password`),
  `audit/` (filter, search, paged), and the lists below.
* **Master lists**: `GET /api/platform/lists/`, `GET /api/platform/lists/{key}/`, `POST .../{key}/values/`, `PATCH /api/platform/list-values/{id}/`,
  `POST .../{key}/reorder/`, `GET .../{key}/export/?type=csv` (JSON by default), `POST /api/platform/lists/import/?dry_run=1`.
  Rules: a value's **code never changes**, values are **turned off, never deleted**, and values marked `is_system` cannot be turned off
  because the app's logic depends on them. Everyone signed in reads the active values from `GET /api/lists/` (add `?keys=a,b`).
* **Seed**: the 67 lists (1035 values) in `masterdata/seed/master_lists.json` are loaded by `python manage.py migrate`. To load again
  or after adding new lists: `python manage.py seed_lists`. It never overwrites a label you edited.
* The `?format=` query is reserved by Django REST Framework, so exports use `?type=csv`.

## Behaviour worth knowing

* **Patient registration** needs `consent_data`. A duplicate phone in the same clinic is rejected. Phone numbers are stored as 10 digits. A number that is a
  staff account cannot be a patient.
* **Treatment done** (`POST /api/treatments/{id}/complete/`) deducts stock once, using the usage rules
  (`procedure` text matches the rule's procedure, case-insensitive; `units` multiplies the quantity).
  Variable items (alginate trays, gloves) go through `POST /api/treatments/{id}/use-stock/`.
* **Chair** goes `in_use` when a visit starts and `locked` when it completes. It returns to `ready` after the
  4 after-patient hygiene photos are submitted. A locked chair cannot start a visit.
* **Hygiene photo integrity**: server timestamp, SHA-256 hash, and a repeated photo is rejected.
  "Camera only" must be enforced in the mobile app (use the camera API, no gallery picker); a server cannot tell.
* **Advance payments** (`/api/advances/`): money a patient pays before it is billed. It stays as credit on the patient.
  Send `apply_advance: true` when creating an invoice, or call `POST /api/invoices/{id}/apply-advance/`, to pay an invoice
  from it (oldest advance first, split across advances if needed). That creates normal payments with method `advance`, so
  totals, dues and the dashboard need no special case. Advances are never edited. The doctor can refund what is left
  (every refund is its own record) and delete an entry only if it was never used or refunded. `GET /api/patients/{id}/billing/`
  returns billed, paid, due, the advance available and `due_after_advance` (what is left to pay if the unused advance is applied).
  An invoice can also be created with money already paid: `paid_now` (with `paid_method`, `paid_reference`), e.g. Braces 34000
  with `paid_now: 20000` gives paid 20000 and due 14000. If `apply_advance` is also true the advance is taken first. A request
  that fails saves nothing.
* **Compliance score** = approved photos / all photos in the period.
* **Tooth numbers** use FDI two-digit codes: 11 to 48 for adults and 51 to 85 for children. `12 14,16` is saved as `12, 14, 16`;
  anything else (for example 19 or 99) is refused with a message.
* **Medical history** (`GET` and `POST /api/patients/{id}/history/`) is a checklist of codes from the lists `medical-conditions`, `allergies`,
  `flagged-medications` and `habits`. Each review is a new version; earlier ones stay on file. `GET` returns the `alerts` shown in the
  red banner: allergies, flagged medicines and conditions that change treatment. Infections such as hepatitis and HIV are deliberately
  not alerts, because standard precautions apply to everyone and status is confidential.
* **Appointments** have `duration_minutes` (default 30) and a computed `end_at`. A chair or a doctor cannot be in two active visits at once
  (touching end to start is fine). Cancelling can carry a `cancel_reason` from the `cancellation-reasons` list.
  `POST /api/visits/{id}/reschedule/ {"scheduled_at": ...}` moves a visit, re-checks overlaps and resets the reminder.
* **Stock** can go negative if usage is recorded after the shelf is empty. The monthly count fixes it and logs the variance.

## WhatsApp

`messaging/whatsapp.py` sends **template** messages through the Meta Cloud API. Without `WHATSAPP_TOKEN`
it only logs (status `dry_run` in the Message table). To go live:

1. Set `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`.
2. Get these templates approved in Meta (parameters in order):
   `login_otp [code]`, `appointment_reminder [name, clinic, date, time]`, `feedback_request [name, clinic, link]`,
   `daily_oral_tip [tip]`, `doctor_daily_digest [summary]`, `document_expiry_alert [document, days, date]`,
   `stock_alert [low items, expiring items]`, `hygiene_missed_alert [round]`, `low_rating_alert [name, rating, comment]`,
   plus any offer templates you broadcast.
3. Point the Meta webhook at `/api/whatsapp/webhook/`. Replies: `1`/`YES` confirms the next booked visit,
   `2` asks to reschedule, `STOP` withdraws WhatsApp consent.

Messages are only sent to patients with `consent_whatsapp`. Broadcasts run inside the request, which is fine for a
clinic's patient list; move them to a background worker if volume grows.

## Scheduled jobs

`python manage.py run_job <name>` from cron or Task Scheduler. Names and suggested times are in `messaging/jobs.py`:
`reminders`, `digest`, `feedback-requests`, `documents`, `stock`, `hygiene-morning`, `hygiene-evening`, `tips`, `purge-photos`.

## Not built yet (on purpose, to keep it simple)

* Camera and RVG integration: this is a desktop or vendor-SDK job. The API accepts uploads (`POST /api/images/`)
  with a `taken_at` time, so a desktop connector or a watched-folder uploader can post to it.
* Offline sync: the app should queue uploads and send `taken_at`. Server-side, uploads are already independent requests.
* Image compression and thumbnails (needs Pillow), and S3 storage (django-storages).
* One batch and expiry per stock item (the latest delivery). Add a Batch model if clinics need several at once.
* Token auth has no expiry. Replace with JWT or expiring tokens before a public launch.
* Hindi and regional languages: patients have a `language` used for WhatsApp templates; app strings are the app's job.
