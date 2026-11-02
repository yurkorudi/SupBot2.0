# Панель за публічною IP-адресою без SSH-тунелю

Після виконання цього сценарію браузер відкриватиме
`https://92.5.162.96/login` напряму, з довіреним сертифікатом, без тунелю.
Це IP-адреса, не домен. VPS продовжує запускати панель; змінюється спосіб доступу.
Якщо адреса VPS інша, замініть тільки SERVER_IP нижче. Адреса має належати вашому
VPS і бути доступною з інтернету на TCP 80/443; forwarding провайдера теж має це дозволяти.

За [офіційною інструкцією Let's Encrypt](https://letsencrypt.org/2026/03/11/shorter-certs-certbot.html)
Certbot 5.4+ підтримує IP через webroot і профіль shortlived. Сертифікат чинний
6 днів, тому timer поновлення — обов’язковий. Стара apt-версія Certbot у Ubuntu
22.04/24.04 може не підтримувати ці прапорці; нижче використовується окремий venv.
Цей сценарій не використовує `certbot --nginx` для видачі IP-сертифіката.

## 1. На якому кроці переходити

Спочатку виконайте README до запуску трьох systemd-служб включно: MySQL, environment,
міграції, create-admin, serverwatch-web/bot/monitor. Потім виконайте firewall-розділ
README з ACCESS_MODE=https. Гілки Nginx для тунелю та домену пропустіть і використайте
цю інструкцію. Якщо тунель уже налаштований, наведені далі кроки замінять сайт
serverwatch, без зміни БД, логінів, TOTP та ключа шифрування.

Усі наступні Bash-команди виконуйте **на VPS** від свого sudo-користувача.
SSH-порт і чинні дозволи SSH залиште відкритими; не запускайте ufw reset.
Оновлення Git виконується власником репозиторію.

```bash
sudo -u serverwatch git -C /opt/serverwatch pull --ff-only
export SERVER_IP='92.5.162.96'
export ACCESS_MODE='https'
sudo systemctl is-active serverwatch-web
sudo ufw allow 'Nginx Full'
sudo ufw status verbose
```

Якщо UFW ще не ввімкнений, спочатку виконайте SSH-перевірки й firewall-розділ
README. Не вмикайте firewall без дозволу чинного SSH-порту. У панелі провайдера
також дозвольте TCP 80 і 443. Порти 8000/8080 та MySQL не відкривайте.

## 2. Новий Certbot в окремому середовищі

Установіть засоби venv та Certbot 5.4+ окремо від залежностей застосунку. Команди
виконує root лише для встановлення ACME-клієнта; Python-процеси ServerWatch
продовжують працювати як serverwatch.

```bash
sudo apt update
sudo apt install -y python3-venv ca-certificates
sudo python3 -m venv /opt/serverwatch-certbot
sudo /opt/serverwatch-certbot/bin/python -m pip install --upgrade pip
sudo /opt/serverwatch-certbot/bin/python -m pip install 'certbot>=5.4,<6'
sudo /opt/serverwatch-certbot/bin/certbot --version
```

## 3. HTTP тільки для перевірки володіння IP

Встановіть спільні конфігурації та webroot. Початковий сайт обслуговує лише
ACME-challenge, а для панелі повертає 404: пароль і ключі через HTTP не передаються.
Це очікуваний стан до завершення кроку 5.

```bash
sudo install -d -o root -g root -m 0755 /var/lib/serverwatch-acme/.well-known/acme-challenge
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-proxy.conf /etc/nginx/snippets/serverwatch-proxy.conf
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/nginx-limits.conf /etc/nginx/conf.d/serverwatch-limits.conf
sed "s/__SERVER_IP__/$SERVER_IP/g" /opt/serverwatch/deploy/nginx-ip-bootstrap.conf | sudo tee /etc/nginx/sites-available/serverwatch >/dev/null
if test -L /etc/nginx/sites-enabled/default; then sudo unlink /etc/nginx/sites-enabled/default; fi
sudo ln -sfn /etc/nginx/sites-available/serverwatch /etc/nginx/sites-enabled/serverwatch
sudo nginx -t
sudo systemctl reload nginx
```

Перевірте webroot локально: має повернути serverwatch-acme-ok. Для перевірки
ззовні відкрийте `http://92.5.162.96/.well-known/acme-challenge/serverwatch-check`
зі свого комп’ютера; результат має бути той самий. Тестовий рядок не є секретом.

```bash
printf '%s\n' 'serverwatch-acme-ok' | sudo tee /var/lib/serverwatch-acme/.well-known/acme-challenge/serverwatch-check >/dev/null
curl --fail -H "Host: $SERVER_IP" http://127.0.0.1/.well-known/acme-challenge/serverwatch-check
```

## 4. Видача сертифіката

Certbot запитає email та згоду з умовами. Використовується production Let’s Encrypt,
ім’я lineage serverwatch-ip фіксоване й збігається зі шляхами у Nginx та timer.
За помилки перевірки IP виправте доступність порту 80/challenge й повторіть;
до наступного кроку переходьте тільки після успішної видачі.

```bash
sudo /opt/serverwatch-certbot/bin/certbot certonly \
  --webroot --webroot-path /var/lib/serverwatch-acme \
  --preferred-profile shortlived \
  --ip-address "$SERVER_IP" --cert-name serverwatch-ip
sudo test -s /etc/letsencrypt/live/serverwatch-ip/fullchain.pem
sudo test -s /etc/letsencrypt/live/serverwatch-ip/privkey.pem
```

Не додавайте --staging до production-команди: staging-сертифікат не є довіреним
для браузера. Якщо перевіряли staging окремо, отримайте production-сертифікат
перед установленням HTTPS. Не ігноруйте попередження браузера.

## 5. Перемикання на HTTPS

Відкрийте environment та замініть **лише** SESSION_COOKIE_SECURE на true. Не
запускайте повторно setup_environment.py і не міняйте SECRET_KEY/ENCRYPTION_KEY.

```bash
sudoedit /etc/serverwatch/serverwatch.env
```

Рядок має виглядати так:

```ini
SESSION_COOKIE_SECURE=true
```

Перезапустіть web, встановіть конфігурацію з виданими сертифікатами. Порт 80
залишається для ACME та redirect, панель працює через 443. Curl має пройти без -k.

```bash
sudo systemctl restart serverwatch-web
sed "s/__SERVER_IP__/$SERVER_IP/g" /opt/serverwatch/deploy/nginx-ip-https.conf | sudo tee /etc/nginx/sites-available/serverwatch >/dev/null
sudo nginx -t
sudo systemctl reload nginx
curl --fail -I "https://$SERVER_IP/login"
```

Тепер відкрийте **https://92.5.162.96/login** на своєму комп’ютері. Увійдіть
логіном/паролем, створеним create-admin, та налаштуйте TOTP при першому вході.
Тунель для цього доступу не потрібний. Захист TOTP і ліміти входу залишаються.

## 6. Обов’язкове автоматичне поновлення

Установіть окрему службу, що використовує новий Certbot, та timer кожні 6 годин.
Після фактичного оновлення deploy-hook перевіряє Nginx і робить reload.
Перевірте ACME-renew через staging dry-run та виконання deploy-hook.

```bash
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/serverwatch-certbot.service /etc/systemd/system/serverwatch-certbot.service
sudo install -o root -g root -m 0644 /opt/serverwatch/deploy/serverwatch-certbot.timer /etc/systemd/system/serverwatch-certbot.timer
sudo systemd-analyze verify /etc/systemd/system/serverwatch-certbot.service /etc/systemd/system/serverwatch-certbot.timer
sudo /opt/serverwatch-certbot/bin/certbot renew --cert-name serverwatch-ip --dry-run --run-deploy-hooks --deploy-hook '/usr/sbin/nginx -t && /usr/bin/systemctl reload nginx'
sudo systemctl daemon-reload
sudo systemctl enable --now serverwatch-certbot.timer
sudo systemctl start serverwatch-certbot.service
sudo systemctl list-timers serverwatch-certbot.timer --all
sudo journalctl -u serverwatch-certbot.service -n 50 --no-pager
```

На цьому чистому VPS без інших сертифікатів вимкніть старий apt-timer, щоб старий
Certbot не намагався обробляти IP-lineage. Якщо на машині є інші сертифікати,
спочатку збережіть для них окрему робочу процедуру поновлення: новий timer
обслуговує тільки serverwatch-ip.

```bash
sudo systemctl disable --now certbot.timer
```

Порт 80 і webroot повинні залишатися доступними для наступних ACME-перевірок.
Періодично перевіряйте timer/журнал та дату завершення, особливо після reboot.
При зміні публічної IP потрібна нова видача сертифіката для нової адреси.

```bash
sudo systemctl is-enabled serverwatch-certbot.timer
sudo systemctl is-active serverwatch-certbot.timer
sudo openssl x509 -in /etc/letsencrypt/live/serverwatch-ip/fullchain.pem -noout -dates -ext subjectAltName
```

## Межі перевірки

Шаблони та синтаксис команд перевіряються локальними тестами. Видача сертифіката
для 92.5.162.96, відкритість портів і Nginx на вашому VPS тут не перевірялися.
Після отримання HTTPS поверніться до README: підготовка клієнтів, Telegram,
backup та перевірка після reboot виконуються далі за тією самою процедурою.
