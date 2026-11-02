# Аудит розгортання ServerWatch — 2026-11-02

Перевірено requirements.txt, .env.example, app/config.py, app/extensions.py,
app.create_app(), усі SQLAlchemy-моделі, Alembic environment/початкову ревізію,
app/cli.py, run.py, bot/main.py, monitoring/worker.py, deploy та README.

## Виявлені проблеми та виправлення

| Проблема | Результат |
|---|---|
| Домен був обов’язковим у документації | Основний сценарій без домену: loopback Nginx та SSH-тунель; HTTPS окремо |
| Secure cookie заважав би HTTP-тунелю | Генератор задає false тільки для tunnel, true для HTTPS; описані межі |
| Несумісний імпорт datetime.UTC на Python 3.10 | Три імпорти замінено на timezone.utc, Ruff target py310; README охоплює системний Python Ubuntu 22.04/24.04 |
| Секрети друкувалися в термінал і вручну переносилися | Генератор записує секрети й SQL у приватні файли без виведення |
| Shell source environment мав ризик помилок екранування | Manage-helper читає dotenv і запускає справжні Flask-команди без shell |
| MySQL-встановлення без явної послідовності перевірок | Install, validate-config, enable, loopback, root socket, створення БД/ролей, grants, Python SELECT 1 |
| DDL-роль незручно передавалася в shell | Окремий root-only migration.env; helper знижує права до запуску Flask |
| Немає лімітів Nginx для входу | Global map/limit_req_zone та ліміт POST /login, /totp, HTTP 429 |
| Scheduler залежав лише від дисципліни оператора | Monitor-unit запускається через flock; прямий дублюючий запуск заборонений інструкцією |
| Backup pipeline міг приховати падіння mysqldump | Pipefail, flock, приватний temp, gzip --test, atomic rename, systemd timer |
| Dump не відновлює MySQL-користувачів | Окремий restore helper зі старими URI; відмова змінювати наявні ролі |
| Перевірки після reboot та діагностика були неповними | Окремий checklist, журнали, стан socket/метрик/черги, таблиця troubleshooting |

README містить повний вміст трьох unit-файлів. Тест звіряє їх із deploy,
перевіряє посилання на scripts/deploy та локальну документацію. Додано тести
генерації environment, розділення runtime/DDL, відновлення ролей через SQL-параметри,
відмови за наявних ролей та властивостей backup/loopback-конфігурації.

Додатково виправлено CLI-повідомлення create-admin: воно тепер вказує SSH-тунель
або HTTPS, замість вимоги домену/HTTPS для будь-якого встановлення. Firewall
описаний перед TLS, reboot-перевірка — після створення резервних копій.

## Змінені та додані файли

| Файли | Зміна |
|---|---|
| README.md | Повна нова інструкція, два способи доступу, діагностика та відновлення |
| SECURITY.md | Межі SSH-тунелю, runtime/DDL, provisioning і backup |
| .env.example | Пояснення розташування environment і Secure cookie |
| .gitattributes | LF для executable backup-скрипта |
| app/cli.py | Повідомлення про вхід підтримує сценарій без домену |
| deploy/nginx.conf | IPv6, спільний proxy snippet, ліміти входу |
| deploy/nginx-tunnel.conf | Новий loopback server |
| deploy/nginx-limits.conf | Нова global map та rate limit zone |
| deploy/nginx-proxy.conf | Новий спільний proxy snippet |
| deploy/serverwatch-monitor.service | Flock навколо scheduler |
| deploy/serverwatch-backup.service | Нова root backup-служба |
| deploy/serverwatch-backup.timer | Новий щоденний persistent timer |
| scripts/__init__.py | Імпорти deployment-модулів для тестів |
| scripts/setup_environment.py | Генерація секретів, environment та SQL bootstrap |
| scripts/manage.py | Environment, перевірки БД, Flask-команди, Telegram ID |
| scripts/restore_database_users.py | Відновлення ролей на новому MySQL зі старими паролями |
| scripts/serverwatch-backup | Захищений backup pipeline |
| tests/test_deployment.py | Deployment-тести та перевірка Bash-блоків |
| requirements-dev.txt | Bashlex для статичного синтаксичного розбору |
| docs/deployment-audit.md | Цей звіт |

## Що перевіряється локально

- pytest: чинні прикладні тести та нові deployment-тести з mock, SQLite у пам’яті.
- Ruff: Python-код і форматування.
- djlint: чинні Jinja-шаблони.
- pip check: сумісність установлених Python-залежностей.
- Alembic: застосування/відкат на SQLite; SQL для MySQL можна компілювати offline.
- Зіставлення README з фактичними файлами та точками входу.
- Bashlex: статичний розбір Bash-блоків README та backup-скрипта; quoted heredoc
  delimiters нормалізуються через відоме обмеження bashlex 0.18. Це не bash -n
  і не виконання команд у Linux.

Початковий локальний прогін: **49 passed**, із них 10 deployment-тестів.
Перевірено 53 Bash-блоки README та backup-скрипт. Ruff check/format, djlint і
pip check завершилися успішно. Міграцію окремо скомпільовано в MySQL SQL без
відкриття з’єднання з MySQL. Застосування й відкат міграції виконано лише на SQLite.

## Виправлення для Python 3.10

На VPS підтверджено Python 3.10.12 і ImportError для datetime.UTC. Імпорт
замінено на timezone.utc в app/models/__init__.py, monitoring/collectors.py
та monitoring/worker.py. Ruff target у pyproject.toml змінено на py310;
README тепер містить мінімальну вимогу 3.10+ і системні версії обох Ubuntu.
Додано tests/test_python_compatibility.py: синтаксис Python 3.10 та імпорт
моделей/моніторингу без datetime.UTC. БД, секрети та схема не змінювалися.

Після виправлення **51 тест пройшов на справжньому Python 3.10.22 та на Python
3.14.6** в окремих локальних Windows-середовищах. Це підтверджує Python-сумісність,
але не є перевіркою розгортання Linux або точної patch-версії 3.10.12 на VPS.

## Що не виконувалося

Встановлення на чистому Ubuntu, запуск справжніх MySQL/Nginx/systemd/UFW/Fail2ban,
випуск сертифіката, reboot VPS, Telegram polling та реальні SSH/SWAP/backup дії
не виконувалися. Робоче середовище аудиту — Windows; WSL не встановлений.
Статичні перевірки команд не є виконанням цих команд у Linux. Перед production
потрібен прохід README на staging/VPS і checklist із розділу 13.
