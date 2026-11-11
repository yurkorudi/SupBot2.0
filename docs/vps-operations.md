# VPS operations

## Configure the dashboard

1. Open the VPS and choose **Налаштування**. To schedule Telegram reports, select
   **Індивідуальний графік**, enter an IANA timezone such as `Europe/Kyiv`,
   add times in `HH:MM` format, and select weekdays. Confirm the next UTC run.
2. Bind the VPS to its Telegram group before relying on alerts or reports.
3. Enable automatic actions in global settings and for the VPS, then choose the
   individual actions. The global switch starts off. Manual requests are
   independent of the automatic switches.

During a daylight-saving gap the affected time is skipped. During a repeated
hour it is used once, at its first occurrence. After a scheduler outage, one
report represents any missed times and the cursor advances to the next future
slot. Cursor advancement and the Telegram outbox insert commit together.
Telegram delivery retries can still repeat a message if the bot crashes after
Telegram accepts it and before delivery is recorded.

## Prepare a new VPS

The onboarding wizard installs the restricted monitor dispatcher, the collector,
and a separate unprivileged `serverwatch-terminal` account for the administrator
who added the VPS. Choose persistent swap, database export, and reboot permissions
in the wizard as applicable. These selections install narrowly scoped sudo rules.
The wizard does not add the monitor user to the Docker group.

For a service restart, grant the monitor login exact commands for approved
names. For example, edit a dedicated file with `sudo visudo -f
/etc/sudoers.d/serverwatch-service-restart` and adapt these lines to the client:

```sudoers
monitor ALL=(root) NOPASSWD: /usr/bin/systemctl restart -- nginx.service
monitor ALL=(root) NOPASSWD: /usr/bin/systemctl show --property=ActiveState --value -- nginx.service
monitor ALL=(root) NOPASSWD: /usr/bin/docker restart --time 10 -- api
monitor ALL=(root) NOPASSWD: /usr/bin/docker inspect --format {{.State.Status}} -- api
```

Use individual service names and run `sudo visudo -c`. Review Docker container
names when containers are replaced. Discovery and the dashboard run regardless
of restart permissions. The worker stores statuses and permits disabling
monitoring individually. Installed but stopped services are discovered for
common application families. Running application units and visible Docker
containers are also included.

## Update an existing VPS

