# ServerWatch — розгортання на чистому Ubuntu VPS

SSH-моніторинг Linux, українська Flask-панель із паролем/TOTP, MySQL та Telegram
на aiogram 3. Docker, Redis і Node.js не потрібні.

**Основний сценарій — без домену, через SSH-тунель.** Nginx слухає лише
`127.0.0.1:8080` на VPS. З’єднання з комп’ютером шифрується SSH. Публічний HTTPS
із власним доменом описаний окремою гілкою. Паролі, TOTP та приватні SSH-ключі
не можна передавати через публічний незашифрований HTTP.

Для прямого доступу **за публічною IP без тунелю й без домену** використовуйте
[окремий сценарій HTTPS на IP](docs/ip-https.md). Він замінює гілки Nginx у
розділі 10 після запуску служб і firewall; для нього ACCESS_MODE=https.

Інструкція призначена для **чистого Ubuntu Server 22.04/24.04 LTS, Python 3.10+ та MySQL 8**.
**На чистому Ubuntu VPS ця процедура під час аудиту не виконувалася.** Локальні
тести та перевірка конфігурацій не замінюють перевірку встановлення на сервері.

## 1. Фактична архітектура та сумісність

| Компонент | Точка входу | Systemd |
|---|---|---|
| Flask/Gunicorn | `run:app`, factory `app.create_app()` | `serverwatch-web` |
| Telegram polling і доставка outbox | `python -m bot.main` | `serverwatch-bot` |
| SSH, аварії, звіти, агрегація/cleanup | `python -m monitoring.worker` | `serverwatch-monitor` |
| MySQL backup | `/usr/local/sbin/serverwatch-backup` | `serverwatch-backup.timer` |

Три Python-процеси незалежні й працюють під Linux-користувачем `serverwatch`.
Спільна MySQL зберігає розклад і чергу `outbox`; збій Telegram або вебпанелі
не зупиняє моніторинг. Gunicorn слухає Unix socket `/run/serverwatch/web.sock`.
Бот використовує polling і не відкриває вхідних портів. Scheduler працює лише
у monitor. Його unit утримує `flock` на `instance/monitor.lock`; не запускайте
другий worker вручну в обхід unit-файлу.

Конфігурація — `app/config.py`, розширення — `app/extensions.py`, моделі —
`app/models/`, міграції — `migrations/`. Кореневого `config.py` немає.
Production-залежності — `requirements.txt`, тести — `requirements-dev.txt`.
Gunicorn встановлюється тільки на Linux; Chart.js включений локально з ліцензією.

У звітах є короткі коментарі з гумором і легкою лайкою: по 20 для нормального,
попереджувального й критичного навантаження. Фраза відповідає порогам конкретного
сервера; технічні рекомендації залишаються поруч. Для кожного сервера й рівня
20 варіантів проходять коло без повторів, потім цикл починається знову. Позиція
зберігається у settings, тому переживає рестарт і спільна для worker та бота.
Позитивні варіанти переважно без лайки, з самоіронією бота; негативні починаються
з емоційного «Бляяя…», «Курва…» тощо. Є один політичний жарт серед позитивних.
Це локальна бібліотека фраз, без зовнішнього AI/API. Для застарілих даних чи
недоступного SSH звіт не робить жартівливих висновків про поточне навантаження.
Нової міграції БД для цієї можливості не потрібно.

