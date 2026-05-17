#!/bin/bash
# ============================================================
# Ministry Intelligence Platform (MIP) – Setup Script
# One-command deployment on a fresh Ubuntu 22.04 ARM64 VM
# (Oracle Cloud Ampere A1 – Always Free)
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/YOUR_ORG/mip/main/setup.sh | bash
#   # OR after cloning:
#   bash setup.sh
#
# Prerequisites:
#   - Ubuntu 22.04 LTS (ARM64)
#   - Minimum 4 OCPUs / 24 GB RAM (Oracle A1)
#   - Outbound internet access
#   - Domain pointed to this server's public IP
# ============================================================

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${RESET}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
log_step()  { echo -e "\n${BLUE}${BOLD}▶ $*${RESET}"; }
die()       { log_error "$*"; exit 1; }

# ── Constants ─────────────────────────────────────────────────────
MIP_DIR="/opt/mip"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/tmp/mip_setup.log"
RUNNER_VERSION="2.317.0"
RUNNER_ARCH="arm64"

echo -e "${BOLD}"
cat << 'BANNER'
 __  __ ___ ____
|  \/  |_ _|  _ \
| |\/| || || |_) |
| |  | || ||  __/
|_|  |_|___|_|
Ministry Intelligence Platform – Setup
BANNER
echo -e "${RESET}"

log_info "Setup log: $LOG_FILE"

# ── Helper: check running as non-root with sudo ────────────────────
check_user() {
    if [[ $EUID -eq 0 ]]; then
        log_warn "Running as root. Proceeding (not recommended for production — use a sudo user)."
        SUDO=""
    else
        SUDO="sudo"
        if ! sudo -n true 2>/dev/null; then
            die "This script requires sudo privileges. Run: sudo -v first."
        fi
    fi
}

# ── Step 1: System packages ────────────────────────────────────────
install_system_deps() {
    log_step "Step 1/12: Installing system dependencies"
    $SUDO apt-get update -qq >> "$LOG_FILE" 2>&1
    $SUDO apt-get install -y --no-install-recommends \
        curl wget git ca-certificates gnupg lsb-release \
        python3 python3-pip python3-venv \
        certbot age stress-ng \
        gettext-base jq postgresql-client \
        ufw unattended-upgrades \
        >> "$LOG_FILE" 2>&1
    log_info "System packages installed."
}

# ── Step 2: Docker ─────────────────────────────────────────────────
install_docker() {
    log_step "Step 2/12: Installing Docker CE + Docker Compose v2"
    if command -v docker &>/dev/null; then
        log_info "Docker already installed: $(docker --version)"
        return
    fi
    # Official Docker install script (ARM64 safe)
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh >> "$LOG_FILE" 2>&1
    $SUDO bash /tmp/get-docker.sh >> "$LOG_FILE" 2>&1
    $SUDO systemctl enable --now docker >> "$LOG_FILE" 2>&1
    $SUDO usermod -aG docker "${SUDO_USER:-$USER}" || true
    log_info "Docker installed: $(docker --version)"
    log_info "Docker Compose: $(docker compose version)"
}

# ── Step 3: Clone / position repo ─────────────────────────────────
setup_repo() {
    log_step "Step 3/12: Positioning repository"
    if [[ "$SCRIPT_DIR" != "$MIP_DIR" ]]; then
        if [[ -d "$MIP_DIR" ]]; then
            log_info "MIP directory already exists at $MIP_DIR. Pulling latest..."
            cd "$MIP_DIR" && git pull --ff-only >> "$LOG_FILE" 2>&1 || log_warn "git pull failed; continuing with existing files."
        else
            read -rp "Enter your GitHub repository URL (e.g. https://github.com/ORG/mip): " REPO_URL
            $SUDO git clone "$REPO_URL" "$MIP_DIR" >> "$LOG_FILE" 2>&1
            $SUDO chown -R "${SUDO_USER:-$USER}:${SUDO_USER:-$USER}" "$MIP_DIR"
        fi
        cd "$MIP_DIR"
    else
        cd "$SCRIPT_DIR"
        MIP_DIR="$SCRIPT_DIR"
    fi
    log_info "Working directory: $(pwd)"
}

