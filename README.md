# ServerWatch

Централізований моніторинг Linux-серверів через SSH, українська Flask-адмінпанель
з паролем і TOTP, MySQL та Telegram-сповіщення на aiogram 3.

## Архітектура

Три незалежні процеси використовують спільну MySQL:

- `run:app` — вебпанель, керування серверами, Chart.js, аналітика й журнал дій.
- `python -m monitoring.worker` — SSH-збір, пороги, погодинні агрегати, очищення,
  автоматичні звіти та контрольовані SWAP-завдання.
- `python -m bot.main` — Telegram polling, авторизація груп, команди, кнопки й доставка outbox.

Worker пише повідомлення в транзакційну чергу БД; бот доставляє їх із backoff.
Відсутність Telegram або вебпанелі не зупиняє інші процеси. Після перезапуску
розклад і черга зберігаються. Перевірки за замовчуванням кожні 180 секунд,
звіти кожні 4 години, нагадування аварії — щогодини. SSH concurrency — 2,
Gunicorn — один worker з двома потоками. Redis, Docker, Node.js не потрібні.

Сирі метрики зберігаються 24 години, погодинні середні й максимуми — 90 днів.
Пороги й інтервали налаштовуються через вебпанель. Час у БД та журналах — UTC.
Мережевий трафік — накопичувальні лічильники байтів з моменту запуску інтерфейсу.

## Локальна перевірка

Потрібен Python 3.12+; для production — Ubuntu 24.04 LTS з MySQL 8.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Тести використовують SQLite у пам’яті та mock SSH: не підключаються до серверів
і не створюють справжній SWAP. Приклад `.env` — у `.env.example`. Для запуску
вебпанелі локально потрібні власні секрети та БД; `SESSION_COOKIE_SECURE=false`
допустимо тільки для локального HTTP. У production використовуйте HTTPS.
Chart.js 4.5.1 включений локально з MIT-ліцензією, зовнішній CDN не потрібен.

## 1. Підготовка Ubuntu

Наведені нижче адміністративні команди виконуйте з наявного sudo-облікового запису.
Задайте власні значення; домен повинен уже мати DNS A-запис на центральний сервер.

```bash
export DOMAIN=monitor.example.com
export ADMIN_IP=203.0.113.10
export REPO_URL=https://github.com/YOUR_ORG/YOUR_REPOSITORY.git
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-dev build-essential libffi-dev \
  mysql-server nginx certbot python3-certbot-nginx ufw fail2ban git openssh-client
sudo adduser --system --group --home /opt/serverwatch serverwatch
sudo install -d -o serverwatch -g serverwatch -m 0755 /opt/serverwatch
sudo -u serverwatch git clone "$REPO_URL" /opt/serverwatch
sudo install -d -o serverwatch -g serverwatch -m 0700 /opt/serverwatch/instance
sudo install -d -o root -g serverwatch -m 0750 /etc/serverwatch
sudo timedatectl set-ntp true
sudo systemctl enable --now mysql nginx fail2ban
```

Перед змінами SSH тримайте відкритою другу перевірену сесію й консоль провайдера.
Якщо ще немає особистого адміністратора з ключем, створіть його:

```bash
sudo adduser opsadmin
sudo usermod -aG sudo opsadmin
sudo install -d -o opsadmin -g opsadmin -m 0700 /home/opsadmin/.ssh
sudo -u opsadmin nano /home/opsadmin/.ssh/authorized_keys
sudo chmod 0600 /home/opsadmin/.ssh/authorized_keys
# З іншого термінала перевірте: ssh opsadmin@CENTRAL_SERVER_IP та sudo -v
```

Лише після перевірки нового входу й sudo:

```bash
printf 'PubkeyAuthentication yes\nPasswordAuthentication no\nPermitRootLogin no\n' | \
  sudo tee /etc/ssh/sshd_config.d/00-serverwatch-hardening.conf >/dev/null
sudo sshd -t
sudo systemctl reload ssh
sudo ufw allow from "$ADMIN_IP" to any port 22 proto tcp
sudo ufw allow 'Nginx Full'
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ufw status verbose
sudo systemctl status fail2ban --no-pager
```

