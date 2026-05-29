# Multi-stage build for the dashboard frontend.
#
# Stage 1 compiles the Vite bundle. Stage 2 serves the static dist/ output
# with nginx. All runtime configuration (Keycloak authority, dev-mode link,
# reverse-proxy targets for /api, /auth, /watchdog) is delivered at deploy
# time via ConfigMap mounts. The image itself is generic and ships without
# any operator-specific values baked in.

FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# Default nginx config is overridden by the ConfigMap mounted at
# /etc/nginx/conf.d/default.conf so the listen port, reverse-proxy targets,
# and CORS headers are decided per deployment, not per image.
EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
