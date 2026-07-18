FROM node:26-bookworm-slim AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_NAIVE_API_URL=http://localhost:8001
ARG VITE_STREAM_API_URL=http://localhost:8002
ENV VITE_NAIVE_API_URL=$VITE_NAIVE_API_URL \
    VITE_STREAM_API_URL=$VITE_STREAM_API_URL
RUN npm run build

FROM nginx:1.29-alpine
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