Не додавайте правило SSH лише для вказаного прикладового IP: замініть його власним
і врахуйте VPN/динамічну адресу. Перевірте нову SSH-сесію перед закриттям старої.

## 2. MySQL і права

MySQL не повинен слухати публічний інтерфейс:

```bash
printf '[mysqld]\nbind-address=127.0.0.1\nmysqlx-bind-address=127.0.0.1\ninnodb_buffer_pool_size=128M\nmax_connections=30\n' | \
  sudo tee /etc/mysql/mysql.conf.d/serverwatch.cnf >/dev/null
sudo systemctl restart mysql
sudo mysql
```

У MySQL задайте **два власні випадкові паролі**, без стандартних значень:

```sql
CREATE DATABASE serverwatch CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'serverwatch'@'127.0.0.1' IDENTIFIED BY 'REPLACE_WITH_RANDOM_RUNTIME_PASSWORD';
GRANT SELECT, INSERT, UPDATE, DELETE ON serverwatch.* TO 'serverwatch'@'127.0.0.1';
CREATE USER 'serverwatch_migrate'@'127.0.0.1' IDENTIFIED BY 'REPLACE_WITH_RANDOM_MIGRATION_PASSWORD';
GRANT ALL PRIVILEGES ON serverwatch.* TO 'serverwatch_migrate'@'127.0.0.1';
EXIT;
```

Міграційний користувач має права тільки на `serverwatch.*`, без глобальних прав.
Його пароль не зберігайте у runtime environment. Для URI спеціальні символи
пароля потрібно percent-encode, наприклад через `urllib.parse.quote(password, safe='')`.

## 3. Virtual environment і секрети

```bash
cd /opt/serverwatch
sudo -u serverwatch python3 -m venv .venv
sudo -u serverwatch .venv/bin/python -m pip install --upgrade pip
sudo -u serverwatch .venv/bin/pip install -r requirements.txt
sudo install -o root -g serverwatch -m 0640 .env.example /etc/serverwatch/serverwatch.env
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
sudo nano /etc/serverwatch/serverwatch.env
```

Перше значення вставте у `SECRET_KEY`, друге — у `ENCRYPTION_KEY`.
`DATABASE_URL` має містити runtime-користувача та власний пароль.
Не комітьте цей файл. Збережіть зашифровану офлайн-копію ENCRYPTION_KEY окремо
від дампів БД: без нього SSH-ключі й TOTP відновити неможливо.

## 4. Telegram

1. Відкрийте офіційний @BotFather, виконайте `/newbot`, отриманий токен вставте
   у `TELEGRAM_BOT_TOKEN` у захищеному environment-файлі.
2. Визначте числовий User ID облікового запису @Yureweo. Для отримання через
   власного бота спочатку надішліть йому приватне повідомлення, потім виконайте:

```bash
sudo -u serverwatch bash -c '
set -a; . /etc/serverwatch/serverwatch.env; set +a
cd /opt/serverwatch
.venv/bin/python - <<"PY"
import asyncio, os
from aiogram import Bot
async def main():
    async with Bot(os.environ["TELEGRAM_BOT_TOKEN"]) as bot:
        for update in await bot.get_updates():
            if update.message and update.message.from_user:
                sender = update.message.from_user
                print(sender.id, sender.username)
asyncio.run(main())
PY
'
```

Звірте, що ID належить саме вашому обліковому запису, та задайте `TELEGRAM_ADMIN_ID`.
Не запускайте `getUpdates` одночасно з bot-сервісом. Username не надає прав.
Polling не потребує вхідного порту. Додайте бота до групи; для кодів використовуйте
`/bind КОД`. Для звичайного 15-значного повідомлення вимкніть Group Privacy через
BotFather `/setprivacy` або надайте боту адміністративні права.