# ── Step 4: Environment variables ─────────────────────────────────
setup_env() {
    log_step "Step 4/12: Configuring environment variables"
    if [[ -f .env ]]; then
        log_info ".env file already exists."
        read -rp "Overwrite existing .env? [y/N] " OVERWRITE
        [[ "${OVERWRITE,,}" != "y" ]] && { log_info "Keeping existing .env."; return; }
    fi

    cp .env.example .env

    echo ""
    log_info "You must fill in the .env file. Opening with nano..."
    log_warn "Required fields: POSTGRES_PASSWORD, SUPERSET_SECRET_KEY, NOCODB_JWT_SECRET,"
    log_warn "  SUPERSET_ADMIN_PASSWORD, NOCODB_ADMIN_PASSWORD, DOMAIN, and API keys."
    echo ""
    read -rp "Press Enter to open .env in nano (or Ctrl+C to configure manually later)..."
    nano .env

    # Validate critical fields
    source .env 2>/dev/null || true
    local missing=()
    for var in POSTGRES_PASSWORD SUPERSET_SECRET_KEY NOCODB_JWT_SECRET DOMAIN \
                SUPERSET_ADMIN_PASSWORD NOCODB_ADMIN_PASSWORD; do
        if [[ -z "${!var:-}" ]] || [[ "${!var}" == *"CHANGE_ME"* ]]; then
            missing+=("$var")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        log_warn "These .env variables still need to be set: ${missing[*]}"
        log_warn "You must set them before services will start correctly."
    else
        log_info "Environment validation passed."
    fi
}

