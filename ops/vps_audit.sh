#!/usr/bin/env bash
set -u

echo "=== RozLedger VPS Non-Destructive Audit ==="
date -Is
echo

echo "=== Host ==="
hostnamectl 2>/dev/null || hostname
uname -a
echo

echo "=== Current User ==="
id
echo

echo "=== IP Addresses ==="
ip -brief addr || true
echo

echo "=== Listening Ports ==="
ss -tulpen || true
echo

echo "=== Web Server Processes ==="
ps aux | grep -Ei 'nginx|apache2|httpd|caddy|traefik|haproxy' | grep -v grep || true
echo

echo "=== System Services: Web/Proxy/Docker ==="
for svc in nginx apache2 httpd caddy traefik docker containerd; do
  systemctl is-active "$svc" >/dev/null 2>&1 && systemctl status "$svc" --no-pager -l | sed -n '1,12p'
done
echo

echo "=== Firewall ==="
ufw status verbose 2>/dev/null || true
iptables -S 2>/dev/null | sed -n '1,80p' || true
echo

echo "=== Docker Version ==="
docker --version 2>/dev/null || true
docker compose version 2>/dev/null || docker-compose --version 2>/dev/null || true
echo

echo "=== Docker Containers ==="
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}\t{{.Status}}' 2>/dev/null || true
echo

echo "=== Docker Networks ==="
docker network ls 2>/dev/null || true
echo

echo "=== Docker Volumes ==="
docker volume ls 2>/dev/null || true
echo

echo "=== Compose Files Under Common Locations ==="
find /opt /srv /home /var/www -maxdepth 4 \( -name 'docker-compose.yml' -o -name 'compose.yml' -o -name 'docker-compose.yaml' -o -name 'compose.yaml' \) 2>/dev/null || true
echo

echo "=== Nginx Config Files ==="
find /etc/nginx -maxdepth 4 -type f 2>/dev/null | sort || true
echo

echo "=== Apache Config Files ==="
find /etc/apache2 -maxdepth 4 -type f 2>/dev/null | sort || true
echo

echo "=== Caddy Config Files ==="
find /etc/caddy -maxdepth 4 -type f 2>/dev/null | sort || true
echo

echo "=== Existing Web Roots ==="
find /var/www /srv /opt -maxdepth 3 -type d 2>/dev/null | sort | sed -n '1,160p' || true
echo

echo "=== Disk Usage ==="
df -h
echo

echo "=== Memory ==="
free -h
echo

echo "=== Audit Complete ==="
