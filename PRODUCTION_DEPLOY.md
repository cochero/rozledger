# RozLedger Production Domain and HTTPS Setup

Primary domain:

```text
rozledger.in
```

Redirect domains:

```text
www.rozledger.in
rozledger.com
www.rozledger.com
```

## DNS records

At your domain registrar or DNS provider, point these records to the VPS public IP:

```text
A  @    VPS_PUBLIC_IP
A  www  VPS_PUBLIC_IP
```

Do this for both `rozledger.in` and `rozledger.com`.

## Production environment

On the VPS:

```bash
cd /home/USER/RozLedger
cp django_backend/.env.docker.example django_backend/.env.docker
nano django_backend/.env.docker
```

Use values like:

```text
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=rozledger.in,www.rozledger.in,rozledger.com,www.rozledger.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://rozledger.in,https://www.rozledger.in,https://rozledger.com,https://www.rozledger.com
ROZLEDGER_PUBLIC_URL=https://rozledger.in
```

Also set strong values for:

```text
DJANGO_SECRET_KEY
MYSQL_PASSWORD
MYSQL_ROOT_PASSWORD
DJANGO_SUPERUSER_PASSWORD
```

## Run with HTTPS

The production overlay adds Caddy, which automatically gets HTTPS certificates.

```bash
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Open:

```text
https://rozledger.in
```

Admin:

```text
https://rozledger.in/admin/
```

Sitemap:

```text
https://rozledger.in/sitemap.xml
```

## Redirect behavior

The included `Caddyfile` redirects these to `https://rozledger.in`:

```text
www.rozledger.in
rozledger.com
www.rozledger.com
```

## Important VPS notes

- Ports `80` and `443` must be free for Caddy.
- If another Nginx/Apache site already uses `80` and `443`, do not run the Caddy overlay on that server until a reverse-proxy plan is made.
- Keep staging on port `18080`.
- Use the production overlay only on the VPS where RozLedger owns the web ports.

## Google Search Console

After DNS and HTTPS work:

1. Add property: `https://rozledger.in`
2. Submit sitemap: `https://rozledger.in/sitemap.xml`
3. Request indexing for the homepage and `/content/`
