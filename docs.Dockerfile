# Standalone docs image: builds the MkDocs Material site and serves it with
# nginx. Published as ghcr.io/jacobbista/kelt-docs with its OWN release
# lifecycle (built by .github/workflows/docs-site.yml on docs changes), so the
# in-dashboard docs stay fresh independently of the dashboard-frontend tag and
# are available offline / LAN-only (no dependency on the public Pages site).
FROM python:3.12-slim AS build
WORKDIR /src
RUN pip install --no-cache-dir mkdocs-material
COPY mkdocs.yml ./
COPY docs ./docs
RUN mkdocs build -d /site

FROM nginx:1.27-alpine
COPY --from=build /site /usr/share/nginx/html
# nginx default serves /usr/share/nginx/html on :80. The dashboard frontend
# reverse-proxies /docs/ to this service (prefix stripped); Material uses
# relative internal links so it serves correctly under that sub-path.
EXPOSE 80
