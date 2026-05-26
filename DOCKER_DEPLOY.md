# RozLedger Docker Staging Deployment

Use this for your Ubuntu staging server on the local network.

## 1. Install Docker on Ubuntu

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker $USER
```

Log out and log back in after adding your user to the `docker` group.

## 2. Copy project to the server

From your Windows machine, copy `C:\Projects\RozLedger` to the Ubuntu server. Example:

```powershell
scp -r C:\Projects\RozLedger user@YOUR_SERVER_IP:/home/user/RozLedger
```

## 3. Create Docker environment file

On Ubuntu:

```bash
cd /home/user/RozLedger
cp django_backend/.env.docker.example django_backend/.env.docker
nano django_backend/.env.docker
```

Change:

- `DJANGO_SECRET_KEY`
- `DJANGO_ALLOWED_HOSTS`
- `DJANGO_CSRF_TRUSTED_ORIGINS`
- `MYSQL_PASSWORD`
- `MYSQL_ROOT_PASSWORD`
- `DJANGO_SUPERUSER_PASSWORD`

For local staging, set:

```text
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost,YOUR_SERVER_IP
DJANGO_CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000,http://YOUR_SERVER_IP:8000
```

## 4. Build and run

```bash
docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d --build
```

Open:

```text
http://YOUR_SERVER_IP:18080
```

Admin:

```text
http://YOUR_SERVER_IP:18080/admin/
```

Health check:

```text
http://YOUR_SERVER_IP:18080/api/health
```

## 5. Useful commands

```bash
docker compose ps
docker compose logs -f web
docker compose logs -f mysql
docker compose restart web
docker compose down
```

To keep the MySQL data, use `docker compose down`.

To delete all staging data, use:

```bash
docker compose down -v
```

## 6. Test the product functions

Use the website and confirm:

- Invoice calculator updates GST and total.
- UPI link and QR code update.
- WhatsApp reminder text updates.
- `Save invoice lead` stores data.
- `Join Pro waitlist` stores data.
- Admin panel shows leads and invoices.

## 7. VPS migration later

For VPS deployment, keep Docker Compose but add:

- Domain DNS pointing to the VPS
- Nginx or Caddy reverse proxy
- HTTPS certificate
- Strong production secrets
- Database backups
- `DJANGO_DEBUG=False`
