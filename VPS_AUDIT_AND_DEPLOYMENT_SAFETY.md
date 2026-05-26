# VPS Audit and Safe Deployment Plan

Target VPS:

```text
185.193.19.146
```

Goal:

Deploy RozLedger without breaking existing websites or Docker applications.

## Rules

- Do not change existing Nginx, Apache, Caddy, Traefik or HAProxy configs during audit.
- Do not stop, restart, remove or recreate existing containers.
- Do not bind RozLedger directly to ports `80` or `443` until the existing reverse proxy layout is known.
- Do not run `docker compose down` outside the RozLedger folder.
- Do not delete Docker networks or volumes except RozLedger-specific ones.
- Keep RozLedger in its own folder, for example `/opt/rozledger`.
- Keep RozLedger on an internal app port first, for example `127.0.0.1:18080`.

## Audit Command

Copy and run:

```bash
bash ops/vps_audit.sh | tee rozledger_vps_audit_$(date +%Y%m%d_%H%M%S).log
```

This script is read-only. It lists:

- Listening ports
- Existing web servers and reverse proxies
- Docker containers, networks and volumes
- Existing Compose files
- Nginx/Apache/Caddy config file locations
- Firewall status
- Disk and memory

## Safe Deployment Shape

First deploy RozLedger behind local-only port:

```text
127.0.0.1:18080 -> rozledger_web:8000
```

Then connect the public domain through the existing reverse proxy.

Preferred domain routing:

```text
rozledger.in -> RozLedger
www.rozledger.in -> redirect to rozledger.in
rozledger.com -> redirect to rozledger.in
www.rozledger.com -> redirect to rozledger.in
```

## If VPS Already Has Nginx

Add a new isolated server block only for RozLedger:

```nginx
server {
    listen 80;
    server_name rozledger.in www.rozledger.in rozledger.com www.rozledger.com;

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Only reload Nginx after:

```bash
sudo nginx -t
```

## If VPS Already Has Caddy

Add only a new site block for RozLedger:

```caddy
rozledger.in {
  reverse_proxy 127.0.0.1:18080
}

www.rozledger.in, rozledger.com, www.rozledger.com {
  redir https://rozledger.in{uri} permanent
}
```

Only reload Caddy after:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

## If VPS Has No Reverse Proxy

Use the included production Caddy overlay:

```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Only use this if ports `80` and `443` are free.

## Pre-Deployment Checklist

- Audit saved.
- Existing port owners documented.
- Existing containers documented.
- Reverse proxy identified.
- DNS points to the VPS.
- `.env.docker` created on the VPS.
- `DJANGO_ALLOWED_HOSTS` includes all RozLedger domains.
- `DJANGO_CSRF_TRUSTED_ORIGINS` includes `https://rozledger.in`.
- `ROZLEDGER_PUBLIC_URL=https://rozledger.in`.

## Rollback

If RozLedger causes issues:

```bash
cd /opt/rozledger
docker-compose stop web
```

If only the reverse proxy route was added, remove just the RozLedger route and reload the proxy after config validation.