Apply the central upgrade in [the production runbook](../README.md#16-upgrades-and-totp-recovery)
first. For a wizard-managed client, transfer the files below through the
client's administrative SSH connection. On the client, use the trusted copies
from the new revision and keep a separate administrative connection open:

```bash
sudo install -o root -g root -m 0755 ssh_dispatch.py /usr/local/lib/serverwatch/ssh_dispatch.py
sudo install -o root -g root -m 0644 remote_collect.py /usr/local/lib/serverwatch/remote_collect.py
sudo install -o root -g root -m 0755 serverwatch-docker-list /usr/local/sbin/serverwatch-docker-list
sudo install -o root -g root -m 0755 serverwatch-swap-persistent /usr/local/sbin/serverwatch-swap-persistent
sudo install -o root -g root -m 0755 serverwatch-db-export /usr/local/sbin/serverwatch-db-export
```

The Python dispatcher and collector are in `monitoring/`. The root helpers
are in `scripts/`. For legacy SSH clients, the collector is transmitted by
the existing SSH connection. Apply helper permissions to the configured SSH
username in place of `monitor`. Use only the rules for enabled capabilities:

```sudoers
monitor ALL=(root) NOPASSWD: /usr/local/sbin/serverwatch-docker-list ""
monitor ALL=(root) NOPASSWD: /usr/local/sbin/serverwatch-swap-persistent ""
monitor ALL=(root) NOPASSWD: /usr/bin/systemctl --no-block reboot
monitor ALL=(root) NOPASSWD: /usr/local/sbin/serverwatch-db-export discover
monitor ALL=(root) NOPASSWD: /usr/local/sbin/serverwatch-db-export mysql
monitor ALL=(root) NOPASSWD: /usr/local/sbin/serverwatch-db-export mariadb
monitor ALL=(root) NOPASSWD: /usr/local/sbin/serverwatch-db-export postgresql
```

On a wizard-managed VPS, swap also requires the existing onboarding swap
permission in ServerWatch. Previous clients without the persistent helper
retain the temporary Telegram swap operation if their legacy helper is
available. Stop a feature from the dashboard to cancel subsequent automatic
requests. Remove its client sudo rules to revoke operating-system permission.

## Swap and CPU recovery

The persistent swap helper supports ext4 and XFS, reserves at least 512 MiB of
free disk, and checks active swap before allocating a single 2 GiB file. It
enables a dedicated systemd unit and verifies activation. An existing swap
partition or file remains available. The activity journal and Telegram report
the result when the group is authorized.

Critical RAM jobs become eligible ten minutes after Telegram delivery. The
worker rechecks RAM and existing swap before invoking the helper. Recovery of
RAM or acknowledgment cancels the pending automatic action. The administrator
can take over a pending job with the Telegram swap button.

CPU recovery requires at least two recent critical samples, a delivered Telegram
notice, and three more minutes of verified critical CPU. The worker records
the intent before sending the reboot command. It verifies recovery by collecting
fresh metrics with a changed Linux boot ID. It waits up to 15 minutes and
limits automatic reboot attempts to one per VPS in 24 hours. Run all reboot and
swap acceptance checks on an expendable staging VPS.

## Terminal

Use the **Термінал** tab as the web administrator who owns the VPS terminal
credential. For an existing VPS, create a key in the tab and install the shown
`restrict,pty` line for a dedicated unprivileged SSH user. Verify the server's
existing SSH host key before connecting. For Bash command timing on an existing
VPS, install `scripts/terminal-shell` and `scripts/terminal.bashrc` as
root-owned files under `/usr/local/lib/serverwatch/` with modes 0755 and
0644, then prefix the key with
`restrict,pty,command="/usr/local/lib/serverwatch/terminal-shell"`.

Public deployments use the HTTPS origin in `PUBLIC_BASE_URL`. Update both
Nginx snippets during upgrade for WebSocket forwarding. The documented
loopback-only SSH tunnel can use HTTP with its existing cookie settings.
The deployed single Gunicorn worker accepts one live terminal at a time.
Sessions close after five minutes without input or output, or after 30 minutes.
The browser holds scrollback. The activity journal records connection events.

## Backup and restore

Select daily backups for a VPS, or request a one-off backup from **Бекапи**.
The helper exports local MySQL or MariaDB over its Unix socket and PostgreSQL
over local peer authentication. It uses `pg_dumpall` for the default
PostgreSQL cluster and the appropriate MySQL-family dump client. If the local
MySQL-family account requires credentials, configure a root-owned mode 0600
`/etc/serverwatch/db-export.cnf` using a `[client]` section. Keep the
parent directory owned by root and unwritable by other accounts.

On the central host, the archive is in
`/opt/serverwatch/instance/vps-backups/<server_id>/backup-<generation>.zip`.
The worker validates SQL headers, ZIP CRC, size, and SHA-256 before committing
the new file reference. One successfully verified archive is retained by
default. The previous successful archive stays available if a dump fails.
Keep the instance directory private. The limit is 2 GiB of uncompressed SQL per
engine and the worker reserves at least 512 MiB on the destination filesystem.

For a restore drill, copy the latest archive to a disposable instance with
the same database engine and a compatible version. Compare its SHA-256 to the
value shown in the dashboard, then run `unzip -t` and extract the matching
SQL. Run the restore with local administrator credentials on that disposable
instance. Examples:

```bash
psql -X -v ON_ERROR_STOP=1 -d postgres -f postgresql.sql
mysql --protocol=SOCKET < mysql.sql
mariadb --protocol=SOCKET < mariadb.sql
```

For PostgreSQL cluster dumps and MySQL-family system schemas, prepare the
disposable instance according to the target engine's restore procedure and
review conflicts with pre-existing roles or system tables. Confirm expected
schemas, tables, row counts, and application reads. Repeat the drill after
major engine or schema changes.
Use a MariaDB restore client that understands the dump's sandbox preamble.
[MariaDB dump compatibility](https://mariadb.org/mariadb-dump-file-compatibility-change/).

The central ServerWatch database timer is independent of these VPS backups.
There are no new environment variables. Terminal HTTPS reuses
`PUBLIC_BASE_URL` and the existing cookie configuration.
