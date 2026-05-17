# Ministry Intelligence Platform (MIP)

> **Zero-cost, self-hosted ministry management and analytics platform for a 50–100 member campus fellowship.**

[![CI](https://github.com/YOUR_ORG/mip/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_ORG/mip/actions/workflows/ci.yml)

---

## Overview

MIP replaces all manual ministry reporting with an automated, data-driven platform:

| Capability | Tool |
|---|---|
| Data entry (attendance, outreach, finance, donations) | NocoDB Community Edition |
| Analytics, dashboards, RLS | Apache Superset |
| Mind maps | draw.io (self-hosted) |
| Automated PDF reports → WhatsApp | Python + WhatsApp Cloud API |
| Mobile money (MTN MoMo / Airtel Money) | Direct API integration |
| Database | PostgreSQL 16 |
| Reverse proxy + portal | Nginx |
| Infrastructure | Docker Compose on Oracle Cloud A1 (Always Free) |
| CI/CD | GitHub Actions (self-hosted runner) |

**Total cost: $0.00/month forever.**

---

## Quick Start (Fresh Server)

```bash
# 1. Clone repository on your Oracle A1 VM
git clone https://github.com/YOUR_ORG/mip.git /opt/mip
cd /opt/mip

# 2. Run the automated setup script
bash setup.sh
```

`setup.sh` handles: Docker install, SSL certificates (Let's Encrypt), environment configuration,
service launch, Superset bootstrap, NocoDB form setup, cron jobs, and GitHub Actions runner.

---

## Project Structure

```
mip/
├── docker-compose.yml          # All services
├── .env.example                # Environment variable template
├── setup.sh                    # One-command deployment script
├── nginx/
│   ├── nginx.conf              # Global Nginx config
│   └── sites-enabled/mip.conf # Virtual host (uses ${DOMAIN} envsubst)
├── superset/
│   ├── superset_config.py      # Superset configuration + Google OAuth + RLS
│   └── dashboards/             # 6 dashboard JSON exports + init script
├── nocodb/
│   └── init_nocodb.py          # API-based NocoDB workspace/form setup
├── scripts/
│   ├── init_db.sql             # Full PostgreSQL schema (idempotent)
│   ├── donation_initiator.py   # MTN MoMo + Airtel Money API
│   ├── webhook_listener.py     # Flask: WhatsApp + payment callbacks
│   ├── generate_report.py      # PDF report generator (ReportLab)
│   ├── integrity_check.py      # Data quality validation
│   ├── calculate_goals.py      # Semester goal progress calculator
│   ├── backup.sh               # pg_dump + age encrypt + OCI upload
│   ├── keep_alive.sh           # Oracle VM idle-reclaim prevention
│   ├── requirements.txt        # Python dependencies
│   └── Dockerfile.automation   # Automation container image
├── portal/
│   └── index.html              # Single-page portal (tab-based UI)
├── tests/
│   └── verify_system.py        # End-to-end system verification
├── .github/workflows/
│   ├── ci.yml                  # Lint + schema + syntax checks
│   ├── deploy-staging.yml      # Auto-deploy on push to develop
│   ├── deploy-prod.yml         # Manual-approval deploy to main
│   ├── backup-db.yml           # Nightly database backup
│   └── integrity-check.yml     # Weekly data integrity validation
└── operator_handbook.md        # Complete operations guide
```

---

## Environment Variables

Copy `.env.example` to `.env` and fill in all values:

```bash
cp .env.example .env
nano .env
```

**Required before first launch:**

| Variable | Description |
|---|---|
| `POSTGRES_PASSWORD` | Strong random password for PostgreSQL |
| `SUPERSET_SECRET_KEY` | 32+ char random string for Superset sessions |
| `NOCODB_JWT_SECRET` | 32+ char random string for NocoDB JWTs |
| `SUPERSET_ADMIN_PASSWORD` | Superset admin account password |
| `NOCODB_ADMIN_PASSWORD` | NocoDB admin account password |
| `DOMAIN` | Your domain (e.g. `ministry.example.com`) |
| `GOOGLE_OAUTH_CLIENT_ID` | For Superset team leader login |
| `GOOGLE_OAUTH_CLIENT_SECRET` | |
| `WHATSAPP_TOKEN` | Permanent WhatsApp Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | From Meta Developer dashboard |
| `WHATSAPP_GROUP_ID` | Target WhatsApp group ID |
| `WHATSAPP_VERIFY_TOKEN` | Random string for Meta webhook verification |
| `PAYMENT_WEBHOOK_SECRET` | HMAC secret for payment callback verification |
| `MTN_API_USER` | MTN MoMo API user UUID |
| `MTN_API_KEY` | MTN MoMo API key |
| `MTN_SUBSCRIPTION_KEY` | MTN MoMo subscription key |
| `AIRTEL_CLIENT_ID` | Airtel Money OAuth client ID |
| `AIRTEL_CLIENT_SECRET` | Airtel Money OAuth client secret |

Generate secrets:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Service URLs (after deployment)

| Service | URL |
|---|---|
| **Portal** | `https://YOUR_DOMAIN` |
| **Data Entry (NocoDB)** | `https://YOUR_DOMAIN/nocodb` |
| **Dashboards (Superset)** | `https://YOUR_DOMAIN/superset` |
| **Mind Map (draw.io)** | `https://YOUR_DOMAIN/drawio` |
| **Health Check** | `https://YOUR_DOMAIN/health` |
| **WhatsApp Webhook** | `https://YOUR_DOMAIN/webhook/whatsapp` |
| **Payment Webhook** | `https://YOUR_DOMAIN/webhook/payment` |

---

## Dashboards

All 6 dashboards are automatically imported into Superset on first run:

| Dashboard | Description | RLS |
|---|---|---|
| Leader Overview | KPIs, weekly trend, outreach summary | No |
| Community & Outreach Map | deck.gl geo map of all outreach locations | No |
| Finance Overview | Income/expense/balance with category breakdowns | No |
| Donations & Giving | Giving by category, payment method, donor frequency | No |
| Team Scorecard | Per-team attendance, outreach, follow-up metrics | **Yes** |
| Semester Goals | Progress bars and actual vs. target charts | No |

**Configuring Row-Level Security** for Team Scorecard:
1. Log into Superset as Admin → Security → Row Level Security
2. Filter clause for `attendance` dataset:
   ```sql
   team_owner IN (SELECT team_id FROM user_teams WHERE user_email = '{{ current_username() }}')
   ```
3. Assign to `Alpha` role.
4. Add a row to `user_teams` for each team leader with their Google email.

---

## WhatsApp Report Triggers

Reports are sent free-of-charge within the 24-hour service conversation window:

| Command (send to group) | Action |
|---|---|
| `SEND REPORT` | Weekly PDF report |
| `SEND MONTHLY REPORT` | Monthly PDF report |
| `SEND SEMESTER REPORT` | Semester PDF report |

The clerk sends the trigger message; the system generates and delivers the PDF within ~60 seconds.

---

## Common Operations

```bash
# Check service status
docker compose ps

# View logs
docker compose logs -f superset
docker compose logs -f automation
docker compose logs -f db

# Restart a service
docker compose restart superset

# Open database shell
docker compose exec db psql -U ministry ministry_db

# Run integrity check manually
docker compose exec automation python /app/scripts/integrity_check.py --verbose

# Update semester goal progress
docker compose exec automation python /app/scripts/calculate_goals.py

# Generate a report manually
docker compose exec automation python /app/scripts/generate_report.py --type weekly

# Run end-to-end verification
python3 tests/verify_system.py

# Manual database backup
bash scripts/backup.sh

# Update the platform
git pull && docker compose build --no-cache automation && docker compose up -d
```

---

## Backup & Recovery

**Nightly backups** run automatically via GitHub Actions at 00:00 UTC.

Backup flow: `pg_dump` → `gzip` → `age encrypt` → `OCI Object Storage`

**Restore from backup:**
```bash
# Download from OCI
oci os object get --bucket-name mip-backups \
  --name 2026/01/backup_20260101_030000.sql.gz.age \
  --file backup.sql.gz.age

# Decrypt
age --decrypt -i ~/mip-backup-key.txt backup.sql.gz.age | gunzip > backup.sql

# Restore
docker compose exec -T db psql -U ministry ministry_db < backup.sql
```

---

## Security Notes

- All external traffic terminates at Nginx with TLS 1.2/1.3
- PostgreSQL and internal services are not exposed outside Docker network
- API keys stored encrypted in PostgreSQL (`pgcrypto`)
- Payment webhooks verified via HMAC-SHA256
- Superset uses Google OAuth; NocoDB uses email/password
- SSH: key-only authentication (configure in `/etc/ssh/sshd_config`)

---

## External API Registration

After deployment, register these webhook URLs:

**WhatsApp (Meta Developer Dashboard):**
- Callback URL: `https://YOUR_DOMAIN/webhook/whatsapp`
- Verify token: value of `WHATSAPP_VERIFY_TOKEN` in `.env`
- Subscribe to: `messages`

**MTN MoMo:**
- Callback URL: `https://YOUR_DOMAIN/webhook/payment`
- Set in MTN Developer Portal under API User configuration

**Airtel Money:**
- Callback URL: `https://YOUR_DOMAIN/webhook/payment`
- Set in Airtel Developer Portal

---

## License

All components are open-source (Apache 2.0, AGPLv3, MIT). No proprietary software.
This repository and configuration: [MIT License](LICENSE).