# ── Step 5: SSL certificates ───────────────────────────────────────
setup_ssl() {
    log_step "Step 5/12: Obtaining SSL certificates (Let's Encrypt)"
    source .env 2>/dev/null || true
    DOMAIN="${DOMAIN:-}"

    if [[ -z "$DOMAIN" ]]; then
        log_warn "DOMAIN not set in .env — skipping SSL. Configure manually with:"
        log_warn "  sudo certbot certonly --standalone -d your.domain.com"
        return
    fi

    if [[ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
        log_info "SSL certificate already exists for $DOMAIN."
        return
    fi

    log_info "Requesting certificate for: $DOMAIN"
    # Stop any service on port 80 temporarily
    $SUDO certbot certonly \
        --standalone \
        --non-interactive \
        --agree-tos \
        --email "admin@${DOMAIN}" \
        -d "$DOMAIN" \
        >> "$LOG_FILE" 2>&1 || {
        log_warn "certbot failed. Check that port 80 is open and $DOMAIN resolves to this IP."
        log_warn "Run manually: sudo certbot certonly --standalone -d $DOMAIN"
        return
    }

    # Auto-renew via systemd timer (certbot package includes this)
    $SUDO systemctl enable --now certbot.timer >> "$LOG_FILE" 2>&1 || true
    log_info "SSL certificate obtained. Auto-renewal enabled."

    # Create certbot webroot for nginx-managed renewal
    $SUDO mkdir -p /var/www/certbot
}

# ── Step 6: Firewall ───────────────────────────────────────────────
setup_firewall() {
    log_step "Step 6/12: Configuring UFW firewall"
    $SUDO ufw --force reset >> "$LOG_FILE" 2>&1
    $SUDO ufw default deny incoming >> "$LOG_FILE" 2>&1
    $SUDO ufw default allow outgoing >> "$LOG_FILE" 2>&1
    $SUDO ufw allow 22/tcp comment 'SSH' >> "$LOG_FILE" 2>&1
    $SUDO ufw allow 80/tcp comment 'HTTP (certbot + redirect)' >> "$LOG_FILE" 2>&1
    $SUDO ufw allow 443/tcp comment 'HTTPS' >> "$LOG_FILE" 2>&1
    $SUDO ufw --force enable >> "$LOG_FILE" 2>&1
    log_info "Firewall configured: 22, 80, 443 open."
}

# ── Step 7: OCI CLI (for backups) ─────────────────────────────────
setup_oci_cli() {
    log_step "Step 7/12: Installing OCI CLI (optional – for Object Storage backups)"
    if command -v oci &>/dev/null; then
        log_info "OCI CLI already installed: $(oci --version 2>/dev/null)"
        return
    fi
    # Install via pip (works on ARM64)
    pip3 install --quiet oci >> "$LOG_FILE" 2>&1 || {
        log_warn "OCI CLI install failed. Backups will be stored locally only."
        log_warn "Install manually: pip3 install oci && oci setup config"
        return
    }
    log_info "OCI CLI installed. Configure with: oci setup config"
    log_warn "OCI CLI must be configured before nightly backups will upload to Object Storage."
}

# ── Step 8: Launch Docker services ────────────────────────────────
launch_services() {
    log_step "Step 8/12: Building and launching Docker services"

    # Build automation image
    log_info "Building automation container image..."
    docker compose build --no-cache automation >> "$LOG_FILE" 2>&1
    log_info "Build complete."

    # Start infrastructure first
    log_info "Starting database and Redis..."
    docker compose up -d db redis >> "$LOG_FILE" 2>&1

    log_info "Waiting for PostgreSQL to be ready..."
    for i in $(seq 1 40); do
        if docker compose exec -T db pg_isready -U "${POSTGRES_USER:-ministry}" -d "${POSTGRES_DB:-ministry_db}" &>/dev/null; then
            log_info "PostgreSQL ready."
            break
        fi
        [[ $i -eq 40 ]] && die "PostgreSQL failed to become ready after 200s."
        sleep 5
    done

    # Start remaining services
    log_info "Starting all services..."
    docker compose up -d >> "$LOG_FILE" 2>&1
    log_info "All services started."
}

# ── Step 9: Wait for services + health check ───────────────────────
verify_services() {
    log_step "Step 9/12: Verifying service health"
    log_info "Waiting 90s for services to stabilise..."
    sleep 90

    local failures=0
    check_http() {
        local name="$1" url="$2"
        local code
        code=$(curl -sf --max-time 10 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
        if [[ "$code" =~ ^(200|301|302|307)$ ]]; then
            log_info "  ✅ $name (HTTP $code)"
        else
            log_warn "  ⚠️  $name not responding (HTTP $code) – may still be starting"
            failures=$((failures + 1))
        fi
    }

    check_http "Superset"   "http://localhost:8088/health"
    check_http "NocoDB"     "http://localhost:8080/api/v1/health"
    check_http "Automation" "http://localhost:5000/health"

    if [[ $failures -gt 0 ]]; then
        log_warn "$failures service(s) not healthy yet. Check: docker compose logs"
        log_warn "Services may still be starting. Re-run: docker compose ps"
    else
        log_info "All core services healthy."
    fi
}

# ── Step 10: Run Superset init ─────────────────────────────────────
init_superset() {
    log_step "Step 10/12: Initialising Superset (admin user + dashboards)"
    log_info "Waiting for Superset to be healthy..."
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:8088/health" &>/dev/null; then
            log_info "Superset healthy."
            break
        fi
        [[ $i -eq 30 ]] && { log_warn "Superset not ready after 150s. Run superset-init manually later."; return; }
        sleep 5
    done

    log_info "Running superset-init container..."
    docker compose run --rm superset-init >> "$LOG_FILE" 2>&1 || {
        log_warn "superset-init had warnings (may be expected on first run)."
        log_warn "Check: docker compose logs superset-init"
    }
    log_info "Superset initialised."

    # Init NocoDB forms
    log_info "Initialising NocoDB forms and workspace roles..."
    docker compose exec -T automation python /app/nocodb/init_nocodb.py >> "$LOG_FILE" 2>&1 || {
        log_warn "NocoDB init had warnings. Check: docker compose logs automation"
    }
    log_info "NocoDB configured."
}

# ── Step 11: Cron jobs ─────────────────────────────────────────────
setup_cron() {
    log_step "Step 11/12: Setting up cron jobs"
    local CRON_USER="${SUDO_USER:-$USER}"
    local cron_file="/tmp/mip_crontab"

    # Read existing crontab (ignore error if empty)
    crontab -u "$CRON_USER" -l 2>/dev/null > "$cron_file" || true

    # Remove any existing MIP entries
    grep -v "mip\|keep_alive\|calculate_goals\|MIP" "$cron_file" > "${cron_file}.new" || true
    mv "${cron_file}.new" "$cron_file"

    # Add MIP cron entries
    cat >> "$cron_file" << CRON
# ── MIP Keep-Alive (Oracle VM idle reclaim prevention) ──────────────
0 */12 * * * ${MIP_DIR}/scripts/keep_alive.sh >> /var/log/mip_keepalive.log 2>&1
# ── MIP Goal Calculator (weekly on Monday 04:00 EAT = 01:00 UTC) ────
0 1 * * 1 docker compose -f ${MIP_DIR}/docker-compose.yml exec -T automation python /app/scripts/calculate_goals.py >> /var/log/mip_goals.log 2>&1
# ── MIP SSL Certificate Renewal Check ────────────────────────────────
0 2 * * 0 certbot renew --quiet --deploy-hook "docker compose -f ${MIP_DIR}/docker-compose.yml restart nginx" >> /var/log/mip_ssl_renew.log 2>&1
CRON

    crontab -u "$CRON_USER" "$cron_file"
    rm -f "$cron_file"
    log_info "Cron jobs installed:"
    crontab -u "$CRON_USER" -l | grep -v "^#" | grep -v "^$" | while read -r line; do
        log_info "  $line"
    done
}

# ── Step 12: GitHub Actions self-hosted runner ─────────────────────
setup_github_runner() {
    log_step "Step 12/12: GitHub Actions self-hosted runner"
    local RUNNER_DIR="/opt/actions-runner"

    if [[ -f "${RUNNER_DIR}/run.sh" ]]; then
        log_info "GitHub Actions runner already installed at $RUNNER_DIR."
        return
    fi

    echo ""
    log_info "To set up the self-hosted runner, you need a GitHub registration token."
    log_info "Get it from: GitHub → Your Repo → Settings → Actions → Runners → New self-hosted runner"
    echo ""
    read -rp "Enter your GitHub repository URL (e.g. https://github.com/ORG/mip): " RUNNER_REPO_URL
    read -rp "Enter the runner registration token: " RUNNER_TOKEN

    if [[ -z "$RUNNER_TOKEN" ]] || [[ "$RUNNER_TOKEN" == "" ]]; then
        log_warn "No token provided. Skipping runner setup."
        log_warn "Set up manually: https://docs.github.com/en/actions/hosting-your-own-runners"
        return
    fi

    $SUDO mkdir -p "$RUNNER_DIR"
    $SUDO chown "${SUDO_USER:-$USER}:${SUDO_USER:-$USER}" "$RUNNER_DIR"
    cd "$RUNNER_DIR"

    # Download runner
    local RUNNER_FILE="actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
    curl -fsSL \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_FILE}" \
        -o "$RUNNER_FILE" >> "$LOG_FILE" 2>&1
    tar xzf "$RUNNER_FILE" >> "$LOG_FILE" 2>&1
    rm "$RUNNER_FILE"

    # Configure
    ./config.sh \
        --url "$RUNNER_REPO_URL" \
        --token "$RUNNER_TOKEN" \
        --name "oracle-a1" \
        --labels "oracle-a1,self-hosted,linux,ARM64" \
        --work "_work" \
        --unattended >> "$LOG_FILE" 2>&1

    # Install as systemd service
    $SUDO ./svc.sh install "${SUDO_USER:-$USER}" >> "$LOG_FILE" 2>&1
    $SUDO ./svc.sh start >> "$LOG_FILE" 2>&1

    cd "$MIP_DIR"
    log_info "GitHub Actions runner installed and started."
    log_info "Label 'oracle-a1' — workflows will use: runs-on: self-hosted"
}

# ── Print summary ──────────────────────────────────────────────────
print_summary() {
    source .env 2>/dev/null || true
    DOMAIN="${DOMAIN:-YOUR_DOMAIN}"

    echo ""
    echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${GREEN}${BOLD}║         MIP SETUP COMPLETE                               ║${RESET}"
    echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo -e "  ${BOLD}Portal:${RESET}       https://${DOMAIN}"
    echo -e "  ${BOLD}Data Entry:${RESET}   https://${DOMAIN}/nocodb"
    echo -e "  ${BOLD}Dashboards:${RESET}   https://${DOMAIN}/superset"
    echo -e "  ${BOLD}Mind Map:${RESET}     https://${DOMAIN}/drawio"
    echo ""
    echo -e "  ${BOLD}Superset Admin:${RESET}  ${SUPERSET_ADMIN_EMAIL:-admin@ministry.org}"
    echo -e "  ${BOLD}NocoDB Admin:${RESET}    ${NOCODB_ADMIN_EMAIL:-admin@ministry.org}"
    echo ""
    echo -e "  ${BOLD}Useful commands:${RESET}"
    echo -e "    docker compose ps                     # service status"
    echo -e "    docker compose logs -f superset       # tail logs"
    echo -e "    docker compose exec db psql -U ministry ministry_db   # DB shell"
    echo -e "    bash scripts/backup.sh                # manual backup"
    echo -e "    python3 tests/verify_system.py        # end-to-end verification"
    echo ""
    echo -e "  ${BOLD}Setup log:${RESET}    $LOG_FILE"
    echo ""
    echo -e "${YELLOW}Next steps:${RESET}"
    echo -e "  1. Open https://${DOMAIN} and verify the portal loads."
    echo -e "  2. Log into Superset and configure RLS rules per operator handbook."
    echo -e "  3. Register MTN/Airtel webhook callbacks to: https://${DOMAIN}/webhook/payment"
    echo -e "  4. Register WhatsApp webhook to: https://${DOMAIN}/webhook/whatsapp"
    echo -e "  5. Configure GitHub Actions environments (staging/production) with required approvers."
    echo -e "  6. Run: python3 tests/verify_system.py --host https://${DOMAIN}"
    echo ""
}

# ── Main ───────────────────────────────────────────────────────────
main() {
    check_user
    install_system_deps
    install_docker
    setup_repo
    setup_env
    setup_ssl
    setup_firewall
    setup_oci_cli
    launch_services
    verify_services
    init_superset
    setup_cron
    setup_github_runner
    print_summary
}

main "$@"