У першому звіті святкового дня бот додає привітання один раз на Telegram-групу,
навіть якщо в ній кілька серверів. Дата визначається в Europe/Kyiv з урахуванням
літнього часу; потрібний пакет tzdata включений у requirements.txt.
Календар: 1 січня, 14 лютого, 8 березня, 1 квітня, 28 червня, 24 серпня,
1 жовтня, 6 грудня, 25 грудня та 31 грудня. Перелік і тексти — у
app/services/report_holidays.py. Привітання не замінює аварійну інформацію.
Рухомі свята до цього фіксованого календаря не включені.
Дати 1 жовтня звірено з [офіційним календарем](https://zakon.rada.gov.ua/laws/main/day193).



У [Ubuntu 24.04](https://packages.ubuntu.com/noble/python3) системний Python 3.12,
у [Ubuntu 22.04](https://packages.ubuntu.com/jammy/python3) — 3.10. Код сумісний з
Python 3.10+: для UTC використовується `datetime.timezone.utc`, а не доданий у 3.11
аліас `datetime.UTC`. Ruff налаштовано на py310, щоб автоматичні виправлення не
повертали цей несумісний імпорт. Змінювати системний Python або перевстановлювати
VPS для усунення цієї помилки не потрібно. Повне розгортання на обох версіях
Ubuntu слід перевірити на своєму VPS; локальні Python-тести не замінюють Linux-перевірку.

## 2. Перед початком

Блоки `bash` виконуйте **на центральному VPS** у Bash від власного sudo-адміністратора,
крім явно позначених клієнтських/локальних команд. `sudo -u serverwatch` означає
запуск застосунку без root. Не вмикайте `set -x`, щоб секрети не потрапляли в логи.

Перевірте ОС, ресурси, sudo та Python. Якщо остання команда завершується помилкою,
зупиніться: потрібен підтримуваний інтерпретатор.

```bash
cat /etc/os-release
uname -m
free -h
df -h /
id
sudo -v
python3 --version
python3 -c 'import sys; assert sys.version_info >= (3,10), "Потрібен Python 3.10+"'
```

Задайте **свої параметри** замість прикладів. `SSH_PORT` — чинний порт SSH
центрального VPS. Після перепідключення повторно задайте ці змінні.

```bash
export REPO_URL='https://github.com/YOUR_ORG/YOUR_REPOSITORY.git'
export VPS_IP='YOUR_VPS_PUBLIC_IP'
export SSH_PORT='22'
export SSH_ADMIN='YOUR_EXISTING_SUDO_LOGIN'
export ADMIN_IP='YOUR_CURRENT_PUBLIC_IP'
export ACCESS_MODE='tunnel'
# Лише якщо є домен: export ACCESS_MODE='https'
# Лише якщо є домен: export DOMAIN='YOUR_REAL_DOMAIN'
```

Ці змінні — параметри інсталяції, а не Flask. Код **не читає APP_ENV або DOMAIN**;
їх додавання в `.env` не налаштує застосунок.

## 3. Системні пакети

Оновіть APT і пакети. Якщо `/var/run/reboot-required` існує, заплануйте reboot
до наступних кроків і знову підключіться до VPS.

```bash
sudo apt update
sudo apt upgrade -y
if test -f /var/run/reboot-required; then cat /var/run/reboot-required; fi
```

Установіть Python/venv, інструменти збірки wheels, Git, SSH, MySQL, Nginx,
Certbot, firewall, Fail2ban та засоби перевірки/backup. Certbot використовується
лише в доменній гілці. Увімкніть синхронізацію часу для TOTP.

```bash
sudo apt install -y python3 python3-venv python3-dev build-essential libffi-dev \
  libssl-dev git openssh-client mysql-server nginx certbot python3-certbot-nginx \
  ufw fail2ban curl ca-certificates openssl gzip tar gnupg util-linux acl
sudo timedatectl set-ntp true
sudo systemctl enable --now mysql nginx
```

Перевірте встановлені команди та запущені служби. Не продовжуйте, якщо MySQL
або Nginx падає; дивіться troubleshooting нижче.

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

## 4. Користувач, каталоги та Python venv

Створіть системного користувача без інтерактивного входу. `--no-create-home`
залишає робочий каталог порожнім для clone. Ці команди — для першої інсталяції,
не для перезапису вже наявного проєкту.

```bash
sudo adduser --system --group --no-create-home --home /opt/serverwatch \
  --shell /usr/sbin/nologin serverwatch
sudo install -d -o serverwatch -g serverwatch -m 0755 /opt/serverwatch
sudo -u serverwatch bash -c 'umask 022; git clone "$1" /opt/serverwatch' bash "$REPO_URL"
sudo install -d -o serverwatch -g serverwatch -m 0700 /opt/serverwatch/instance
sudo install -d -o root -g serverwatch -m 0750 /etc/serverwatch
cd /opt/serverwatch
```

Перевірте файли клонованого репозиторію. Якщо clone не спрацював, спочатку
виправте URL/доступ до Git, а не переходьте до встановлення залежностей.

```bash
sudo -u serverwatch git -C /opt/serverwatch status --short
ls -l run.py requirements.txt .env.example app/config.py migrations/env.py
ls -l bot/main.py monitoring/worker.py deploy/serverwatch-web.service
```

Створіть venv системним Python 3.10+ і встановіть залежності саме в нього. Системний
`sudo pip` не потрібен; усі процеси використовують `/opt/serverwatch/.venv`.

```bash
sudo -u serverwatch python3 -m venv /opt/serverwatch/.venv
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip install --upgrade pip
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip install -r /opt/serverwatch/requirements.txt
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip check
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -c 'import flask, flask_migrate, sqlalchemy, pymysql, aiogram, paramiko, pyotp, cryptography; print("Python imports: OK")'
/opt/serverwatch/.venv/bin/gunicorn --version
```

Для ручної роботи активація — `source /opt/serverwatch/.venv/bin/activate`,
вихід — `deactivate`. Нижче абсолютні шляхи до Python роблять активацію зайвою.

## 5. MySQL: локальний доступ, запуск та перевірка

Обмежте classic port і X Protocol адресою loopback; невеликий buffer pool
підходить як початкова конфігурація VPS із 1 GB RAM. Перевірте cnf до рестарту.

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

У стандартному Ubuntu root MySQL входить через `sudo mysql` та auth_socket,
без встановлення root-пароля. Перевірте доступ, параметри та listeners.

```bash
sudo mysql --execute "SELECT VERSION(), CURRENT_USER(); SHOW VARIABLES WHERE Variable_name IN ('bind_address','mysqlx_bind_address','port');"
sudo ss -lntp '( sport = :3306 or sport = :33060 )'
sudo mysql
```

У відкритій MySQL-консолі виконайте цей SQL; EXIT повертає до Bash.
Змінювати root на парольну автентифікацію для застосунку не потрібно.

```sql
SELECT user, host, plugin FROM mysql.user WHERE user = 'root';
SHOW VARIABLES LIKE 'character_set_server';
EXIT;
```

Для 3306/33060 допустимий тільки `127.0.0.1`, не `0.0.0.0`, `*` або `[::]`.
3306 не відкривайте у firewall. Джерело: [Ubuntu MySQL](https://ubuntu.com/server/docs/install-and-configure-a-mysql-server).

## 6. Environment, база та окремі ролі

Створіть бота через `/newbot` у @BotFather і збережіть токен у менеджері паролів.
Bot service поки не запускайте. Переконайтеся, що БД/ролей ще немає: якщо вони
існують, це не чисте встановлення; не видаляйте дані й не генеруйте Fernet заново.

```bash
sudo mysql --execute "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='serverwatch'; SELECT user,host FROM mysql.user WHERE user IN ('serverwatch','serverwatch_migrate');"
```

Генератор із репозиторію приховано запитає токен і числовий Telegram User ID.
Якщо ID невідомий, натисніть Enter; його можна визначити нижче. Він генерує
кожний пароль БД і SECRET_KEY через `secrets.token_hex(32)`, Fernet-ключ через
`Fernet.generate_key()`, записує файли з обмеженими правами, не друкує секрети
і відмовляється перезаписувати наявну конфігурацію.

```bash
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/setup_environment.py --access "$ACCESS_MODE"
sudo stat -c '%U:%G %a %n' /etc/serverwatch/serverwatch.env /etc/serverwatch/migration.env /etc/serverwatch/bootstrap.sql
```

| Файл | Власник/режим | Призначення |
|---|---|---|
| `/etc/serverwatch/serverwatch.env` | root:serverwatch 0640 | Runtime для трьох служб |
| `/etc/serverwatch/migration.env` | root:root 0600 | DATABASE_URL окремої DDL-ролі |
| `/etc/serverwatch/bootstrap.sql` | root:root 0600 | Одноразове створення БД та ролей |

`bootstrap.sql` містить CREATE DATABASE `serverwatch` з `utf8mb4` та
`utf8mb4_unicode_ci`, CREATE USER/GRANT для `serverwatch@127.0.0.1`
(SELECT, INSERT, UPDATE, DELETE) та `serverwatch_migrate@127.0.0.1`
(ALL PRIVILEGES тільки на `serverwatch.*`). Паролі випадкові, URI екрановані;
вставляти секрети вручну в SQL/history не потрібно. `FLUSH PRIVILEGES` після
CREATE USER/GRANT не потрібна. Root не використовується runtime-процесами.

Імпортуйте SQL через root socket без `--force`, перевірте кодування/права та
увійдіть Python-драйвером від імені нового runtime-користувача.

```bash
sudo bash -c '/usr/bin/mysql --protocol=SOCKET --user=root < /etc/serverwatch/bootstrap.sql'
sudo mysql --execute "SHOW CREATE DATABASE serverwatch; SHOW GRANTS FOR 'serverwatch'@'127.0.0.1'; SHOW GRANTS FOR 'serverwatch_migrate'@'127.0.0.1';"
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py check-db
```

`check-db` читає runtime DATABASE_URL, підключається через PyMySQL і виконує
SELECT 1. Очікується `MySQL: OK ... user=serverwatch`; паролі не виводяться.
Лише після успішної перевірки видаліть одноразовий SQL; migration.env залиште.

```bash
sudo rm -- /etc/serverwatch/bootstrap.sql
```

### Усі змінні, які справді читає застосунок

| Змінна | Призначення / значення |
|---|---|
| PUBLIC_BASE_URL | Публічна HTTPS-адреса центральної панелі для клієнтського callback, без шляху; обов’язкова для майстра |
| SECRET_KEY | 64 випадкові hex-символи; сесії й HMAC кодів |
| ENCRYPTION_KEY | Fernet; SSH-ключі й TOTP-секрети |
| DATABASE_URL | `mysql+pymysql://serverwatch:ПАРОЛЬ@127.0.0.1/serverwatch?charset=utf8mb4` |
| TELEGRAM_BOT_TOKEN | Токен BotFather |
| TELEGRAM_ADMIN_ID | Додатний числовий ID адміністратора |
| SSH_KNOWN_HOSTS | `/etc/serverwatch/known_hosts` |
| SSH_TIMEOUT | `12`; timeout підключення, збір має окремий обмежений timeout |
| MONITOR_CONCURRENCY | `2`; код обмежує діапазон 1–4 |
| SESSION_COOKIE_SECURE | false тільки для loopback HTTP через SSH-тунель, true для HTTPS |
| AI_ENABLED | false, AI не потрібен для стандартних звітів |
| AI_URL | `http://127.0.0.1:11434/api/generate`, необов’язковий локальний Ollama |
| AI_MODEL | `llama3.2:1b`, лише коли ввімкнений AI |

На 1 GB VPS локальну LLM не встановлюйте. Для власних паролів у URI потрібне
percent-encoding; генератор робить це автоматично. APP_ENV і DOMAIN у коді відсутні.
Міграційний DATABASE_URL не передається runtime-службам.

Редагуйте конфігурацію через sudoedit. Environment читають systemd і dotenv;
**не виконуйте ці файли через source або `. файл`**. Не друкуйте ключі через
cat/echo та не додавайте .env у Git; `.env.example` — тільки приклад.

```bash
sudoedit /etc/serverwatch/serverwatch.env
```

### Telegram User ID

Якщо ID не було задано, надішліть власному боту приватне повідомлення з головного
акаунта та виконайте getUpdates через helper. Bot service має бути зупинений;
другого polling-процесу або webhook бути не повинно. Виводяться лише ID/username.

```bash
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py telegram-id
sudoedit /etc/serverwatch/serverwatch.env
```

Замініть TELEGRAM_ADMIN_ID=0 на свій звірений додатний ID. Не копіюйте ID
стороннього відправника. @Yureweo — згадка, а не механізм авторизації.
Без токена та додатного ID бот не запуститься.

## 7. Міграції та адміністратор

Flask-Migrate вже налаштований у factory через `migrate.init_app(app, db)`.
Моделі завантажуються, початкова ревізія — `c99481d201d8`, поточна —
`4e2c9d8f610a` (майстер підключення). На VPS **не запускайте
db init, db migrate, db.create_all або stamp head**: потрібен upgrade готової
міграції, інакше можна приховати відсутню схему.

Helper upgrade читає root-only migration.env, знижує права до serverwatch та
виконує `.venv/bin/python -m flask --app run db upgrade`. Перевірте таблиці й
поточну ревізію після застосування; Flask CLI не виконується від root.

```bash
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py upgrade
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
sudo mysql --execute "SHOW TABLES FROM serverwatch; SELECT version_num FROM serverwatch.alembic_version;"
```

Очікуються users, servers, server_metrics, server_hourly_metrics, server_logs,
alerts, admin_actions, login_attempts, settings, jobs, outbox, alembic_version.
status показує лічильники й час, без ключів/токенів/хешів паролів.

Створіть адміністратора реальною командою create-admin. Пароль вводиться
приховано двічі; мінімум 14 символів. Не передавайте його через --password.

```bash
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py create-admin
```

Після першого парольного входу в браузері відскануйте QR у Google Authenticator
і підтвердьте TOTP. До цього панель закрита; наступні входи теж вимагають TOTP.
Нові міграції створює розробник на окремій БД командою `flask --app run db migrate
-m "Опис"`, перевіряє ревізію й комітить її; це не крок production-встановлення.


## 8. Systemd: три окремі процеси

Gunicorn 25.3+ запускається з --no-control-socket: керування виконує systemd.
Це запобігає створенню .gunicorn у read-only каталозі проєкту;
ProtectSystem=strict залишається ввімкненим.

Нижче — повний вміст фактичних unit-файлів. EnvironmentFile читає systemd до
запуску процесу. Web має групу www-data для socket та додаткову serverwatch для
known_hosts; /run/serverwatch створюється автоматично. instance створено раніше.

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

Встановіть файли від root, перевірте їх, перезавантажте systemd, увімкніть
автозапуск і запустіть служби. Не запускайте бот до заповнення токена та ID.

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

Перевірте журнал кожного компонента та доступ до socket саме від Nginx-користувача.
Остання команда має повернути HTTP 200. Після reboot каталог /run відновить systemd.

```bash
sudo journalctl -u serverwatch-web -n 100 --no-pager
sudo journalctl -u serverwatch-bot -n 100 --no-pager
sudo journalctl -u serverwatch-monitor -n 100 --no-pager
sudo ls -ld /run/serverwatch
sudo ls -l /run/serverwatch/web.sock
sudo -u www-data curl --unix-socket /run/serverwatch/web.sock -I http://localhost/login
```

## 9. UFW, SSH та Fail2ban

До ufw enable перевірте **реальний SSH-порт**, правило для нього, другу сесію
та доступну консоль провайдера. Не змінюйте порт одночасно з firewall. Перевірте
також cloud firewall. За наявності Match-блоків sshd врахуйте їх для свого логіна.

```bash
sudo sshd -T | head -n 30
sudo ss -lntp
printf 'Дозволити SSH із %s на TCP %s\n' "$ADMIN_IP" "$SSH_PORT"
sudo ufw allow from "$ADMIN_IP" to any port "$SSH_PORT" proto tcp
sudo ufw status numbered
sudo ufw show added
```

Якщо адреса адміністратора змінна, свідомо використайте натомість
`sudo ufw allow "$SSH_PORT/tcp"`, а пізніше обмежте доступ VPN/сталою адресою.
Не видаляйте робоче правило до перевірки нового входу. Лише після перевірки
SSH-правила додайте вебпорти для HTTPS і ввімкніть firewall. Tunnel їх не потребує.

```bash
if test "$ACCESS_MODE" = 'https'; then sudo ufw allow 'Nginx Full'; fi
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
sudo ufw status verbose
```

З **іншого термінала свого комп’ютера** перевірте нову SSH-сесію на чинному порту.
Не продовжуйте hardening до успіху. На чистому VPS у tunnel-гілці назовні
потрібен тільки SSH; 8080, 8000, 3306/33060 не відкривайте. На машині зі старими
правилами перевірте зайві дозволи вручну, не застосовуйте сліпий ufw reset.

Якщо ви ще входите як root і не маєте іншого sudo-адміністратора, створіть його
до заборони root-login. Вставте **публічний** ключ у редакторі. Якщо ваш наявний
sudo-користувач уже працює з ключем, цей блок пропустіть.

```bash
sudo adduser opsadmin
sudo usermod -aG sudo opsadmin
sudo install -d -o opsadmin -g opsadmin -m 0700 /home/opsadmin/.ssh
sudo -u opsadmin nano /home/opsadmin/.ssh/authorized_keys
sudo chmod 0600 /home/opsadmin/.ssh/authorized_keys
```

З іншого термінала перевірте `ssh -p YOUR_PORT opsadmin@YOUR_VPS_IP` та sudo -v.
Тільки після перевірки ключового входу, sudo й консолі відновлення створіть
hardening-файл, перевірте синтаксис і reload. Стару сесію поки не закривайте.

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

Знову перевірте новий вхід. Потім увімкніть Fail2ban для фактичного SSH_PORT;
backend systemd читає journal. Перевірка -t виконується до рестарту служби.

```bash
printf '[sshd]\nenabled=true\nbackend=systemd\nport=%s\nmaxretry=5\nfindtime=10m\nbantime=15m\n' "$SSH_PORT" | sudo tee /etc/fail2ban/jail.d/serverwatch.local >/dev/null
sudo fail2ban-client -t
sudo systemctl enable --now fail2ban
sudo systemctl restart fail2ban
sudo fail2ban-client status sshd
```

Обмежте загальний обсяг journald для SSD 40 GB. Це квота всього VPS, не тільки
ServerWatch; перегляд логів далі виконується через journalctl.

```bash
sudo install -d -m 0755 /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\nRuntimeMaxUse=30M\n' | sudo tee /etc/systemd/journald.conf.d/serverwatch.conf >/dev/null
sudo systemctl restart systemd-journald
```

## 10. Nginx і вибір доступу

Установіть proxy snippet та global map/limit_req_zone. У стандартному Ubuntu
conf.d включається всередині http, де дозволені ці директиви. Не вставляйте
nginx-limits.conf всередину server. Зона обмежує тільки POST /login і /totp:
5 запитів/хвилину з burst=5, при перевищенні HTTP 429. У тунелі клієнтська адреса
для Nginx — loopback, отже ліміт спільний. БД також обмежує невдалі входи.

```bash
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-proxy.conf /etc/nginx/snippets/serverwatch-proxy.conf
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-limits.conf /etc/nginx/conf.d/serverwatch-limits.conf
```

Proxy перезаписує X-Forwarded-For/Proto, не довіряючи зовнішнім заголовкам.
Gunicorn залишається тільки на Unix socket. Джерело директив:
[Nginx limit_req](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html).
Виконайте **тільки одну гілку**. Стандартний default видаляється лише як symlink
чистої інсталяції, інші сайти не змінюються.

### 10A. Без домену: SSH-тунель

Установіть loopback server. Порт 8080 у UFW не відкривайте. Перевірте конфігурацію
до reload, HTTP та фактичний listener після нього.

```bash
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-tunnel.conf /etc/nginx/sites-available/serverwatch
if test -L /etc/nginx/sites-enabled/default; then sudo unlink /etc/nginx/sites-enabled/default; fi
sudo ln -sfn /etc/nginx/sites-available/serverwatch /etc/nginx/sites-enabled/serverwatch
sudo nginx -t
sudo systemctl reload nginx
curl -I http://127.0.0.1:8080/login
sudo ss -lntp '( sport = :8080 )'
```

Очікуються HTTP 200 та 127.0.0.1:8080, без 0.0.0.0 або [::] на 8080.
Генератор для tunnel задає SESSION_COOKIE_SECURE=false: браузер використовує
локальний HTTP, а весь віддалений шлях захищає SSH. Для публічної панелі це
значення не використовуйте. Домен і Certbot у цій гілці не потрібні.

Наступну команду виконуйте **на своєму комп’ютері**, замінивши власні SSH-дані.
Локальний 18080 також слухає лише loopback. Термінал залиште відкритим.

```bash
ssh -p YOUR_SSH_PORT -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -L 127.0.0.1:18080:127.0.0.1:8080 YOUR_SUDO_LOGIN@YOUR_VPS_PUBLIC_IP
```

Відкрийте `http://127.0.0.1:18080/login` у браузері. SSH-сервер має дозволяти
TCP forwarding для вашого адміністративного користувача. Якщо він обмежений,
перевірте Match-блоки sshd; не відкривайте 8080 назовні. Клієнтський користувач
моніторингу з restrict не призначений для адміністративного тунелю.

### 10B. Необов’язково: домен і HTTPS

Виконуйте тільки за наявності власного DNS A-запису на VPS; AAAA, якщо задано,
також повинен вести на чинну IPv6-адресу. DOMAIN — звичайне DNS-ім’я без
протоколу/слешів. У runtime environment має бути SESSION_COOKIE_SECURE=true.
Якщо переходите з tunnel, змініть його через sudoedit і перезапустіть web.

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

Перед отриманням сертифіката перевірте виконані правила розділу 9 для HTTPS:
80/443 повинні бути доступні і в UFW, і у firewall провайдера. До TLS **не вводьте
пароль/TOTP/SSH-ключ через HTTP**. Certbot запитає email та згоду з умовами,
встановить сертифікат і redirect. Перевірка curl виконується без -k.

```bash
sudo certbot --nginx -d "$DOMAIN" --redirect
sudo nginx -t
sudo systemctl reload nginx
curl -I "https://$DOMAIN/login"
sudo systemctl enable --now certbot.timer
sudo systemctl list-timers certbot.timer --all
sudo certbot renew --dry-run
```

Очікується HTTPS 200 без помилок довіри. Відкрийте `https://ВАШ_ДОМЕН/login`.
Не ігноруйте попередження сертифіката. Renew описаний у
[Certbot documentation](https://eff-certbot.readthedocs.io/en/stable/using.html).
Без домену пропустіть цю гілку. Прямий HTTPS на IP налаштовується за
[docs/ip-https.md](docs/ip-https.md), з новим Certbot та окремим timer поновлення.

## 11. Перший вхід і клієнтські сервери

Відкрийте браузер за URL обраної гілки, введіть пароль, відскануйте QR у Google
Authenticator/сумісному TOTP-застосунку. Підтвердьте 6-значний код. Перевірте
вихід і новий вхід: без TOTP dashboard недоступний.

### Підключення нового клієнтського сервера

Новий сервер підключається через майстер у панелі без ручного створення SSH-ключів
і редагування клієнтських конфігурацій. Для callback потрібен доступний клієнту
**публічний HTTPS** центрального ServerWatch з довіреним сертифікатом. Домен
не обов’язковий: підійде [HTTPS на IP](docs/ip-https.md).
Один лише loopback HTTP через SSH-тунель не приймає встановлення з клієнта.

Один раз задайте в `/etc/serverwatch/serverwatch.env`
`PUBLIC_BASE_URL=https://92.5.162.96` (або вашу адресу центральної панелі)
та перезапустіть три служби. Не додавайте `/login`. Для вже встановленого
ServerWatch спочатку застосуйте оновлення й міграцію з розділу 16.

1. У панелі відкрийте **Сервери → Додати сервер**: назва, публічна IPv4, SSH-порт,
   адміністративний користувач встановлення (`root` або користувач із sudo),
   клієнт та інтервал. Telegram Group ID можна залишити порожнім.
2. Натисніть **Створити сервер**. Python створить індивідуальний Ed25519 та
   зашифрує приватний ключ. Він залишається на центральному VPS й не показується.
3. На другому кроці натисніть **Скопіювати весь скрипт** або **Завантажити .sh**.
   Скрипт містить секретний одноразовий токен на одну годину: не публікуйте його.
4. Зі свого комп’ютера підключіться SSH до **клієнтського сервера** як вказаний
   адміністративний користувач. Вставте повний скрипт у Linux Bash або запустіть
   завантажений файл через Bash. Вручну дописувати ключі не потрібно.
5. Скрипт створить обмежений обліковий запис `monitor`, збирач і SSH dispatcher,
   збереже сторонні ключі, перевірить sshd та надішле HTTPS callback із host key.
   Підтримуються Ubuntu/Debian із systemd; потрібен root або sudo.
6. Поверніться до майстра. Worker сам перевірить SSH і фактичний збір метрик.
   Лише після цього з’явиться READY і відкриється третій крок.
7. Скопіюйте окремий 15-значний Telegram-код: чинний 24 години, одноразовий,
   у БД зберігається тільки хеш.
8. Додайте бота до групи. Від головного Telegram-адміністратора надішліть
   `/start`, потім одним повідомленням `/bind ВАШ_15_ЗНАЧНИЙ_КОД`.
   Команда `/bind` працює з privacy mode; голі цифри можуть бути приховані.
9. Після підтвердження стан стане ACTIVE. Виконайте `/status` і `/report`,
   перевірте час свіжої метрики та графіки у панелі.

Стани встановлення видно в списку й картці сервера. При помилці або простроченні
у майстрі можна видати новий скрипт: старий токен анулюється, ключ зберігається,
повторний запуск не дублює authorized_keys. Використаний токен повторно не активується.
Callback тільки запускає перевірку: його отримання ще не доводить працездатність SSH.

Уже підключені сервери продовжують працювати зі своїми попередніми ключами,
SSH-користувачами й `known_hosts`; переналаштовувати їх не потрібно. Для нових
host key автоматично закріплюється в БД через одноразовий HTTPS callback.
Зміна закріпленого ключа автоматично заборонена.

Повна інструкція, установлені файли й усі стани: [docs/onboarding.md](docs/onboarding.md).
Група може мати кілька серверів. /status показує останній знімок; /report також
запитує свіжу перевірку. Початковий інтервал — 180 секунд, автоматичний звіт —
4 години. Сирі дані — 24 години, погодинні середні/максимуми — 90 днів.

## 12. Необов’язковий SWAP-helper

Для нових серверів виберіть опцію SWAP на першому кроці майстра: скрипт сам
встановить helper і sudo-правило для `monitor` лише на його запуск без аргументів.
Загальний sudo не надається; сам SWAP під час встановлення не створюється.
Наведені нижче ручні команди призначені **тільки для раніше підключених серверів**.

Моніторинг не потребує sudo. Якщо потрібна кнопка SWAP для старого підключення, передайте
scripts/serverwatch-swap та deploy/serverwatch-swap.sudoers через власний
адміністративний SSH на **клієнтський сервер**. У каталозі переданих файлів
установіть helper і перевірте sudoers; сам helper для smoke test не запускайте.

```bash
sudo install -o root -g root -m 0755 serverwatch-swap /usr/local/sbin/serverwatch-swap
sudo visudo -cf serverwatch-swap.sudoers
sudo install -o root -g root -m 0440 serverwatch-swap.sudoers /etc/sudoers.d/serverwatch-swap
sudo visudo -c
sudo -l -U serverwatch
```

Дозволено лише /usr/local/sbin/serverwatch-swap **без аргументів**, не ALL/shell/Python.
Якщо SSH-логін інший, змініть sudoers перед встановленням. Кнопки доступні тільки
числовому TELEGRAM_ADMIN_ID. «Займаюсь цим» записує адміністратора і час.
SWAP-кнопка створює окреме завдання: helper перевіряє диск, поточний SWAP і flock,
додає один файл 1 GiB та підтверджує /proc/swaps. Старий SWAP не змінюється.

Додатковий SWAP діє **до reboot клієнта**, /etc/fstab не редагується. Наявний
неактивний файл не перезаписується; після збою потрібна ручна перевірка.
Не натискайте реальну кнопку тільки для перевірки: це змінює клієнтський сервер.


## 13. Перевірка працездатності та автозапуску

Перевірте active/enabled усіх компонентів, конфігурацію Nginx, listeners та БД.
Саме active ще не доводить успішний SSH-збір; потрібен доданий сервер.

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

| Перевірка | Дія та очікування |
|---|---|
| Панель/TOTP | У браузері /login → пароль → TOTP; без коду dashboard закритий |
| Додавання сервера | Майстер → скрипт на клієнті → фактичні метрики → одноразовий Telegram-код |
| Метрики в MySQL | /report у Telegram, потім повторити manage.py status: новий last_success_at і час останньої метрики |
| Графіки | Сторінка сервера, вибір 24 год / 7 / 30 днів; старої історії на новому VPS ще немає |
| Telegram | /bind КОД від головного ID → успіх; /status повертає стан |
| Доставка | /report → свіжий звіт; status показує sent>0, queued не зростає постійно |
| Аварії | Тимчасово понизити один поріг у панелі, дочекатися перевірки, повернути поріг |
| «Займаюсь цим» | Головний ID → успіх, сторонній → відмова; status показує audit action |
| SWAP | Mock-тести перевіряють логіку; реальна кнопка змінює клієнта й натискається лише за потреби |

Браузер і кнопки Telegram перевіряються інтерактивно: systemctl не підтверджує
TOTP або права кнопок. Не виснажуйте RAM/диск для тесту. SQL нижче показує лише
статуси й часові позначки без приватних ключів.

```bash
sudo mysql --execute "SELECT id,status,last_success_at FROM serverwatch.servers; SELECT server_id,MAX(collected_at) AS last_sample,COUNT(*) AS samples FROM serverwatch.server_metrics GROUP BY server_id; SELECT status,COUNT(*) FROM serverwatch.alerts GROUP BY status;"
sudo journalctl -u serverwatch-monitor -u serverwatch-bot --since '-10 min' --no-pager
```

## 14. Автоматичний backup

Root backup підключається до MySQL через Unix socket, без пароля в CLI.
Скрипт має set -euo pipefail, flock, приватний тимчасовий файл і gzip --test:
невдалий mysqldump не стане фінальним архівом. --single-transaction забезпечує
узгоджений знімок InnoDB, але міграції одночасно з backup не запускайте.
Встановлена root-owned копія відокремлена від робочого репозиторію.

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

Timer виконує backup близько 03:15 UTC з затримкою до 5 хвилин, Persistent
компенсує пропущений запуск. Архіви не видаляються автоматично. Переносьте їх
у власне захищене зовнішнє сховище, перевіряйте відновлення та очищуйте зайві
локальні копії після перевірки зовнішніх. Той самий SSD не захищає від втрати VPS.

Оберіть створений дамп і перевірте gzip; timestamp — ваш параметр.

```bash
export BACKUP='/var/backups/serverwatch/serverwatch-YOUR_TIMESTAMP.sql.gz'
sudo gzip --test "$BACKUP"
```

### Окрема копія environment і ключа шифрування

**Без початкового ENCRYPTION_KEY неможливо розшифрувати SSH-ключі й TOTP з дампу.**
Не генеруйте нового ключа при відновленні. Збережіть environment, migration.env
та known_hosts окремо від SQL. Root-only archive шифрується GPG перед перенесенням.
Парольну фразу GPG введіть інтерактивно й зберігайте окремо; не використовуйте
--passphrase у команді. Каталог не повинен бути під nginx/static.

```bash
sudo install -d -o root -g root -m 0700 /var/backups/serverwatch-secrets
sudo bash -c 'umask 077; tar -C /etc/serverwatch -czf /var/backups/serverwatch-secrets/config.tar.gz serverwatch.env migration.env known_hosts'
sudo gpg --symmetric --cipher-algo AES256 --output /var/backups/serverwatch-secrets/config.tar.gz.gpg /var/backups/serverwatch-secrets/config.tar.gz
sudo stat -c '%U:%G %a %n' /var/backups/serverwatch-secrets/config.tar.gz.gpg
```

Перенесіть назовні тільки .gpg. Після перевірки зашифрованої копії видаліть
тимчасовий відкритий archive цією точною командою; робочий environment залишається.

```bash
sudo rm -- /var/backups/serverwatch-secrets/config.tar.gz
```

### Перевірка після reboot

Після налаштування й backup перевірте reboot. Команда перерве SSH: спочатку
переконайтеся в доступності консолі провайдера. Після завантаження повторно
підключіться до VPS; локальний SSH-тунель теж потрібно відкрити заново.

```bash
sudo reboot
```

Після повторного входу перевірте автозапуск і нові дані. Не запускайте worker
вручну поряд зі службою для «відновлення».

```bash
sudo systemctl is-active mysql nginx serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl is-enabled serverwatch-web serverwatch-bot serverwatch-monitor serverwatch-backup.timer
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
sudo journalctl -b -u serverwatch-web -u serverwatch-bot -u serverwatch-monitor -n 100 --no-pager
```

## 15. Відновлення

На новому VPS виконайте пакети, користувача, **сумісну версію коду**, venv і
loopback MySQL із розділів 2–5. Не запускайте setup_environment.py: потрібні
оригінальні секрети. На наявному VPS перед імпортом створіть backup поточного
стану та зупиніть служби. На новій машині невстановлені служби зупиняти не потрібно.

```bash
sudo systemctl stop serverwatch-backup.timer serverwatch-backup.service
sudo systemctl stop serverwatch-web serverwatch-bot serverwatch-monitor
```

Завантажте свій .gpg у root-only каталог із розділу 14. Розшифруйте, перегляньте
**лише список імен**; archive має містити тільки serverwatch.env, migration.env,
known_hosts без сторонніх шляхів. Не розпаковуйте недовірені tar-файли.

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

Дамп таблиць **не містить MySQL-ролей**. Тільки на новому VPS відновіть їх
зі старими паролями з URI командою нижче. Скрипт передає паролі SQL-параметрами,
не друкує їх і відмовляється змінювати вже наявні ролі. На старому VPS із чинними
ролями цей крок пропустіть.

```bash
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/restore_database_users.py
```

Виберіть власний SQL-дамп, перевірте gzip й імпортуйте з pipefail. Імпорт замінює
дані однойменних таблиць: перевірте шлях. Ревізія схеми має відповідати коду;
старий дамп спочатку відновлюйте на сумісну версію, потім застосовуйте upgrade.

```bash
export BACKUP='/var/backups/serverwatch/serverwatch-YOUR_TIMESTAMP.sql.gz'
sudo gzip --test "$BACKUP"
sudo mysql --execute "CREATE DATABASE IF NOT EXISTS serverwatch CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
sudo bash -o pipefail -c 'gzip -dc -- "$1" | mysql serverwatch' bash "$BACKUP"
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py check-db
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py upgrade
```

На новому VPS встановіть unit/Nginx/backup файли за розділами 8–10 та 14. Потім
запустіть служби й повторіть checklist розділу 13. Для зміненого IP перевірте
DNS, UFW, клієнтські from= і known_hosts.

```bash
sudo systemctl daemon-reload
sudo systemctl start serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl start serverwatch-backup.timer
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
sudo journalctl -u serverwatch-monitor -n 100 --no-pager
```

Перервана SSH-перевірка відновиться після lease до 10 хвилин. Перерваний SWAP
автоматично не повторюється: перевірте клієнтський стан вручну.

## 16. Оновлення та відновлення TOTP

Спочатку перевірте новий код на staging, створіть backup БД та секретів.
Зупиніть timer і процеси перед DDL. pull --ff-only не має знищувати локальні зміни.
При змінах deploy перевстановіть відповідні файли, перевірте nginx/unit-файли
й daemon-reload; наведений блок сам по собі встановлені конфігурації не копіює.

```bash
sudo systemctl start serverwatch-backup.service
sudo systemctl stop serverwatch-backup.timer
sudo systemctl stop serverwatch-web serverwatch-bot serverwatch-monitor
sudo -u serverwatch git -C /opt/serverwatch pull --ff-only
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip install -r /opt/serverwatch/requirements.txt
sudo -u serverwatch /opt/serverwatch/.venv/bin/python -m pip check
sudo /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py upgrade
sudo systemctl daemon-reload
sudo systemctl start serverwatch-web serverwatch-bot serverwatch-monitor
sudo systemctl start serverwatch-backup.timer
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py status
```

Міграція `4e2c9d8f610a` додає поля ключів і встановлення, зберігає попередні
SSH-ключі, Telegram-групи та історію. Перед першим використанням нового майстра
додайте `PUBLIC_BASE_URL=https://ВАША_IP_АБО_ДОМЕН` через `sudoedit /etc/serverwatch/serverwatch.env` та перезапустіть web, bot і monitor.
На вашому VPS це `PUBLIC_BASE_URL=https://92.5.162.96`. `setup_environment.py`
повторно не запускайте. Нове встановлення запропонує адресу під час початкового
створення environment. Без адреси наявні сервери працюватимуть, але створення
нових через майстер покаже пояснення.

Не міняйте ENCRYPTION_KEY: ротація потребує окремого перешифрування. При втраті
Google Authenticator з довіреного shell скиньте TOTP тільки власного логіна;
після пароля знову з’явиться QR. Це відновлення доступу, не звичайний спосіб входу.

```bash
sudo -u serverwatch /opt/serverwatch/.venv/bin/python /opt/serverwatch/scripts/manage.py reset-totp --username YOUR_ADMIN_LOGIN
```

## 17. Troubleshooting

У відносних командах нижче каталог — /opt/serverwatch. Не публікуйте environment.
Не виправляйте проблеми через chmod 777, root-runtime, sudo pip або відкриття 3306.

| Помилка | Ймовірна причина | Команда діагностики | Виправлення |
|---|---|---|---|
| MySQL не запускається | cnf, нестача RAM/диска | `sudo journalctl -u mysql -n 100 --no-pager`; `sudo mysqld --validate-config`; `df -h`; `free -h` | Виправити cnf/ресурси, restart mysql |
| Access denied for user | Пароль, host або роль | `sudo mysql --execute "SELECT user,host FROM mysql.user WHERE user LIKE 'serverwatch%';"`; `sudo -u serverwatch .venv/bin/python scripts/manage.py check-db` | Узгодити пароль/URI/роль 127.0.0.1, не використовувати root runtime |
| Unknown database | Не застосовано bootstrap або помилковий URI | `sudo mysql --execute "SHOW DATABASES LIKE 'serverwatch';"` | Перше встановлення: імпорт bootstrap; відновлення: розділ 15 |
| SQLAlchemy не підключається | MySQL вимкнений, URI/driver | `sudo systemctl is-active mysql`; `.venv/bin/python -m pip check`; `sudo -u serverwatch .venv/bin/python scripts/manage.py check-db` | mysql+pymysql, правильні credentials і loopback |
| Flask не запускається | Секрети, Fernet, схема | `sudo journalctl -u serverwatch-web -n 100 --no-pager`; `sudo stat /etc/serverwatch/serverwatch.env` | Відновити environment, upgrade; не генерувати новий ключ для чинної БД |
| ModuleNotFoundError | Інший Python або venv | `.venv/bin/python -m pip check`; `sudo systemctl cat serverwatch-web` | Встановити requirements у фактичний venv, перевірити WorkingDirectory |
| Gunicorn не знаходить application | Невірний модуль/каталог | `ls -l /opt/serverwatch/run.py`; `sudo systemctl cat serverwatch-web` | Точка run:app, WorkingDirectory=/opt/serverwatch |
| Nginx 502 | Web/socket/права | `sudo systemctl status serverwatch-web`; `sudo namei -l /run/serverwatch/web.sock`; `sudo -u www-data curl --unix-socket /run/serverwatch/web.sock -I http://localhost/login` | Відновити unit, Group=www-data та web, без chmod 777 |
| Nginx invalid map / unknown zone | Не встановлена global zone | `sudo nginx -t`; `sudo nginx -T` | nginx-limits.conf у conf.d, proxy snippet у snippets |
| Permission denied | Права env/known_hosts/instance | `sudo namei -l /etc/serverwatch/known_hosts`; `sudo -u serverwatch test -r /etc/serverwatch/serverwatch.env` | root:serverwatch 0640, dir 0750, instance serverwatch 0700 |
| Бот не відповідає | Token/ID, інший polling/webhook/privacy | `sudo journalctl -u serverwatch-bot -n 100 --no-pager`; `/help` у Telegram | Прибрати інший процес/webhook, звірити токен/ID, використовувати /bind |
| Черга Telegram росте | Немає бота/прав або не той Group ID | `sudo -u serverwatch .venv/bin/python scripts/manage.py status`; journal bot | Відновити членство, права та прив’язку; повтори автоматичні |
| SSH connection timeout | IP/порт/мережа/client firewall | journal monitor; `ssh-keyscan -T 5 -p CLIENT_PORT CLIENT_IP` | Перевірити SSH listener і доступ із центрального IP |
| SSH authentication failed | Ключ/логін/from=/права | На клієнті `sudo journalctl -u ssh -n 100 --no-pager`; `sudo namei -l /home/serverwatch/.ssh/authorized_keys` | Виправити pubkey/права/from=, замінити ключ через панель |
| Host key unknown/changed (старі підключення) | Немає перевіреного known_hosts | `sudo -u serverwatch ssh-keygen -F CLIENT_IP -f /etc/serverwatch/known_hosts` | Незалежно звірити fingerprint, тільки тоді змінювати запис |
| Monitor не пише метрики | Немає активних серверів, SSH/Python3/lease | `sudo -u serverwatch .venv/bin/python scripts/manage.py status`; journal monitor; на клієнті `python3 --version` | Перевірити is_active, SSH, Python3, інтервал; lease до 10 хв |
| Міграція падає | Runtime замість DDL, часткова схема | `sudo mysql --execute "SHOW TABLES FROM serverwatch; SELECT version_num FROM serverwatch.alembic_version;"` | upgrade через root helper; розібрати частковий DDL після backup, не stamp навмання |
| TOTP відхиляється | Час/повтор коду/секрет | `timedatectl status`, новий код | NTP, новий часовий крок; за втрати reset-totp, не заміна Fernet |
| Знову login після входу | Secure cookie на HTTP тунелю | sudoedit environment, перевірити гілку Nginx | false для loopback tunnel, true для HTTPS; restart web, новий сеанс |
| SWAP відхилено | Helper/sudo/диск/наявний файл | На клієнті `sudo -l -U serverwatch`; `df -h /var/lib`; `cat /proc/swaps` | Розділ 12; не видаляти/перезаписувати старий SWAP |
| Backup failed | Root socket auth/диск | `sudo journalctl -u serverwatch-backup.service -n 100 --no-pager`; `sudo mysql --execute 'SELECT 1'`; `df -h /var/backups` | Відновити socket auth або окремо налаштувати backup-роль; місце; повторити service |

## 18. Тести й межі перевірки

На локальній/staging-машині встановіть dev-залежності та виконайте перевірки.
Для тестів використовується SQLite та mock SSH; реальний SWAP не створюється.
Встановлювати dev-пакети на production VPS із 1 GB RAM не потрібно.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m djlint app/templates --check
.venv/bin/python -m pip check
```

Захист і межі доступу: [SECURITY.md](SECURITY.md). Деталі аудиту й фактичних
перевірок: [docs/deployment-audit.md](docs/deployment-audit.md). Python-тести,
перевірка структури команд і компіляція SQL не доводять роботу Nginx, systemd,
UFW, Telegram або MySQL на VPS. Виконайте checklist розділу 13.
