FROM node:24.14.0-alpine3.23
WORKDIR /app
COPY --chown=10001:10001 package.json server.js ./
COPY --chown=10001:10001 public ./public
USER 10001:10001
EXPOSE 8080
CMD ["node", "server.js"]
