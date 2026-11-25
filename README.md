# LibraSphere

Multi-branch library platform: **Django 5.1 + DRF + MySQL + JWT** backend, **React + Vite + Tailwind** frontend.
This is the Phase 0 + Phase 1 build from the architecture plan, evolved from the earlier Athenaeum code.

```
librasphere/
├── backend/   core · accounts · branches · catalog · circulation · billing · notifications · analytics
├── frontend/  public catalog · member area · staff area (Today board, Desk mode, catalog, members, reports)
└── docker-compose.yml   MySQL 8 + Redis for development
```

## What works (Phase 1)
- **Roles:** super admin (Django superuser), branch manager and librarian (per-branch `BranchRole`), member, anonymous visitor
- **Catalog:** Work → Edition → Item (physical copy); resource types with metadata templates; ISBN duplicate detection; public search with format/branch/language/availability filters
- **Circulation:** check-out, check-in, renew, mark lost, one-open-loan-per-copy enforced by the database, policy rows (branch / tier / format, most specific wins), branch closures skipped when computing due dates
- **Holds:** queue per title and pickup branch, ready on the hold shelf with a pickup deadline, expiry, cancel, auto-assign on return
- **Fines & payments:** overdue fines with cap and grace days, lost fees, payments applied oldest-first, waivers (managers), borrowing blocked above a threshold
- **Members:** self-registration (pending → approved by a manager), walk-in registration by staff, QR/barcode card, privacy-first history (past loans are anonymised after 30 days unless the member opts in)
- **Reports:** today board, overdue, most borrowed, new arrivals, membership growth
- **Notifications:** in-app + email for hold ready, due soon, membership approved
- **Frontend:** warm light/dark themes, accessibility panel (text size, easy-reading font, high contrast, reduced motion), skeleton loaders, Desk mode (one scan field)
- OpenAPI schema at `/api/v1/schema/`, Swagger UI at `/api/v1/docs/`

## Run it

### 1. Services (optional — SQLite works for a first look)
```bash
docker compose up -d          # MySQL on 3306, Redis on 6379
```

### 2. Backend
```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt                       # mysqlclient needs MySQL client libs; see its docs
cp .env.example .env                                  # export the variables (nothing auto-loads .env)
# For MySQL:  export DATABASE_URL=mysql://libra:libra@127.0.0.1:3306/librasphere
python manage.py makemigrations core accounts branches catalog circulation billing notifications analytics
python manage.py migrate
python manage.py seed_demo                            # DEV ONLY: 2 branches, 12 titles, one user per role
python manage.py runserver                            # http://127.0.0.1:8000
```
`makemigrations` is a one-time step: commit the generated files. Demo logins (password `ChangeMe123!`):
`admin@`, `manager@` (City Centre), `librarian@` (City Centre), `member@` + `librasphere.test`.
For a real install use `createsuperuser` instead of `seed_demo`.

**MySQL full-text search (optional, recommended past ~10k titles):**
`python manage.py create_fulltext_index` then set `SEARCH_BACKEND=mysql_fulltext`.

**Background jobs:** with `CELERY_EAGER=1` (the dev default) email tasks run inline. In production run
`celery -A config worker -l info` and `celery -A config beat -l info`, or without Celery use cron:
`python manage.py run_nightly` (fines, hold expiry, due reminders, history anonymisation).

### 3. Frontend
```bash
cd frontend
npm install
npm run dev                                           # http://localhost:5173 (proxies /api to Django)
```
Production: set `VITE_API_URL=https://<backend>/api/v1`, add the frontend origin to `CORS_ALLOWED_ORIGINS`
and `CSRF_TRUSTED_ORIGINS`. `vercel.json` handles SPA routing.

### 4. Tests
```bash
cd backend && python manage.py test        # 27 tests: circulation rules, holds, fines, permissions, privacy
```

## Try this first
1. Sign in as `librarian@librasphere.test` → **Staff → Desk**. Scan a member card (type `LS-…`, shown in the member's
   *My library → Library card* tab), then item barcodes (Django admin → Items, or the *Add copies* panel).
2. As `member@…`, reserve a title on its page, then check it in at the desk to watch the hold shelf logic.
3. **Staff → Today** shows what needs doing; **Reports → Overdue** after back-dating a loan in the admin.

## Decisions worth knowing
- **MySQL has no partial unique indexes.** "One open loan per copy" and "one live hold per member per title" use a
  nullable unique `active_key` column (set while open, NULL when closed).
- Holds are filled from copies **at the pickup branch**. Cross-branch transfers and inter-library loans are Phase 3.
- Fines are one overdue row per loan that grows daily until return; payments are a separate append-only ledger
  (`Payment` + `FineAllocation`).
- Lists use page-number pagination. Switch the catalog to cursor pagination when it passes ~100k titles.
- Frontend is plain JavaScript (JSX), not TypeScript. The **ReactBits** library isn't used; the UI is Tailwind with a few
  CSS animations. It can be dropped into `components/` anywhere later.
- "Easy reading" uses Atkinson Hyperlegible with extra spacing. OpenDyslexic can be added via `@fontsource/opendyslexic`.
- The Athenaeum **visitor log** (sign-in/out of people, not books) is not part of this build. It would come back as a
  small *Visits* module if you want footfall counts.

## Not built yet (Phases 2–3)
Reading dashboard, reviews, lists, clubs, suggest-a-book, online/M-Pesa fine payment, bulk ISBN import, label printing,
events, space booking, digital library, transfers, acquisitions, archive, multi-language, SMS/push, analytics charts.

## Security notes
JWT access 15 min / refresh 7 days with rotation + blacklist; login and register throttled; staff actions written to
`AuditLog`; every staff queryset is branch-scoped; members only reach their own data; production settings enforce
HTTPS and refuse to start without `DJANGO_SECRET_KEY`.
