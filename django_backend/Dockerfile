FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends default-libmysqlclient-dev build-essential pkg-config fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY django_backend/requirements.txt /app/django_backend/requirements.txt
RUN pip install --no-cache-dir -r /app/django_backend/requirements.txt

COPY index.html index-us.html app.js styles.css content.html content-us.html blog.html pricing.html pricing-us.html privacy.html terms.html contact.html contact-us.html sitemap.xml robots.txt rozledger-logo.png /app/
COPY pages /app/pages
COPY videos /app/videos
COPY build_scripts /app/build_scripts
COPY django_backend /app/django_backend

WORKDIR /app/django_backend

RUN chmod +x /app/django_backend/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/django_backend/docker-entrypoint.sh"]
CMD ["gunicorn", "rozledger.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
