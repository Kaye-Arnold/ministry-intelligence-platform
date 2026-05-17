# MIP Operator's Handbook
## Ministry Intelligence Platform v1.0

**Audience:** System administrator, fellowship technical lead.  
**Scope:** Day-to-day operations, maintenance, troubleshooting, disaster recovery.

---

## 1. System Architecture Quick Reference

```
Internet → Nginx (443) → NocoDB (8080)    → PostgreSQL (5432)
                       → Superset (8088)  → PostgreSQL + Redis (6379)
                       → draw.io (8080)
                       → Automation (5000) → PostgreSQL
                                           → MTN/Airtel APIs (outbound)
                                           → WhatsApp Cloud API (outbound)
```

All containers share `mip-net` bridge network. Only Nginx is exposed externally.

---

## 2. Daily Health Checks (Automated)

These run automatically. Check if any fail:

| Check | How | Frequency |
|---|---|---|
| Container health | GitHub Actions deploy workflow | On every deploy |
| Data integrity | `integrity-check.yml` workflow | Weekly (Monday 06:00 EAT) |
| Database backup | `backup-db.yml` workflow | Daily (03:00 EAT) |
| SSL certificate | Certbot systemd timer | Weekly |
| VM keep-alive | `keep_alive.sh` cron | Every 12 hours |

**View GitHub Actions status:** Your Repo → Actions tab.

**Quick manual health check:**
```bash
cd /opt/mip
docker compose ps
curl -sf https://YOUR_DOMAIN/health | python3 -m json.tool
```

---

## 3. User Management