## 5. Міграції та адміністратор

Для зручності наступних команд перейдіть до shell користувача застосунку:

```bash
sudo -u serverwatch bash
cd /opt/serverwatch
set -a; . /etc/serverwatch/serverwatch.env; set +a
read -rsp 'Міграційний пароль MySQL: ' MIGRATION_PASSWORD; echo
export MIGRATION_PASSWORD
RUNTIME_DATABASE_URL="$DATABASE_URL"
export DATABASE_URL="$(.venv/bin/python -c 'import os; from urllib.parse import quote; print("mysql+pymysql://serverwatch_migrate:" + quote(os.environ["MIGRATION_PASSWORD"], safe="") + "@127.0.0.1/serverwatch?charset=utf8mb4")')"
.venv/bin/flask --app run db upgrade
export DATABASE_URL="$RUNTIME_DATABASE_URL"
unset MIGRATION_PASSWORD RUNTIME_DATABASE_URL
.venv/bin/flask --app run create-admin
exit
```

Пароль адміністратора має бути щонайменше 14 символів; CLI запитує його приховано.
Міграції вже включені — `db init` або `db migrate` під час встановлення не потрібні.

## 6. Gunicorn, systemd, Nginx і HTTPS

```bash
cd /opt/serverwatch
sudo install -m 0644 deploy/serverwatch-{web,monitor,bot}.service /etc/systemd/system/
sudo sed "s/monitor.example.com/$DOMAIN/g" deploy/nginx.conf | \
  sudo tee /etc/nginx/sites-available/serverwatch >/dev/null
sudo ln -sfn /etc/nginx/sites-available/serverwatch /etc/nginx/sites-enabled/serverwatch
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now serverwatch-web serverwatch-monitor serverwatch-bot
sudo systemctl reload nginx
sudo certbot --nginx -d "$DOMAIN" --redirect
sudo certbot renew --dry-run
sudo systemctl status serverwatch-web serverwatch-monitor serverwatch-bot --no-pager
```

Відкрийте `https://ВАШ_ДОМЕН`, введіть створені логін і пароль. На першому вході
відскануйте QR у Google Authenticator, введіть код і підтвердьте 2FA. Поки TOTP
не підтверджений, панель недоступна. Наступні входи завжди вимагають TOTP.

Окремо обмежте journald для сервера 40 GB:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\nRuntimeMaxUse=30M\n' | \
  sudo tee /etc/systemd/journald.conf.d/serverwatch.conf >/dev/null
