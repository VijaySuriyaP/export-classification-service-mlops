# Web client (A8): static page + same-origin reverse proxy to the API. Runs as non-root on 8080.
FROM nginxinc/nginx-unprivileged:1.27.5-alpine

COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY web/index.html web/app.js web/styles.css /usr/share/nginx/html/
USER 101

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
