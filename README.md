# Dental & Derma Center — Clinic Management System

A web system for a dental and dermatology clinic, in Arabic (right-to-left) and English.
Built with Django (Python). Runs on a laptop with SQLite, and online with PostgreSQL.

## What's in stage 1

| Area | What it does |
|---|---|
| Sign-in | Username + password, locks for 15 minutes after 5 wrong attempts, 8-hour sessions |
| Roles | System admin, clinic manager, doctor, nurse, receptionist, accountant, storekeeper. Each role sees only what it's allowed to (`accounts/roles.py`) |
| Users | Add staff, assign role and branches, doctor details (specialty, department, calendar color), set passwords |
| Branches | Several branches; each user picks their current branch from the top bar |
| Services | Dental and dermatology services with codes, categories, base price, duration, "per tooth" flag |
| Price lists | Standard / insurance / corporate / offer lists, a general discount %, special prices per service, optional branch and dates |
| Packages | Bundles of sessions for one price (e.g. 6 laser sessions) with value and savings calculated |
| Activity log | Every sign-in, failed sign-in, and change: who, what, when, from which IP |

## What's in stage 2

| Area | What it does |
|---|---|
| Patients | File number (P-000001…), contact, insurance/price list, allergies and chronic conditions (red alert everywhere), visit history |
| Booking | Search patient by name/phone/file no., pick doctor and service (duration fills itself), refuses double-booking a doctor |
| Day list | The day's bookings per branch with status tabs and one-click Check in → Start visit → Finish, plus cancel / no-show / restore |
| Calendar | One column per doctor; click an empty spot to book at that time; red line shows the current time |
| Queue screen | Full-screen page for the waiting-room TV: queue numbers and first names only, refreshes every 10 seconds |
| Dashboard | Today's bookings, waiting, with doctor, done |

Status rules: reception can confirm, check in, call the patient in, cancel, mark no-show; only the doctor
(their own patients) or a nurse can finish a visit.

Who can do what:

| Module | View | Edit |
|---|---|---|
| Services | everyone | admin, manager |
| Price lists | admin, manager, reception, accountant | admin, manager, accountant |
| Packages | admin, manager, reception, accountant, doctor | admin, manager |
| Branches | admin, manager | admin |
| Users | admin, manager | admin |
| Activity log | admin | — |
| Patients | everyone except storekeeper | admin, manager, doctor, nurse, reception |
| Appointments | admin, manager, doctor, nurse, reception | book: admin, manager, reception |

## Run it on your computer (Windows)

Needs Python 3.12+.

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py seed_demo
.venv\Scripts\python manage.py runserver
```

Open http://127.0.0.1:8000

### Demo accounts (local testing only)

`seed_demo` creates one account per role. They all use the password in `SEED_PASSWORD`
(default `Clinic@2026`, see `.env.example`):

`admin`, `manager`, `dr.sara`, `dr.omar`, `dr.lina`, `nurse`, `reception`, `accountant`, `store`

Never run `seed_demo` on the live site, and never use these passwords online.

### Tests

```bash
.venv\Scripts\python manage.py test core
```

## Put it online (Render + GitHub)

The project includes `render.yaml` (creates the website + PostgreSQL database) and `build.sh`
(installs, migrates, and creates the first admin from `DJANGO_ADMIN_USERNAME` / `DJANGO_ADMIN_PASSWORD`).

1. Create a free account at github.com and a **private** repository named `clinic-system` (empty, no README).
2. In this folder, upload the code:
   ```bash
   git init
   git add .
   git commit -m "Clinic system"
   git branch -M main
   git remote add origin https://github.com/YOUR-USERNAME/clinic-system.git
   git push -u origin main
   ```
3. Create an account at render.com (sign up with GitHub).
4. Render dashboard → **New** → **Blueprint** → pick `clinic-system` → enter the admin username and a strong password → **Apply**.
5. Wait for the build (about 5 minutes), open the `https://clinic-….onrender.com` link, sign in as that admin.
6. Add your real branches, services, prices and staff (the demo data is never uploaded: `db.sqlite3` is in `.gitignore`).

Updating later: commit and `git push` — Render redeploys by itself.

Before real patient data: move the web service and database to paid plans (the free database expires
after a trial period and has no backups; the free website sleeps when unused), and optionally add your own
domain in Render → Settings → Custom domains (then add it to `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS`).

When `DJANGO_DEBUG=false` the app forces HTTPS, secure cookies and HSTS.

## Project layout

```
config/      settings and main URLs
accounts/    staff users, roles & permissions, sign-in
core/        branches, services, price lists, packages, activity log, dashboard
  i18n.py    all Arabic/English interface text
templates/   pages
static/      styles and scripts
```

## Next stages

3. Clinical records: tooth chart, dermatology photos, prescriptions, treatment plans
4. Finance: invoices, cash boxes, expenses, insurance claims, accounting
5. Warehouse and fixed assets
6. HR: attendance, shifts, payroll, commissions
7. Online booking, WhatsApp reminders, reports, going live