sudo systemctl restart systemd-journald
```

## 7. Підготовка віддалених серверів

На **кожному клієнтському Linux-сервері** з sudo-облікового запису:

```bash
sudo apt update
sudo apt install -y python3 openssh-server
sudo adduser --disabled-password --gecos '' serverwatch
sudo install -d -o serverwatch -g serverwatch -m 0700 /home/serverwatch/.ssh
sudo -u serverwatch nano /home/serverwatch/.ssh/authorized_keys
sudo chmod 0600 /home/serverwatch/.ssh/authorized_keys
```

На власній захищеній машині створіть **окремий ключ для кожного сервера**:

```bash
ssh-keygen -t ed25519 -f ./client01-serverwatch -N '' -C serverwatch-client01
cat ./client01-serverwatch.pub
```

Публічний ключ вставте в `authorized_keys`, перед ключем додайте `restrict`.
За сталої адреси центрального сервера додайте також `from="CENTRAL_PUBLIC_IP"`.
Закритий ключ вставляється в HTTPS-форму додавання сервера. Обліковий запис має
бути без загального sudo, без shell-команд від Telegram. Для повних системних
журналів можна додати його до `systemd-journal`, врахувавши доступ до локальних логів:

```bash
sudo usermod -aG systemd-journal serverwatch
```

На центральному сервері отримайте й **звірте** host fingerprint з консоллю провайдера:

```bash
export CLIENT_IP=198.51.100.20
export CLIENT_PORT=22
ssh-keyscan -p "$CLIENT_PORT" "$CLIENT_IP" > /tmp/serverwatch-host.candidate
ssh-keygen -lf /tmp/serverwatch-host.candidate
# Звірте з результатом на клієнті:
# sudo ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
sudo touch /etc/serverwatch/known_hosts
sudo sh -c 'cat /tmp/serverwatch-host.candidate >> /etc/serverwatch/known_hosts'
sudo chown root:serverwatch /etc/serverwatch/known_hosts
sudo chmod 0640 /etc/serverwatch/known_hosts
rm /tmp/serverwatch-host.candidate
```

`ssh-keyscan` сам по собі не підтверджує довіру: додавати ключ слід тільки після
звірки fingerprint незалежним каналом. Для нестандартного порту known_hosts
містить `[IP]:PORT`. Невідомий або змінений host key відхиляється.

Після цього в панелі натисніть «Додати сервер». Backend перевірить SSH і покаже
одноразовий код, чинний 24 години. У групі від головного адміністратора надішліть
`/start`, потім `/bind 15_ЦИФР_БЕЗ_ПРОБІЛІВ`. Кожний сервер має власний код;
в одній групі можуть бути кілька серверів.

## 8. Контрольований SWAP +1 GB

Це необов’язкова можливість; моніторинг працює без sudo. Лише на клієнтському
сервері, де потрібна ця кнопка, передайте файли `scripts/serverwatch-swap` і
`deploy/serverwatch-swap.sudoers` через свій адміністративний канал, потім:

```bash
sudo install -o root -g root -m 0755 serverwatch-swap /usr/local/sbin/serverwatch-swap
sudo visudo -cf serverwatch-swap.sudoers
sudo install -o root -g root -m 0440 serverwatch-swap.sudoers /etc/sudoers.d/serverwatch-swap
sudo visudo -c
```

Ім’я SSH-користувача має збігатися з `serverwatch` у sudoers, або замініть його
на свій окремий обліковий запис. Не надавайте `NOPASSWD: ALL`.
Не запускайте helper для «тесту» на production: він справді створює SWAP.

Кнопка в критичному повідомленні створює завдання лише після перевірки ID.
Helper перевіряє диск, поточний SWAP і паралельні операції, додає один файл
`/var/lib/serverwatch-swap/swap-1g`, підтверджує `/proc/swaps`. Повторне натискання
за тією самою аварією не створює нового файлу. Старий SWAP не змінюється.
**Додатковий SWAP діє до перезавантаження**, `/etc/fstab` автоматично не редагується.
За наявності неактивного файлу після збою/перезавантаження потрібна ручна перевірка;
helper відмовить у перезаписі. Для файлових систем без підтримки swap-файлів
(наприклад, Btrfs без спеціальної підготовки) операція буде відхилена.

## 9. Перевірка роботи та логи

```bash
curl -I "https://$DOMAIN/login"
sudo ss -lntp
sudo journalctl -u serverwatch-web -n 80 --no-pager
sudo journalctl -u serverwatch-monitor -n 80 --no-pager
sudo journalctl -u serverwatch-bot -n 80 --no-pager
sudo journalctl -u serverwatch-monitor -f
sudo systemctl restart serverwatch-monitor
```

Перевірте: вхід із TOTP; успішне додавання сервера; `/status` у групі; нові дані
на графіках після перевірки; `/report`; історію аварій; відмову сторонньому ID на
кнопках. Аварії перевіряйте зміною порога в панелі, не штучним виснаженням RAM.
Звіти кожні 4 години відлічуються від останнього запланованого звіту й наступної
перевірки сервера. `/report` повертає останні дані та запитує свіжий знімок.

Порт 3306 має слухати лише 127.0.0.1; бот не відкриває TCP-портів. `offline` означає
дві невдалі SSH-спроби, а не доведену відсутність RAM. Відсутність подій systemd
може означати брак прав на journal. Недоставка Telegram відображається зростанням
черги в налаштуваннях; перевірте токен, права бота й Group ID.

## 10. Backup і відновлення

Підготуйте root-only файл `/root/.serverwatch-backup.cnf` через `sudo nano`:

```ini
[client]
user=root
socket=/var/run/mysqld/mysqld.sock
```

Ubuntu MySQL root використовує локальну socket-автентифікацію:

```bash
sudo chmod 0600 /root/.serverwatch-backup.cnf
sudo install -d -o root -g root -m 0700 /var/backups/serverwatch
sudo sh -c 'umask 077; mysqldump --defaults-extra-file=/root/.serverwatch-backup.cnf \
  --single-transaction --no-tablespaces serverwatch | gzip > \
  /var/backups/serverwatch/serverwatch-$(date -u +%Y%m%dT%H%M%SZ).sql.gz'
