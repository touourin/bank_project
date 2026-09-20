FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2.11.4-alpine
ENV XDG_CONFIG_HOME=/tmp/caddy_config XDG_DATA_HOME=/tmp/caddy_data
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/dist /srv
USER 65532:65532
EXPOSE 8080
