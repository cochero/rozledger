# VPS Audit Report: 185.193.19.146

Audit date: 2026-05-26 server time / 2026-05-27 IST

## Summary

This VPS is already hosting multiple applications. Do not use the RozLedger Caddy production overlay here because ports `80` and `443` are already owned by Nginx.

Recommended safe deployment:

```text
RozLedger Docker web -> 127.0.0.1:18080
Existing Nginx -> proxy rozledger.in to 127.0.0.1:18080
```

## Key Findings

- VPS IP: `185.193.19.146`
- OS: Ubuntu 24.04.4 LTS
- Docker is installed: `Docker version 29.2.1`
- Docker Compose is installed: `Docker Compose version v5.1.0`
- Nginx is active and owns public ports `80` and `443`
- Firewall allows only `22`, `80`, and `443`
- Existing public app on port `8000`: `truvomms.service`
- Existing local apps:
  - `127.0.0.1:3000`
  - `127.0.0.1:8001`
  - `127.0.0.1:8005`
  - `127.0.0.1:8100`
- Existing databases/services:
  - MySQL on `127.0.0.1:3306`
  - PostgreSQL on `127.0.0.1:5432`
  - Redis on `127.0.0.1:6379`

## Existing Docker Containers

The VPS already has a TruvoHealth Docker stack:

```text
truvohealth_backend
truvohealth_frontend
truvohealth_celery
truvohealth_celery_beat
truvohealth_daphne
truvohealth_redis
truvohealth_db
```

Do not run global Docker cleanup commands.

Avoid:

```bash
docker system prune
docker volume prune
docker network prune
docker compose down
```

Unless you are inside the RozLedger project folder and targeting only RozLedger.

## Existing Nginx Sites

Detected server names include:

```text
cap.skilldeveloper.org
csp.prohubprocess.com
erp5.klickevents.in
gst.celse.in
klickevents.in
oneday.celselabs.com
skilldeveloper.org
www.skilldeveloper.org
mms.truvo.ae
health.truvo.ae
www.health.truvo.ae
```

Do not edit these existing site files.

Add a new file only:

```text
/etc/nginx/sites-available/rozledger.in
```

Then symlink it:

```text
/etc/nginx/sites-enabled/rozledger.in
```

## Domain DNS Status

Initial DNS lookup from Windows showed:

```text
rozledger.in  -> 127.0.0.1
rozledger.com -> 127.0.0.1
```

That was not correct for production. DNS was later fixed and verified:

```text
rozledger.in      A 185.193.19.146
www.rozledger.in  A 185.193.19.146
rozledger.com     A 185.193.19.146
www.rozledger.com A 185.193.19.146
```

Production canonical routing:

```text
https://rozledger.in
```

All HTTP, `www`, and `.com` requests redirect to `https://rozledger.in`.

## Safe RozLedger Deployment Plan

1. Clone repo to:

```text
/opt/rozledger
```

2. Create server-only `.env.docker`.

3. Run RozLedger bound to loopback only:

```bash
docker compose -f docker-compose.yml -f docker-compose.vps-nginx.yml up -d --build
```

This exposes:

```text
127.0.0.1:18080
```

It does not expose a public Docker port.

4. Add new Nginx config from:

```text
ops/nginx_rozledger.conf
```

5. Validate Nginx:

```bash
nginx -t
```

6. Reload Nginx only after validation:

```bash
systemctl reload nginx
```

7. Add HTTPS with Certbot only for RozLedger domains:

```bash
certbot --nginx -d rozledger.in -d www.rozledger.in -d rozledger.com -d www.rozledger.com
```

8. Keep `rozledger.in` as the canonical host. Use `ops/nginx_rozledger.conf` as the production Nginx layout after certificates exist.

## Do Not Use On This VPS

Do not run:

```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Reason: that production overlay starts Caddy and tries to bind ports `80` and `443`, which would conflict with existing Nginx sites.