### Add a Data Clerk
1. In NocoDB (https://YOUR_DOMAIN/nocodb):
   - Settings → Team & Auth → Invite Member
   - Email: clerk's email
   - Role: Editor (for Attendance, Outreach, Finance, Donations tables)
2. Share the 1-page "Data Clerk Quick Start" guide (Section 10).

### Add a Team Leader
1. Ensure they have a Google account they will use to log into Superset.
2. Add their email to `user_teams` table:
   ```sql
   -- Via psql or NocoDB
   INSERT INTO user_teams (user_email, team_id, role)
   VALUES ('leader@example.com', 'Family A', 'leader');
   ```
3. In Superset: Security → List Users → find their email after first login → assign `Alpha` role.
4. They can now access https://YOUR_DOMAIN/superset and see only their team's data.

### Remove a User
1. NocoDB: Settings → Team & Auth → remove.
2. PostgreSQL: `DELETE FROM user_teams WHERE user_email = 'email@example.com';`
3. Superset: Security → List Users → delete.

---

## 4. Routine Maintenance

### Monthly (first Monday of month)
```bash
cd /opt/mip

# Pull latest code
git pull --ff-only

# Update Docker images (non-breaking: postgres, redis, nocodb, drawio)
docker compose pull db redis nocodb drawio chrome
docker compose up -d

# Check disk usage
df -h
docker system df

# Prune unused Docker images (safe after verifying services are healthy)
docker image prune -f

# Review SSL expiry
sudo certbot certificates
```

### Semester Change (every ~4 months)
1. Update `CURRENT_SEMESTER` in `.env`:
   ```bash
   nano /opt/mip/.env
   # Change: CURRENT_SEMESTER=2026-S2
   docker compose up -d automation
   ```
2. Add semester goals to the `goals` table:
   ```bash
   docker compose exec db psql -U ministry ministry_db
   ```
   ```sql
   INSERT INTO goals (semester, description, target_value, unit, category) VALUES
     ('2026-S2', 'Average weekly service attendance', 65, 'members', 'Attendance'),
     -- ... add all goals
   ;
   ```
3. In Superset: update the "Semester Goals" dashboard filter to the new semester.

### Weekly (automated, but verify)
- Check GitHub Actions → `Weekly Integrity Check` workflow result.
- If a GitHub Issue titled "Data Integrity Anomalies" is open, address it.
- Verify the Monday morning report was received in the WhatsApp group.

---

## 5. Backup Management

### Verify Backup Ran
```bash
# Check GitHub Actions → "Nightly Database Backup" workflow
# OR check local log:
tail -20 /tmp/mip_backup.log

# List OCI backups:
oci os object list --bucket-name mip-backups --namespace YOUR_NAMESPACE
```

### Manual Backup
```bash
cd /opt/mip
bash scripts/backup.sh
```

### Restore from Backup
```bash
# 1. Download from OCI
oci os object get \
  --bucket-name mip-backups \
  --name "2026/01/backup_20260115_030000.sql.gz.age" \
  --file /tmp/restore.sql.gz.age

# 2. Decrypt (requires private key)
age --decrypt -i ~/mip-backup-private.key /tmp/restore.sql.gz.age \
  | gunzip > /tmp/restore.sql

# 3. Stop write-producing services
docker compose stop automation superset-worker superset-beat

# 4. Restore
docker compose exec -T db psql -U ministry ministry_db < /tmp/restore.sql

# 5. Restart
docker compose up -d

# 6. Verify
python3 tests/verify_system.py
```

---

## 6. Troubleshooting

### Service Won't Start
```bash
docker compose logs --tail=50 SERVICE_NAME
docker compose restart SERVICE_NAME
# If still failing:
docker compose down SERVICE_NAME && docker compose up -d SERVICE_NAME
```

### Database Connection Errors
```bash
# Check PostgreSQL is healthy
docker compose exec db pg_isready -U ministry

# Check connection from automation container
docker compose exec automation python3 -c "
import psycopg2
conn = psycopg2.connect(host='db', user='ministry', password='YOUR_PASS', dbname='ministry_db')
print('Connected OK'); conn.close()
"
```

### Superset Dashboards Not Loading
```bash
# Check Superset logs
docker compose logs --tail=100 superset

# Restart Superset and workers
docker compose restart superset superset-worker superset-beat

# Re-run init if dashboards are missing
docker compose run --rm superset-init
```

### WhatsApp Messages Not Sending
```bash
# Test webhook listener health
curl http://localhost:5000/health

# Check automation logs
docker compose logs --tail=50 automation

# Verify token is set
docker compose exec automation env | grep WHATSAPP

# Test API directly (replace values)
curl -X POST "https://graph.facebook.com/v22.0/YOUR_PHONE_ID/messages" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messaging_product":"whatsapp","to":"YOUR_NUMBER","type":"text","text":{"body":"MIP Test"}}'
```

### Donation Not Processing
```bash
# Check status of a specific donation
docker compose exec db psql -U ministry ministry_db -c \
  "SELECT donation_id, status, transaction_ref, initiated_at FROM donations ORDER BY donation_id DESC LIMIT 10;"

# Manually trigger initiator for a specific donation
docker compose exec automation python /app/scripts/donation_initiator.py --donation-id 123

# Check for stale pending donations
docker compose exec automation \
  python /app/scripts/integrity_check.py --verbose --fix-safe
```

### Nginx 502 Bad Gateway
```bash
# Check which upstream is failing
docker compose ps

# Usually means a backend container is starting up
# Wait 30s and retry, or:
docker compose restart nginx
```

### Disk Space Full
```bash
df -h
docker system df

# Clean Docker artifacts
docker system prune -f
docker volume ls  # identify unused volumes

# Clean old logs
truncate -s 0 /tmp/mip_backup.log
journalctl --vacuum-time=7d
```

---

## 7. Security Operations

### Rotate PostgreSQL Password
```bash
# 1. Generate new password
NEW_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "New password: $NEW_PASS"

# 2. Update in PostgreSQL
docker compose exec db psql -U ministry -c \
  "ALTER USER ministry WITH PASSWORD '$NEW_PASS';"

# 3. Update .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$NEW_PASS/" /opt/mip/.env

# 4. Restart services that use it
docker compose restart nocodb superset superset-worker superset-beat automation
```

### Rotate API Keys (MTN/Airtel/WhatsApp)
```bash
# 1. Regenerate in respective portals
# 2. Update .env:
nano /opt/mip/.env
# 3. Restart automation:
docker compose restart automation
```

### Audit Login Activity
```bash
# Superset login logs
docker compose logs superset | grep "login\|auth\|OAuth"

# NocoDB access
docker compose logs nocodb | grep "signin\|login" | tail -20
```

---

## 8. Superset Configuration Reference

### RLS Rules (Row-Level Security)
After first deploy, configure these in Superset → Security → Row Level Security:

| Table | Filter Clause | Role |
|---|---|---|
| `attendance` | `team_owner IN (SELECT team_id FROM user_teams WHERE user_email = '{{ current_username() }}')` | Alpha |
| `outreaches` | `activity_code IN (SELECT activity_code FROM activities WHERE team_owner IN (SELECT team_id FROM user_teams WHERE user_email = '{{ current_username() }}'))` | Alpha |
| `follow_ups` | `assigned_to IN (SELECT member_id FROM members WHERE email = '{{ current_username() }}')` | Alpha |

### Add a New Dashboard
1. Build the chart in Superset → Charts → + Chart.
2. Connect to the `ministry_db` database.
3. Add chart to a dashboard.
4. Export the dashboard: Dashboard → ··· → Export.
5. Save JSON to `superset/dashboards/export_NEW.json`.
6. Commit and push (CI/CD will deploy).

### Schedule Automated Email Reports
1. Superset → Alerts & Reports → + Report.
2. Select dashboard/chart, set schedule (cron), add recipients.
3. Requires `ALERT_REPORTS` feature flag (already enabled in `superset_config.py`).

---

## 9. Mobile Money Configuration

### MTN MoMo (Production)
1. Go to https://momodeveloper.mtn.com
2. Create API User: note the UUID → set as `MTN_API_USER` in `.env`
3. Generate API Key → set as `MTN_API_KEY`
4. Your Subscription Key → `MTN_SUBSCRIPTION_KEY`
5. Change `MTN_ENVIRONMENT=production` in `.env`
6. Register callback: `https://YOUR_DOMAIN/webhook/payment`
7. Restart: `docker compose restart automation`

### Airtel Money (Production)
1. Go to https://developers.airtel.africa
2. Create app → get `client_id` and `client_secret`
3. Change `AIRTEL_ENVIRONMENT=production`
4. Register callback URL in Airtel developer portal
5. Restart automation container

### Testing (Sandbox)
Keep `MTN_ENVIRONMENT=sandbox` and `AIRTEL_ENVIRONMENT=sandbox` for development.
Use sandbox test phone numbers from each provider's documentation.

---

## 10. Data Clerk Quick Start (One-Page Guide)

**Access:** https://YOUR_DOMAIN → Data Entry tab

### Record Attendance
1. Open "Record Attendance" form.
2. Select **Activity** from dropdown (e.g., "THU-SERV-001 – Thursday Service").
3. For each member, check **Present** or leave unchecked.
4. Add **Arrival Time** if late.
5. Click Submit. *(If you see an error about duplicate records, that member was already recorded.)*

### Log an Outreach
1. Open "Log Outreach" form.
2. Select **Activity Code** (the outreach event).
3. Select **Location** from dropdown.
4. Enter: people reached, salvations, follow-up contacts.
5. Submit.

### Add a Follow-Up Contact
1. Open "Add Follow-Up" form.
2. Enter contact name and phone.
3. Select the **Source Activity** (which outreach they came from).
4. Assign to a leader.
5. Submit.

### Record a Donation
1. Open "Submit Donation" form.
2. Select donor member (or leave blank for guest; enter phone manually).
3. Select **Giving Category** from dropdown.
4. Enter **Amount (UGX)** and select **Payment Method**.
5. Submit. *(The donor will receive a USSD prompt on their phone to confirm.)*

### Record Income/Expense (Finance)
1. Open "Record Income/Expense" form.
2. Select **Type** (Income or Expense).
3. Select **Category**.
4. Enter **Amount** and **Date**.
5. Attach receipt image if available.
6. Submit.

---

## 11. Disaster Recovery Checklist

**VM Unreachable / Corrupted:**

- [ ] Provision new Oracle A1 instance (same region, same Always Free quota)
- [ ] Point domain DNS to new IP
- [ ] Clone repository: `git clone https://github.com/YOUR_ORG/mip /opt/mip`
- [ ] Copy `.env` from backup (stored securely offline)
- [ ] Run `bash setup.sh`
- [ ] Restore latest database backup (Section 6)
- [ ] Verify: `python3 tests/verify_system.py`
- [ ] Re-register GitHub Actions runner (setup.sh step 12)
- [ ] Notify team: system restored

**Expected RTO (Recovery Time Objective): 2–4 hours.**

---

## 12. Contact & Escalation

| Issue | Who to Contact |
|---|---|
| Service outage | System Administrator (SSH access) |
| Data correction | Data Clerk Supervisor |
| Finance discrepancy | Finance Admin |
| API credential renewal (MTN/Airtel) | System Admin + Finance Admin |
| WhatsApp Business account issues | Meta Business Suite → Support |
| Oracle Cloud billing / reclaim | OCI Console → Support Ticket |

---

*MIP v1.0 – Last updated: May 2026*