```

Копіюйте дамп до захищеного зовнішнього сховища. Окремо збережіть зашифровану
резервну копію `/etc/serverwatch/serverwatch.env` і перевіреного `known_hosts`.
Для щоденного backup додайте root cron через `sudo crontab -e`, використавши
наведену команду; очищуйте локальні дампи після перевірки зовнішньої копії.

Відновлення на підготовленій машині з тією самою схемою та секретами:

```bash
sudo systemctl stop serverwatch-web serverwatch-monitor serverwatch-bot
export BACKUP=/var/backups/serverwatch/serverwatch-YOUR_TIMESTAMP.sql.gz
sudo gzip -dc "$BACKUP" | sudo mysql serverwatch
# Відновіть serverwatch.env та known_hosts із захищеної копії та права 0640.
sudo systemctl start serverwatch-web serverwatch-monitor serverwatch-bot
sudo journalctl -u serverwatch-monitor -n 50 --no-pager
```

Перервані SSH-перевірки відновляться після lease до 10 хвилин. SWAP-завдання
не виконуються повторно автоматично після збою: перевірте стан на клієнті.
У разі втрати TOTP з довіреного shell користувача serverwatch:

```bash
cd /opt/serverwatch
set -a; . /etc/serverwatch/serverwatch.env; set +a
.venv/bin/flask --app run reset-totp YOUR_ADMIN_LOGIN
```

Це знову відкриє налаштування QR після пароля. Обмежуйте доступ до shell.

## 11. Оновлення

1. Створіть і перевірте backup БД та секретів.
2. На staging встановіть нові залежності й виконайте тести.
3. Зупиніть служби, оновіть код і залежності, застосуйте міграції окремим
   міграційним користувачем за процедурою з розділу 5.

```bash
sudo systemctl stop serverwatch-web serverwatch-monitor serverwatch-bot
cd /opt/serverwatch
sudo -u serverwatch git pull --ff-only
sudo -u serverwatch .venv/bin/pip install -r requirements.txt
# Застосуйте flask --app run db upgrade з міграційними правами, як у розділі 5.
sudo systemctl start serverwatch-web serverwatch-monitor serverwatch-bot
sudo systemctl status serverwatch-web serverwatch-monitor serverwatch-bot --no-pager
```

Не запускайте кілька polling-процесів одного Telegram-бота. Планувальник живе
тільки у monitor, ніколи у Flask/Gunicorn. Для 5–10 серверів почніть із concurrency=2;
стежте за `free -h`, `systemd-cgtop` і розміром БД. Локальний AI за замовчуванням
вимкнений; на машині 1 GB RAM не встановлюйте LLM. Стандартний аналіз працює автономно.

## Документація й джерела

Механізми захисту та експлуатаційні межі описані в [SECURITY.md](SECURITY.md).
Основні API звірені з [Flask security](https://flask.palletsprojects.com/en/stable/web-security/),
[aiogram Dispatcher](https://docs.aiogram.dev/en/latest/dispatcher/index.html)
і [Paramiko](https://docs.paramiko.org/en/stable/).
Живі MySQL/Telegram/Linux перевірки потребують власного середовища та облікових даних;
локальні тести не підтверджують зовнішнє розгортання.
