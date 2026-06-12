# Caddy com o plugin DNS da Cloudflare (necessário para o desafio DNS-01,
# que funciona com o proxy laranja da Cloudflare ligado).
FROM caddy:2-builder AS builder
RUN xcaddy build --with github.com/caddy-dns/cloudflare

FROM caddy:2
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
