# The web page's container, in two stages:
#   1. "build": Node turns the React + TypeScript code into plain HTML, CSS and JavaScript (dist/).
#   2. nginx, a small web server, serves those files. Node is not in the final image at all.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
