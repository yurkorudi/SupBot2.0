# ServerWatch

**Linux server monitoring, actionable Telegram alerts, and a central web dashboard.**

ServerWatch is a self-hosted monitoring application for administrators managing
Linux VPS infrastructure. It collects resource and service health over SSH,
stores metrics in MySQL, and delivers reports and incident notifications to
Telegram groups. A Flask dashboard brings server onboarding, historical charts,
alert thresholds, and administration into one place.

Built with **Python 3.10+ · Flask · SQLAlchemy · MySQL · aiogram 3 · Paramiko**.
Deployment uses Nginx, Gunicorn, and systemd; Docker, Redis, and Node.js are not
required.

> **Language:** the application interface and Telegram messages are currently
> Ukrainian. This README and the migration README are written in English;
> the linked operational guides are currently in Ukrainian.

## Contents

- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [Security and operating model](#security-and-operating-model)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Deployment runbook](#deployment-runbook)
- [Development and validation](#18-development-and-validation)
- [License](#license)

## Capabilities

| Area | What ServerWatch provides |
|---|---|
| Resource monitoring | CPU utilization, RAM, swap, and root filesystem usage collected over SSH |
| Service health | systemd and Docker discovery, per-service monitoring, and scoped restarts |
| Dashboard | Fleet metrics, server tabs, historical charts, and a filterable activity journal |
| Telegram integration | Per-group server binding, scheduled reports, on-demand checks, incident notifications, and administrator acknowledgment |
| Guided onboarding | Per-server Ed25519 keys, a generated client setup script, HTTPS callback, and SSH verification before activation |
| Metric history | Raw samples retained for 24 hours; hourly averages and maxima retained for 90 days |
| Delivery resilience | A database-backed outbox with retries, separate from metric collection |
| Remediation | Persistent swap provisioning, delayed critical RAM recovery, and opt-in CPU reboot with SSH verification |
| Web terminal | Interactive SSH PTY using an independent encrypted key and per-VPS administrator access |
| Remote database backups | Daily or on-demand SQL exports from MySQL, MariaDB, and PostgreSQL into a verified central archive |
| Optional local AI | Short metric explanations through a loopback-only Ollama endpoint; disabled by default |

The default collection interval is **180 seconds**, with scheduled Telegram
reports every **4 hours**. Each VPS can keep that interval, select local times
and weekdays, or turn scheduled reports off. `/report` remains available in
an authorized group. [Configure VPS operations](docs/vps-operations.md).

Reports include a rotating local library of Ukrainian comments, some containing
informal humor and mild profanity. This feature does not require an AI service.
Holiday greetings use the `Europe/Kyiv` timezone and appear once per group on
configured dates. Stale or unavailable metrics do not produce humorous claims
about current load.

## Architecture

Three independent Python processes share a MySQL database:

```mermaid
flowchart LR
    Admin[Administrator] --> Nginx[Nginx]
    Nginx --> Web[Flask / Gunicorn]
    Web <--> DB[(MySQL)]
    Worker[Monitoring worker] <--> DB
    Worker -->|SSH: restricted collector| Clients[Linux servers]
    Bot[Telegram polling / delivery] <--> DB
    Bot <--> Telegram[Telegram groups]
    Clients -->|HTTPS: installation callback| Nginx
```

| Process | Entry point | systemd unit | Responsibility |
|---|---|---|---|
| Web | `run:app` / `app.create_app()` | `serverwatch-web` | Dashboard, authentication, configuration, installation callbacks |
| Bot | `python -m bot.main` | `serverwatch-bot` | Telegram commands, callbacks, and outbox delivery |
| Monitor | `python -m monitoring.worker` | `serverwatch-monitor` | SSH collection, alerts, scheduled reports, jobs, aggregation, and cleanup |
| Central backup | `/usr/local/sbin/serverwatch-backup` | `serverwatch-backup.timer` | Backup of the ServerWatch database |

The monitor owns scheduling. The web process does not run a scheduler, and
Telegram polling requires no inbound bot port. Gunicorn listens on
`/run/serverwatch/web.sock` behind Nginx. All three application services run as
the unprivileged `serverwatch` operating-system user.

Run **one monitor and one bot instance**. The monitor unit holds a filesystem
lock, while database leases coordinate work. A web or Telegram outage does not
stop the independent monitoring process. Telegram delivery uses at-least-once
semantics: a crash between sending and committing may produce a duplicate.

## Security and operating model

- **Dashboard access:** password authentication plus TOTP, CSRF protection,
  secure session settings, and login throttling.
- **Secret storage:** Fernet encryption for SSH private keys and TOTP secrets;
  one-time installation and Telegram binding secrets stored as HMAC digests.
- **Client access:** a dedicated monitoring account with a forced SSH command,
  restricted key options, and a validated collection protocol.
- **Host identity:** new installations pin SSH host keys through an authenticated
  HTTPS callback; existing connections can use verified `known_hosts` entries.
  Changed host keys are rejected.
- **Database privileges:** separate runtime DML and migration DDL accounts;
  MySQL listens only on loopback in the deployment below.
- **Service isolation:** systemd units use filesystem restrictions and run
  without local privilege escalation.

The generated installer needs root or sudo on the client during setup.
Resource collection runs with the monitor account's privileges. Optional
Docker inventory, swap, reboot, and database exports use specific sudo rules.
The web terminal uses a dedicated unprivileged client account.

See [SECURITY.md](SECURITY.md) for the detailed trust boundaries and operational
constraints. Back up the original `ENCRYPTION_KEY` separately from database
dumps: replacing it makes existing encrypted credentials unreadable.

## Repository layout

```text
app/                    Flask factory, routes, models, services, templates, assets
bot/                    aiogram handlers, keyboards, and backend integration
monitoring/             Worker, SSH dispatcher, remote collector, and retention
migrations/             Flask-Migrate / Alembic revisions
deploy/                 systemd, Nginx, and restricted sudoers configuration
scripts/                Environment setup, administration, backup, and client setup
tests/                  Application, security, migration, and deployment checks
docs/                   Operational guides and deployment audit notes
run.py                  Flask / Gunicorn application entry point
.env.example            Supported environment variables without real credentials
requirements.txt        Runtime dependencies
requirements-dev.txt    Development and validation dependencies
```

## Documentation

| Reference | Scope |
|---|---|
| [Deployment runbook](#deployment-runbook) | Fresh Ubuntu installation, networking, backups, recovery, and upgrades |
| [Client onboarding](docs/onboarding.md) | Setup wizard, client installation, activation states, and retry behavior |
| [HTTPS on a public IP](docs/ip-https.md) | Public access without a domain and certificate renewal |
| [Security notes](SECURITY.md) | Authentication, SSH restrictions, secrets, and delivery guarantees |
| [Deployment audit](docs/deployment-audit.md) | Implementation checks and the limits of automated validation |
| [Migration README](migrations/README) | Revision history and schema maintenance workflow |
| [VPS operations](docs/vps-operations.md) | Reports, recovery, service restarts, terminal access, and remote backup restore |

## Deployment runbook

This runbook targets a **fresh Ubuntu Server 22.04 or 24.04 LTS VPS, Python
3.10+, and MySQL 8**. Python 3.10 compatibility is intentional; Ruff targets
`py310`, and UTC timestamps use `datetime.timezone.utc`.

Commands below run in Bash on the **central VPS** as your sudo administrator
unless explicitly marked as local-computer or client-server commands. Replace
all `YOUR_*` placeholders. The paths assume `/opt/serverwatch` and
`/etc/serverwatch`.

Choose the access method before generating the environment:

| Mode | Dashboard access | Setup wizard callback |
|---|---|---|
| SSH tunnel | Local browser → SSH → VPS loopback HTTP | Tunnel-only access cannot receive client callbacks; the wizard additionally requires public HTTPS |
| Domain HTTPS | Public HTTPS through Nginx | Supported with a trusted certificate and reachable `PUBLIC_BASE_URL` |
| Public-IP HTTPS | Public HTTPS without a domain | Follow the dedicated IP HTTPS guide after section 9 |

**Validation status:** the previous deployment audit did not execute a complete
installation on a fresh Ubuntu VPS. Automated checks use SQLite and mocked SSH
or HTTPS; they do not establish that MySQL, systemd, Nginx, certificates, or
Telegram work in your environment. Complete the acceptance checks in section 13.

## 1. Runtime architecture and compatibility

Application configuration lives in `app/config.py`, extensions in
`app/extensions.py`, and schema definitions in `app/models/`. There is no root
`config.py`. Runtime dependencies are in `requirements.txt`; Gunicorn is installed
only on Linux. Chart.js and its license are included in the repository.

Use the entry points from the architecture table above. Never start a second
monitor manually alongside the systemd unit.

## 2. Preflight checks

Confirm the operating system, available resources, sudo access, and Python
version before proceeding. Do not enable shell tracing (`set -x`) when handling
secrets.

```bash
cat /etc/os-release
uname -m
free -h
df -h /
id
sudo -v
python3 --version
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ is required"'
```

Set your deployment parameters. Re-export them after reconnecting.
`SSH_PORT` is the central VPS's existing SSH port. For either public HTTPS method,
set `ACCESS_MODE=https`; set `DOMAIN` only when using a domain.

These are shell deployment parameters. The application does not read `APP_ENV`
or `DOMAIN` from its runtime environment.

```bash
export REPO_URL='https://github.com/YOUR_ORG/YOUR_REPOSITORY.git'
export VPS_IP='YOUR_VPS_PUBLIC_IP'
export SSH_PORT='22'
export SSH_ADMIN='YOUR_EXISTING_SUDO_LOGIN'
export ADMIN_IP='YOUR_CURRENT_PUBLIC_IP'
export ACCESS_MODE='tunnel'
# For public HTTPS: export ACCESS_MODE='https'
# For domain HTTPS only: export DOMAIN='YOUR_REAL_DOMAIN'
```

## 3. System packages

Update the host. If a reboot is required, schedule it now and reconnect before
continuing.

```bash
sudo apt update
sudo apt upgrade -y
if test -f /var/run/reboot-required; then cat /var/run/reboot-required; fi
```

Install runtime and administration tools, then enable time synchronization
for TOTP. The domain HTTPS branch uses the packaged Certbot; the public-IP branch
has its own instructions.

```bash
sudo apt install -y python3 python3-venv python3-dev build-essential libffi-dev \
  libssl-dev git openssh-client mysql-server nginx certbot python3-certbot-nginx \
  ufw fail2ban curl ca-certificates openssl gzip tar gnupg util-linux acl
sudo timedatectl set-ntp true
sudo systemctl enable --now mysql nginx
```

Verify the installed tools and running services. Resolve MySQL or Nginx
startup failures before continuing.

```bash
python3 --version
git --version
mysql --version
sudo nginx -v
certbot --version
sudo ufw version
fail2ban-client --version
command -v flock mysqldump gzip openssl
sudo timedatectl status
sudo systemctl status mysql nginx --no-pager
```

## 4. Service account, directories, and virtual environment

Create the dedicated account and clone into an empty application directory.
These commands are for a first installation, not for replacing an existing
checkout.

```bash
sudo adduser --system --group --no-create-home --home /opt/serverwatch \
  --shell /usr/sbin/nologin serverwatch
sudo install -d -o serverwatch -g serverwatch -m 0755 /opt/serverwatch
sudo -u serverwatch bash -c 'umask 022; git clone "$1" /opt/serverwatch' bash "$REPO_URL"
sudo install -d -o serverwatch -g serverwatch -m 0700 /opt/serverwatch/instance
sudo install -d -o root -g serverwatch -m 0750 /etc/serverwatch
cd /opt/serverwatch
```

Confirm the checkout and expected entry points before installing dependencies.

```bash
sudo -u serverwatch git -C /opt/serverwatch status --short
ls -l run.py requirements.txt .env.example app/config.py migrations/env.py
ls -l bot/main.py monitoring/worker.py deploy/serverwatch-web.service
```

Create the virtual environment and install runtime requirements. All service
processes use this interpreter; system-wide `sudo pip` is unnecessary.

```bash
sudo -u serverwatch python3 -m venv /opt/serverwatch/.venv
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip install --upgrade pip
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip install -r /opt/serverwatch/requirements.txt
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip check
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -c 'import flask, flask_migrate, sqlalchemy, pymysql, aiogram, paramiko, pyotp, cryptography; print("Python imports: OK")'
/opt/serverwatch/.venv/bin/gunicorn --version
```

For interactive work, activate the environment with
`source /opt/serverwatch/.venv/bin/activate`. The absolute paths used here make
activation unnecessary for the remaining deployment steps.

## 5. MySQL configuration

Bind both MySQL protocols to loopback. The small buffer pool below is an
initial configuration for a small VPS; adjust it to your workload. Validate the
configuration before restarting MySQL.

```bash
sudo tee /etc/mysql/mysql.conf.d/serverwatch.cnf >/dev/null <<'EOF'
[mysqld]
bind-address=127.0.0.1
mysqlx-bind-address=127.0.0.1
innodb_buffer_pool_size=128M
max_connections=30
EOF
sudo mysqld --validate-config
sudo systemctl restart mysql
sudo systemctl enable mysql
sudo systemctl is-active mysql
sudo systemctl is-enabled mysql
sudo systemctl status mysql --no-pager
```

Ubuntu commonly uses socket authentication for the MySQL root account.
Verify access and listeners, then open the MySQL console.

```bash
sudo mysql --execute "SELECT VERSION(), CURRENT_USER(); SHOW VARIABLES WHERE Variable_name IN ('bind_address','mysqlx_bind_address','port');"
sudo ss -lntp '( sport = :3306 or sport = :33060 )'
sudo mysql
```

Run this SQL in the MySQL console; `EXIT` returns to Bash. A root password is
not required for the application's database accounts.

```sql
SELECT user, host, plugin FROM mysql.user WHERE user = 'root';
SHOW VARIABLES LIKE 'character_set_server';
EXIT;
```

Ports 3306 and 33060 must listen only on `127.0.0.1`, never on `0.0.0.0`,
`*`, or `[::]`. Do not expose them through the firewall.

## 6. Environment and database accounts

Create a Telegram bot with `/newbot` in BotFather and store its token privately.
Keep the bot service stopped until the token and administrator ID are configured.
Check that the database and accounts do not already exist. If they do, use the
upgrade or recovery workflow; do not regenerate secrets for an existing database.

```bash
sudo mysql --execute "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='serverwatch'; SELECT user,host FROM mysql.user WHERE user IN ('serverwatch','serverwatch_migrate');"
```

Generate the initial environment. The script privately prompts for the token
and numeric administrator ID; the ID can be supplied later. It generates random
database passwords and `SECRET_KEY`, creates a Fernet key, applies restricted file
permissions, and refuses to overwrite an existing configuration.

```bash
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/setup_environment.py --access "$ACCESS_MODE"
sudo stat -c '%U:%G %a %n' /etc/serverwatch/serverwatch.env /etc/serverwatch/migration.env /etc/serverwatch/bootstrap.sql
```

Import the bootstrap SQL through the root socket, then check grants and the
runtime account. The database uses `utf8mb4` and `utf8mb4_unicode_ci`.
`serverwatch@127.0.0.1` receives SELECT/INSERT/UPDATE/DELETE;
`serverwatch_migrate@127.0.0.1` receives schema privileges only on `serverwatch.*`.
Runtime services never use the root database account.

```bash
sudo bash -c '/usr/bin/mysql --protocol=SOCKET --user=root < /etc/serverwatch/bootstrap.sql'
sudo mysql --execute "SHOW CREATE DATABASE serverwatch; SHOW GRANTS FOR 'serverwatch'@'127.0.0.1'; SHOW GRANTS FOR 'serverwatch_migrate'@'127.0.0.1';"
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py check-db
```

After `check-db` succeeds, delete the one-time SQL file. Keep `migration.env`.
The check prints connection status without passwords.

```bash
sudo rm -- /etc/serverwatch/bootstrap.sql
```

### Runtime configuration reference

The application reads the following values from its environment:

| Variable | Purpose / default |
|---|---|
| `PUBLIC_BASE_URL` | Public HTTPS origin of the central panel; required for setup callbacks, without a path, credentials, query, or fragment |
| `SECRET_KEY` | Session signing and HMAC secrets; the generator creates 64 random hex characters |
| `ENCRYPTION_KEY` | Fernet key for SSH private keys and TOTP secrets; preserve across upgrades and restores |
| `DATABASE_URL` | Runtime SQLAlchemy URI, e.g. `mysql+pymysql://serverwatch:PASSWORD@127.0.0.1/serverwatch?charset=utf8mb4` |
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_ADMIN_ID` | Positive numeric Telegram user ID allowed to bind groups and perform administrative actions |
| `SSH_KNOWN_HOSTS` | Legacy connection trust store; default `/etc/serverwatch/known_hosts` |
| `SSH_TIMEOUT` | SSH connection timeout in seconds; default `12` |
| `MONITOR_CONCURRENCY` | Concurrent checks; default `2`, clamped to `1–4` |
| `SESSION_COOKIE_SECURE` | `true` for HTTPS; `false` only for loopback HTTP through the SSH tunnel |
| `AI_ENABLED` | Optional local AI explanations; default `false` |
| `AI_URL` | Loopback Ollama endpoint; default `http://127.0.0.1:11434/api/generate` |
| `AI_MODEL` | Optional Ollama model; default `llama3.2:1b` |

The migration helper alone reads the separate migration `DATABASE_URL`.
Database passwords in custom URIs require percent-encoding; the generator handles
this automatically. Do not run a local LLM on a 1 GB VPS; standard reports work
without it.

| Generated file | Owner / mode | Role |
|---|---|---|
| `/etc/serverwatch/serverwatch.env` | `root:serverwatch` / `0640` | Runtime configuration for all three services |
| `/etc/serverwatch/migration.env` | `root:root` / `0600` | Migration database credentials |
| `/etc/serverwatch/bootstrap.sql` | `root:root` / `0600` | One-time database/account creation; remove after verification |

Create the legacy trust-store file if it is absent; preserve any existing host
records. New clients use database-pinned host keys, but configuration backups
also include this file.

```bash
if ! sudo test -e /etc/serverwatch/known_hosts; then
  sudo install -o root -g serverwatch -m 0640 /dev/null /etc/serverwatch/known_hosts
fi
```

Edit runtime configuration with `sudoedit`. systemd and python-dotenv read
these files: **do not execute them with `source` or `. file`**. Keep real
credentials out of Git and terminal output; `.env.example` is a reference.

```bash
sudoedit /etc/serverwatch/serverwatch.env
```

If the administrator ID is unknown, send your bot a private message from the
intended administrator account. With polling stopped and no webhook or competing
bot process, retrieve the sender ID and enter it as `TELEGRAM_ADMIN_ID`.
Verify the numeric ID belongs to you. A username or mention does not authorize
administrative actions; the bot requires a token and a positive ID.

```bash
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py telegram-id
sudoedit /etc/serverwatch/serverwatch.env
```

## 7. Migrations and administrator account

Apply the committed migrations. Do not run `db init`, `db migrate`,
`db.create_all`, or `stamp head` as part of production installation.

The initial revision is `c99481d201d8`; `4e2c9d8f610a` adds guided onboarding.
The upgrade helper reads the migration credentials as root, then drops operating
system privileges before invoking Flask CLI.

```bash
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py upgrade
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
sudo mysql --execute "SHOW TABLES FROM serverwatch; SELECT version_num FROM serverwatch.alembic_version;"
```

Check that the schema revision and expected tables exist. Create the web
administrator interactively; the password is entered twice and must contain at
least 14 characters. Do not pass it as a command-line argument.

```bash
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py create-admin
```

Complete TOTP enrollment in the browser after the first password login.
Developers create new revisions against a separate development database, review
the generated operations, and commit the revision. See the migration README.

## 8. systemd services

The following definitions mirror the tracked unit files. Install the files
from `deploy/` rather than maintaining separate copies. The web unit uses the
`www-data` group for socket access and the supplementary `serverwatch` group for
configuration access. systemd recreates `/run/serverwatch` at service start.
Keep Gunicorn's control socket disabled with `--no-control-socket`.

### deploy/serverwatch-web.service

<!-- unit:deploy/serverwatch-web.service -->
```ini
[Unit]
Description=ServerWatch web administration
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
User=serverwatch
Group=www-data
SupplementaryGroups=serverwatch
WorkingDirectory=/opt/serverwatch
EnvironmentFile=/etc/serverwatch/serverwatch.env
ExecStart=/opt/serverwatch/.venv/bin/gunicorn --workers 1 --threads 2 --bind unix:/run/serverwatch/web.sock --no-control-socket --umask 7 --timeout 120 --access-logfile - --error-logfile - run:app
RuntimeDirectory=serverwatch
RuntimeDirectoryMode=0750
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/serverwatch/instance

[Install]
WantedBy=multi-user.target
```

### deploy/serverwatch-bot.service

<!-- unit:deploy/serverwatch-bot.service -->
```ini
[Unit]
Description=ServerWatch Telegram polling and delivery
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
User=serverwatch
Group=serverwatch
WorkingDirectory=/opt/serverwatch
EnvironmentFile=/etc/serverwatch/serverwatch.env
ExecStart=/opt/serverwatch/.venv/bin/python -m bot.main
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/serverwatch/instance

[Install]
WantedBy=multi-user.target
```

### deploy/serverwatch-monitor.service

<!-- unit:deploy/serverwatch-monitor.service -->
```ini
[Unit]
Description=ServerWatch SSH monitoring engine
After=network-online.target mysql.service
Wants=network-online.target

[Service]
Type=simple
User=serverwatch
Group=serverwatch
WorkingDirectory=/opt/serverwatch
EnvironmentFile=/etc/serverwatch/serverwatch.env
ExecStart=/usr/bin/flock --no-fork --nonblock /opt/serverwatch/instance/monitor.lock /opt/serverwatch/.venv/bin/python -m monitoring.worker
Restart=on-failure
RestartSec=5
TimeoutStopSec=180
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/serverwatch/instance

[Install]
WantedBy=multi-user.target
```

Install and validate the three units, then enable and start them. Finish the
Telegram configuration before starting the bot.

```bash
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/serverwatch-web.service /etc/systemd/system/serverwatch-web.service
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/serverwatch-bot.service /etc/systemd/system/serverwatch-bot.service
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/serverwatch-monitor.service /etc/systemd/system/serverwatch-monitor.service
sudo systemd-analyze verify /etc/systemd/system/serverwatch-web.service /etc/systemd/system/serverwatch-bot.service /etc/systemd/system/serverwatch-monitor.service
sudo systemctl daemon-reload
sudo systemctl enable serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl start serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl status serverwatch-web serverwatch-bot serverwatch-monitor --no-pager
sudo systemctl is-enabled serverwatch-web serverwatch-bot serverwatch-monitor
```

Inspect each journal and test the Unix socket as the Nginx user. The login
request should return HTTP 200.

```bash
sudo journalctl -u serverwatch-web -n 100 --no-pager
sudo journalctl -u serverwatch-bot -n 100 --no-pager
sudo journalctl -u serverwatch-monitor -n 100 --no-pager
sudo ls -ld /run/serverwatch
sudo ls -l /run/serverwatch/web.sock
sudo -u www-data curl --unix-socket /run/serverwatch/web.sock -I http://localhost/login
```

## 9. Firewall, SSH, and Fail2ban

Before enabling UFW, confirm the current SSH port, an allowed source address,
a second working session, and access to the provider recovery console. Check the
provider firewall too. Keep the current session open throughout these changes;
do not change the SSH port at the same time.

```bash
sudo sshd -T | head -n 30
sudo ss -lntp
printf 'Allow SSH from %s on TCP %s\n' "$ADMIN_IP" "$SSH_PORT"
sudo ufw allow from "$ADMIN_IP" to any port "$SSH_PORT" proto tcp
sudo ufw status numbered
sudo ufw show added
```

If your administrator IP changes frequently, an explicit alternative is
`sudo ufw allow "$SSH_PORT/tcp"`; restrict it later using a stable address or VPN.
Do not remove a working rule before confirming a new connection. Allow web ports
only for public HTTPS, then enable UFW.

```bash
if test "$ACCESS_MODE" = 'https'; then sudo ufw allow 'Nginx Full'; fi
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ufw status verbose
```

From another terminal on your computer, verify a new SSH connection on the
current port. Keep ports 8080, 8000, 3306, and 33060 private. Review pre-existing
rules rather than blindly resetting UFW.

If you currently have only root access, create a sudo administrator and install
their **public** SSH key. Skip this block if a suitable account already works.

```bash
sudo adduser opsadmin
sudo usermod -aG sudo opsadmin
sudo install -d -o opsadmin -g opsadmin -m 0700 /home/opsadmin/.ssh
sudo -u opsadmin nano /home/opsadmin/.ssh/authorized_keys
sudo chmod 0600 /home/opsadmin/.ssh/authorized_keys
```

Verify key-based login as the sudo administrator and run `sudo -v` in the
new session. Only then disable password and root login. Validate before reload
and keep the old session open.

```bash
sudo tee /etc/ssh/sshd_config.d/00-serverwatch-hardening.conf >/dev/null <<'EOF'
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo sshd -t
sudo systemctl reload ssh
```

Verify a fresh SSH login again, then enable Fail2ban for the actual SSH port.
Its systemd backend reads the journal.

```bash
printf '[sshd]\nenabled=true\nbackend=systemd\nport=%s\nmaxretry=5\nfindtime=10m\nbantime=15m\n' "$SSH_PORT" | sudo tee /etc/fail2ban/jail.d/serverwatch.local >/dev/null
sudo fail2ban-client -t
sudo systemctl enable --now fail2ban
sudo systemctl restart fail2ban
sudo fail2ban-client status sshd
```

Optionally cap journald disk usage. These quotas apply to the entire VPS,
not just ServerWatch.

```bash
sudo install -d -m 0755 /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\nRuntimeMaxUse=30M\n' | sudo tee /etc/systemd/journald.conf.d/serverwatch.conf >/dev/null
sudo systemctl restart systemd-journald
```

## 10. Nginx and access methods

Install the shared proxy snippet and rate-limit configuration. The limits file
belongs in the Nginx `http` context, normally included through `conf.d`, rather
than inside a `server` block. POST requests to `/login` and `/totp` are limited to
5 per minute with a burst of 5; excess requests receive HTTP 429. Tunnel users
share the loopback source address.

The proxy overwrites forwarded address/protocol headers. Gunicorn remains on its
Unix socket. Choose **one** of the access branches below. The symlink commands
assume a fresh Nginx installation.

```bash
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-proxy.conf /etc/nginx/snippets/serverwatch-proxy.conf
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-limits.conf /etc/nginx/conf.d/serverwatch-limits.conf
```

### 10A. SSH tunnel

Use `ACCESS_MODE=tunnel` and `SESSION_COOKIE_SECURE=false` for loopback HTTP
inside SSH. Do not open port 8080 in the firewall. After reload, expect HTTP 200
and a listener bound only to `127.0.0.1:8080`. This branch does not provide the
public HTTPS endpoint required by the client setup wizard.

```bash
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-tunnel.conf /etc/nginx/sites-available/serverwatch
if test -L /etc/nginx/sites-enabled/default; then sudo unlink /etc/nginx/sites-enabled/default; fi
sudo ln -sfn /etc/nginx/sites-available/serverwatch /etc/nginx/sites-enabled/serverwatch
sudo nginx -t
sudo systemctl reload nginx
curl -I http://127.0.0.1:8080/login
sudo ss -lntp '( sport = :8080 )'
```

Run this command on **your local computer** with your real SSH details and
leave the terminal open. Open `http://127.0.0.1:18080/login` in the browser.
The administrator's SSH account must permit TCP forwarding; the restricted client
monitoring account is not an administrative tunnel account.

```bash
ssh -p YOUR_SSH_PORT -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18080:127.0.0.1:8080 YOUR_SUDO_LOGIN@YOUR_VPS_PUBLIC_IP
```

### 10B. Domain HTTPS

Skip this branch when using a tunnel or public-IP HTTPS. Point the domain's DNS
A record at the VPS; any AAAA record must also be correct. Set
`SESSION_COOKIE_SECURE=true` through `sudoedit` before restarting the web service.
`DOMAIN` must be a plain hostname without a scheme or path.

```bash
export DOMAIN='YOUR_REAL_DOMAIN'
getent ahosts "$DOMAIN"
sudoedit /etc/serverwatch/serverwatch.env
sudo systemctl restart serverwatch-web
sed "s/monitor.example.com/$DOMAIN/g" /opt/serverwatch/deploy/nginx.conf | sudo tee /etc/nginx/sites-available/serverwatch >/dev/null
if test -L /etc/nginx/sites-enabled/default; then sudo unlink /etc/nginx/sites-enabled/default; fi
sudo ln -sfn /etc/nginx/sites-available/serverwatch /etc/nginx/sites-enabled/serverwatch
sudo nginx -t
sudo systemctl reload nginx
```

Ensure ports 80 and 443 are reachable through UFW and the provider firewall.
Issue the certificate, enable renewal, and test HTTPS without disabling trust
verification. Do not submit credentials to the initial plain-HTTP site.

```bash
sudo certbot --nginx -d "$DOMAIN" --redirect
sudo nginx -t
sudo systemctl reload nginx
curl -I "https://$DOMAIN/login"
sudo systemctl enable --now certbot.timer
sudo systemctl list-timers certbot.timer --all
sudo certbot renew --dry-run
```

### 10C. HTTPS on a public IP

To use public HTTPS without a domain, follow [docs/ip-https.md](docs/ip-https.md)
after completing section 9 with `ACCESS_MODE=https`. Skip branches 10A and 10B;
the IP guide supplies the appropriate Nginx and certificate-renewal configuration.
After HTTPS is working, continue with section 11.

For any public deployment, expect a trusted HTTPS response and keep
`SESSION_COOKIE_SECURE=true`. Do not bypass certificate warnings.

## 11. First login and client onboarding

Open the selected dashboard URL, enter the administrator password, scan the
QR code in a compatible authenticator, and confirm the six-digit TOTP code.
Verify logout and a subsequent login; the dashboard requires TOTP.

### Three-step client onboarding

The central panel must expose **public HTTPS with a trusted certificate**, and
`PUBLIC_BASE_URL` must point to that origin. The client needs outbound HTTPS to
the panel; the central server needs SSH access to the client. Tunnel-only HTTP
cannot receive the setup callback.

1. **Create a server record.** In the dashboard, select the add-server action.
   Enter a name, public IPv4 address, SSH port, administrative installation login,
   client name, collection interval, and optional systemd units. The installation
   login is `root` or a sudo user, distinct from the background `monitor` account.
   Leave the Telegram group ID empty if it will be filled through `/bind`.
2. **Run the generated script on the client.** ServerWatch generates a unique
   Ed25519 key pair and stores the private key encrypted on the central server.
   Copy or download the complete script and execute it in Linux Bash on the
   client using the installation login. The script contains a one-time secret
   valid for one hour: keep it private and delete saved copies after use.
   The installer supports Ubuntu/Debian with systemd, creates a restricted
   `monitor` account, installs the collector/dispatcher, preserves unrelated
   authorized keys, validates SSH configuration, and submits the public host key
   through HTTPS. The worker must collect real metrics before the state is READY.
3. **Bind a Telegram group.** Add the bot to the group. From the configured
   administrator account, send `/start`, then `/bind YOUR_15_DIGIT_CODE` in one
   message. This separate code is one-time, valid for 24 hours, and stored only
   as an HMAC digest. Successful binding activates the server. A group may
   contain multiple servers.

| Command | Behavior |
|---|---|
| `/start` | Show connection or binding guidance |
| `/bind CODE` | Authorize a server for the current group; administrator only |
| `/status` | Return the latest stored server state |
| `/report` | Return available reports and request a fresh collection/report |
| `/help` | Show available commands |

Use `/bind` when Telegram privacy mode hides ordinary numeric messages.
`/report` can show the last snapshot before the fresh report arrives; check the
metric timestamp. Review the server charts in the dashboard too.

| Installation state | Meaning |
|---|---|
| `PENDING_SETUP` | Script issued; waiting for execution |
| `INSTALLING` | Client reported installation start |
| `VERIFYING` | Callback accepted; worker is checking SSH and metrics |
| `READY` | SSH collection verified; Telegram binding pending |
| `ACTIVE` | Monitoring and Telegram binding are configured |
| `INSTALLATION_FAILED` | Installation failed or the token expired |
| `CONNECTION_FAILED` | SSH connection or metric collection failed |
| `DISABLED` | Administrator disabled monitoring |

Reissuing a script invalidates the previous token while preserving the server's
key pair. A consumed token cannot be reused. Existing clients retain their
previous monitoring users, keys, groups, and verified `known_hosts` entries;
they do not need to rerun the new installer. New clients pin host keys in the
database. A changed host key is never accepted automatically.

See [docs/onboarding.md](docs/onboarding.md) for installed paths, retry behavior,
and detailed client prerequisites.

## 12. Optional swap helper

For new clients, enable the swap option in the onboarding wizard. The installer
sets up the persistent helper and a narrow sudo rule for `monitor`. The
monitor checks for at least 2 GiB after verifying SSH when global automation
and initial provisioning are enabled. The helper activates swap and enables a
systemd swap unit. Critical RAM recovery has a separate per-server switch.
See [VPS operations](docs/vps-operations.md) for prerequisites and behavior.

The following manual steps are **only for legacy clients**. Transfer
`scripts/serverwatch-swap` and `deploy/serverwatch-swap.sudoers` through your
administrative SSH connection, then run these commands on the **client server**
from the transfer directory. Adjust the sudoers username if the monitoring login
is not `serverwatch`. Do not execute the helper as a smoke test.

```bash
sudo install -o root -g root -m 0755 serverwatch-swap /usr/local/sbin/serverwatch-swap
sudo visudo -cf serverwatch-swap.sudoers
sudo install -o root -g root -m 0440 serverwatch-swap.sudoers /etc/sudoers.d/serverwatch-swap
sudo visudo -c
sudo -l -U serverwatch
```

Only the numeric `TELEGRAM_ADMIN_ID` can acknowledge incidents or request
swap actions. Acknowledgment records the administrator and timestamp; the swap
button queues a separate job.

The helper checks free disk space, existing swap, and its lock, then creates a
single additional 1 GiB file and verifies `/proc/swaps`. It preserves existing
swap, refuses to overwrite a leftover inactive file, and does not edit
`/etc/fstab`. **The extra swap lasts only until the client reboots.** Interrupted
operations require manual inspection and are not automatically repeated.

## 13. Acceptance checks

Verify service activity, automatic startup, Nginx syntax, listeners, and database
connectivity. An active monitor process alone does not prove successful SSH
collection; you must onboard a client and check fresh metrics.

For public-IP HTTPS, set `DOMAIN` to that public IP before the checks below; the
variable is used only to construct the HTTPS test URL.

```bash
sudo systemctl is-active mysql nginx serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl is-enabled mysql nginx serverwatch-web serverwatch-bot serverwatch-monitor
sudo nginx -t
sudo ss -lntp
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py check-db
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
if test "$ACCESS_MODE" = 'tunnel'; then curl -I http://127.0.0.1:8080/login; fi
if test "$ACCESS_MODE" = 'https'; then curl -I "https://$DOMAIN/login"; fi
```

### Interactive acceptance checklist

| Check | Expected result |
|---|---|
| Password and TOTP | Login requires both factors; logout and a new login work |
| Onboarding | Client script completes, SSH metrics are verified, then Telegram binding succeeds |
| Fresh metrics | `/report` advances `last_success_at` and the latest sample timestamp |
| Charts | Server detail charts support 24-hour, 7-day, and 30-day views; history fills over time |
| Telegram delivery | Reports reach the bound group; the pending outbox does not grow indefinitely |
| Alerts | Temporarily lower a threshold, observe the alert, then restore the threshold |
| Administrator actions | Authorized acknowledgment succeeds; another user is refused; audit action is recorded |
| Swap | Validate logic with mocks; use the real button only when the client actually needs swap |

Do not exhaust RAM or disk to test alerts. Browser and Telegram interactions
must be checked directly; `systemctl` cannot verify them.

Check the database timestamps and recent monitor/bot journals after an
on-demand report. These queries show status information rather than secrets.

```bash
sudo mysql --execute "SELECT id,status,last_success_at FROM serverwatch.servers; SELECT server_id,MAX(collected_at) AS last_sample,COUNT(*) AS samples FROM serverwatch.server_metrics GROUP BY server_id; SELECT status,COUNT(*) FROM serverwatch.alerts GROUP BY status;"
sudo journalctl -u serverwatch-monitor -u serverwatch-bot --since '-10 min' --no-pager
```

## 14. Automated backups

The backup script uses `mysqldump --single-transaction`, `pipefail`, a lock,
private temporary files, and gzip validation before publishing the final archive.
It assumes Ubuntu's root socket authentication. Do not run DDL migrations during
a backup. Install the root-owned copy outside the application checkout.

```bash
sudo install -d -o root -g root -m 0700 /var/backups/serverwatch
sudo install -o root -g root -m 0750 /opt/serverwatch/scripts/serverwatch-backup /usr/local/sbin/serverwatch-backup
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/serverwatch-backup.service /etc/systemd/system/serverwatch-backup.service
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/serverwatch-backup.timer /etc/systemd/system/serverwatch-backup.timer
sudo systemctl daemon-reload
sudo systemctl start serverwatch-backup.service
sudo journalctl -u serverwatch-backup.service -n 30 --no-pager
sudo ls -lh /var/backups/serverwatch
sudo systemctl enable --now serverwatch-backup.timer
sudo systemctl list-timers serverwatch-backup.timer --all
```

The timer runs around 03:15 UTC with up to five minutes of randomized delay
and catches up missed runs. Archives are not deleted automatically. Copy verified
backups to protected off-host storage, test restoration, and manage retention.
Choose a real archive and verify its gzip integrity.

```bash
export BACKUP='/var/backups/serverwatch/serverwatch-YOUR_TIMESTAMP.sql.gz'
sudo gzip --test "$BACKUP"
```

### Back up configuration and encryption keys separately

The SQL dump cannot decrypt credentials without the original `ENCRYPTION_KEY`.
Archive the environment, migration credentials, and known-host records separately,
then encrypt the archive before off-host transfer. Enter the GPG passphrase
interactively and store it separately. Keep these files outside web-served paths.

```bash
sudo install -d -o root -g root -m 0700 /var/backups/serverwatch-secrets
sudo bash -c 'umask 077; tar -C /etc/serverwatch -czf /var/backups/serverwatch-secrets/config.tar.gz serverwatch.env migration.env known_hosts'
sudo gpg --symmetric --cipher-algo AES256 --output /var/backups/serverwatch-secrets/config.tar.gz.gpg /var/backups/serverwatch-secrets/config.tar.gz
sudo stat -c '%U:%G %a %n' /var/backups/serverwatch-secrets/config.tar.gz.gpg
```

Transfer only the encrypted `.gpg` file. After checking that the encrypted
backup is usable, remove the temporary plaintext archive.

```bash
sudo rm -- /var/backups/serverwatch-secrets/config.tar.gz
```

### Verify recovery after a host reboot

Confirm that the provider console is available, then reboot. This disconnects
SSH; reconnect afterwards and reopen any local tunnel.

```bash
sudo reboot
```

Verify automatic startup and fresh metrics after reconnecting. Do not start a
second worker manually to recover monitoring.

```bash
sudo systemctl is-active mysql nginx serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl is-enabled serverwatch-web serverwatch-bot serverwatch-monitor serverwatch-backup.timer
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
sudo journalctl -b -u serverwatch-web -u serverwatch-bot -u serverwatch-monitor -n 100 --no-pager
```

## 15. Recovery

On a replacement VPS, prepare packages, the service account, a compatible code
revision, the virtual environment, and loopback MySQL using sections 2–5.
**Do not run `setup_environment.py`: recovery requires the original secrets.**

On an existing host, first back up its current state and stop the services. On a
fresh host where the units are not installed yet, skip these stop commands.

```bash
sudo systemctl stop serverwatch-backup.timer serverwatch-backup.service
sudo systemctl stop serverwatch-web serverwatch-bot serverwatch-monitor
```

Place your encrypted configuration backup in the root-only backup directory.
Decrypt it and inspect the archive listing **before running the extraction**.
It must contain only `serverwatch.env`, `migration.env`, and `known_hosts`, with
no unexpected paths. Extract only a trusted archive, then restore permissions.

```bash
sudo bash -c 'umask 077; gpg --output /var/backups/serverwatch-secrets/config.restore.tar.gz --decrypt /var/backups/serverwatch-secrets/config.tar.gz.gpg'
sudo tar -tzf /var/backups/serverwatch-secrets/config.restore.tar.gz
sudo tar --no-same-owner -C /etc/serverwatch -xzf /var/backups/serverwatch-secrets/config.restore.tar.gz
sudo chown root:serverwatch /etc/serverwatch/serverwatch.env /etc/serverwatch/known_hosts
sudo chmod 0640 /etc/serverwatch/serverwatch.env /etc/serverwatch/known_hosts
sudo chown root:root /etc/serverwatch/migration.env
sudo chmod 0600 /etc/serverwatch/migration.env
sudo rm -- /var/backups/serverwatch-secrets/config.restore.tar.gz
```

Database dumps do not include MySQL accounts. On a **new host only**, restore
the database accounts using the original passwords from the saved URIs. This
helper uses bound SQL parameters and refuses to modify existing accounts.
Skip it on a host where those accounts already exist.

```bash
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/restore_database_users.py
```

Select and verify your SQL dump before import. Import replaces data in
matching tables. Restore an older dump against compatible application code first,
then apply upgrades. The pipeline uses `pipefail` to surface decompression errors.

```bash
export BACKUP='/var/backups/serverwatch/serverwatch-YOUR_TIMESTAMP.sql.gz'
sudo gzip --test "$BACKUP"
sudo mysql --execute "CREATE DATABASE IF NOT EXISTS serverwatch CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
sudo bash -o pipefail -c 'gzip -dc -- "$1" | mysql serverwatch' bash "$BACKUP"
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py check-db
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py upgrade
```

On a new host, install the systemd, Nginx, and backup files from sections
8–10 and 14 before starting services. Repeat section 13 afterwards. If the VPS
address changed, review DNS, firewalls, legacy SSH `from=` restrictions, and
host-key trust records.

```bash
sudo systemctl daemon-reload
sudo systemctl start serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl start serverwatch-backup.timer
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
sudo journalctl -u serverwatch-monitor -n 100 --no-pager
```

An interrupted monitoring check can resume after its lease expires, up to
10 minutes later. Interrupted swap operations are not automatically retried;
inspect the client's actual state before taking further action.

## 16. Upgrades and TOTP recovery

Validate the new revision in staging and back up both the database and secrets.
Stop the timer and application processes before schema changes. The sequence
below preserves local work through `git pull --ff-only`.

If tracked deployment files changed, reinstall the affected systemd/Nginx files
and validate them before restarting. `daemon-reload` alone does not copy updates
from the repository.

```bash
sudo systemctl start serverwatch-backup.service
sudo systemctl stop serverwatch-backup.timer
sudo systemctl stop serverwatch-web serverwatch-bot serverwatch-monitor
sudo -u serverwatch git -C /opt/serverwatch pull --ff-only
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip install -r /opt/serverwatch/requirements.txt
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip check
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py upgrade
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-limits.conf /etc/nginx/conf.d/serverwatch-limits.conf
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-proxy.conf /etc/nginx/snippets/serverwatch-proxy.conf
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl daemon-reload
sudo systemctl start serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl start serverwatch-backup.timer
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
```

The onboarding migration preserves existing SSH keys, Telegram groups, and
history. Before using the wizard on an upgraded installation, set
`PUBLIC_BASE_URL=https://YOUR_PUBLIC_IP_OR_DOMAIN` in the runtime environment and
restart web, bot, and monitor. Do not rerun the environment generator.
The current schema head is `35166c40bfc0`. Update the client dispatcher and
collector on previously enrolled VPS hosts to use the new remote operations.
See [existing-client upgrades](docs/vps-operations.md#update-an-existing-vps).

Do not replace `ENCRYPTION_KEY`; rotation requires re-encrypting existing data.
If you lose your authenticator, use a trusted administrator shell to reset TOTP
for your own web account. The next password login will offer enrollment again.

```bash
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py reset-totp --username YOUR_ADMIN_LOGIN
```

## 17. Troubleshooting

Run relative commands from `/opt/serverwatch`. Inspect journals without
publishing environment files, private keys, tokens, or database dumps.

| Symptom | Inspect | Resolution |
|---|---|---|
| MySQL fails to start | `journalctl -u mysql`, `mysqld --validate-config`, disk and RAM | Correct configuration or capacity, then restart |
| Database access denied | Database account host/grants and `scripts/manage.py check-db` | Match the runtime URI, password, and `127.0.0.1` account |
| Unknown database | Bootstrap status and database name | Use the initial bootstrap or recovery procedure as appropriate |
| Flask fails at startup | `journalctl -u serverwatch-web`, environment permissions, schema revision | Restore configuration and apply migrations; preserve the original Fernet key |
| Missing Python module | `.venv/bin/python -m pip check`, unit `WorkingDirectory` / `ExecStart` | Install requirements in the interpreter actually used by the service |
| Gunicorn cannot import the app | `run.py`, unit working directory | Use `run:app` from `/opt/serverwatch` |
| Nginx returns 502 | Web service, socket ownership, `namei -l /run/serverwatch/web.sock` | Restore the unit's socket group and service permissions |
| Nginx reports an invalid map or zone | `nginx -t`, `nginx -T` | Install the limits file in `conf.d` and the proxy file in `snippets` |
| Permission denied | `namei -l` for environment, known-hosts, or instance paths | Restore documented ownership and modes; keep `instance` private |
| Bot does not respond | Bot journal, token, numeric admin ID, polling/webhook conflicts | Run one polling process with valid credentials and no competing webhook |
| Telegram queue keeps growing | `scripts/manage.py status`, bot journal, group membership | Restore bot permissions and the correct group binding; retries are automatic |
| SSH times out | Client SSH port, provider/client firewalls, monitor journal | Restore reachability from the central server |
| SSH authentication fails | Client SSH journal, authorized-key permissions, legacy `from=` restriction | Correct the login, key, or allowed source address |
| Host key rejected | Pinned identity or verified legacy `known_hosts` entry | Verify identity through an independent console before restoring trust |
| No fresh metrics | Active flag, installation state, client Python 3, SSH, leases | Resolve client access and allow up to 10 minutes for an interrupted lease |
| Migration fails | Migration credentials, `alembic_version`, actual tables | Back up and investigate partial DDL; use the upgrade helper rather than guessing with `stamp` |
| TOTP rejected | Host time synchronization and a fresh code | Enable NTP; use the documented account reset only for lost access |
| Login loops through an SSH tunnel | Cookie settings and chosen Nginx branch | Use `SESSION_COOKIE_SECURE=false` only for loopback tunnel HTTP, restart web, then log in again |
| Swap rejected | Client sudoers, free disk, `/proc/swaps`, existing helper file | Review section 12 and inspect leftovers; do not overwrite existing swap |
| Backup fails | Backup journal, root socket authentication, free space | Restore database access/capacity and rerun the backup service |

Do not resolve access problems by using `chmod 777`, running application services
as root, opening MySQL publicly, disabling host-key checks, or enabling debug
logging with production secrets.

## 18. Development and validation

Install development dependencies on a local or staging machine. The suite uses
SQLite, mock SSH, and mock HTTPS; it does not create real swap or run the root
client installer. Development packages need not be installed on a small
production VPS.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m djlint app/templates --check
.venv/bin/python -m pip check
```

On Windows, use `.venv\Scripts\python.exe` in place of `.venv/bin/python`;
production services and the client installer still require Linux.

Checks cover authentication, SSH key handling, installation lifecycle, service
events, reporting, retention, migrations, delivery, and deployment configuration.
Bash parsing and unit-file comparisons are static checks. They cannot validate
real certificate issuance, Telegram delivery, Linux permissions, or production
MySQL behavior. Use section 13 for acceptance testing on the target environment.

When contributing, keep changes focused, update documentation for behavior or
configuration changes, and review generated migrations before committing them.
Use synthetic credentials and mocked remote services in tests.

## License

ServerWatch is licensed under the [Apache License 2.0](LICENSE).
Third-party assets retain their respective notices, including the bundled
[Chart.js license](app/static/vendor/Chart.js.LICENSE.md) and the
[xterm.js](app/static/vendor/xterm.LICENSE.md) and
[FitAddon](app/static/vendor/xterm-addon-fit.LICENSE.md) licenses.
