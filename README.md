# SmileSYNC backend

Django REST Framework API for the SmileSYNC clinic app and patient app.
Covers all 8 modules of the developer brief. Plain Django + DRF, token auth, SQLite in development.

## Run it

```bash
python -m venv venv
venv\Scripts\activate          # Windows (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # demo clinic, doctor, assistant, stock, one patient
python manage.py runserver
python manage.py test             # 16 tests covering the flows in the brief
```

Demo logins (after `seed_demo`): doctor `9000000001` / `doctor123`, assistant `9000000002` / `assist123`,
patient `9111111111` (OTP, shown in the response while `DEBUG=1`).

Onboard a real clinic: `python manage.py createsuperuser`, open `/admin/`, add a Clinic, then add its doctor user.
The doctor then creates assistants and consultants through `POST /api/users/`.

## Layout

| App         | What it holds |
|-------------|---------------|
| `accounts`  | User (phone login, 4 roles), staff password login, patient OTP login, role helpers in `access.py` |
| `clinic`    | Clinic profile, chairs, document vault (expiry alerts), dashboard |
| `patients`  | Patients, visits, treatments, images, invoices, payments, feedback |
| `inventory` | Item master, usage rules, stock-in, auto-deduct, alerts, monthly count |
| `hygiene`   | Photo-verified checks, doctor review, chair unlock, compliance score |
| `messaging` | WhatsApp sender, message log, broadcasts, reply webhook, scheduled jobs |

All routes are in `config/urls.py`.

## How access works

Send `Authorization: Token <token>`. Every view lists the roles that may read and write
(`read_roles`, `write_roles` in `accounts/access.py`), following the visibility table in the brief.

* Staff only ever see rows of their own clinic. Patients only see their own rows.
* A related object from another clinic (patient, visit, item...) is rejected on write.
* Private notes are doctor-only. Treatment plans: assistants view, doctor and consultants edit.
* Files are never public. Every download is an API endpoint that checks role and clinic.

## Behaviour worth knowing

* **Registration** needs `consent_data`. Duplicate phone in a clinic is rejected. Phone numbers are stored as 10 digits.
* **Treatment done** (`POST /api/treatments/{id}/complete/`) deducts stock once, using the usage rules
  (`procedure` text matches the rule's procedure, case-insensitive; `units` multiplies the quantity).
  Variable items (alginate trays, gloves) go through `POST /api/treatments/{id}/use-stock/`.
* **Chair** goes `in_use` when a visit starts and `locked` when it completes. It returns to `ready` after the
  4 after-patient hygiene photos are submitted. A locked chair cannot start a visit.
* **Hygiene photo integrity**: server timestamp, SHA-256 hash, and a repeated photo is rejected.
  "Camera only" must be enforced in the mobile app (use the camera API, no gallery picker); a server cannot tell.
* **Compliance score** = approved photos / all photos in the period.
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
