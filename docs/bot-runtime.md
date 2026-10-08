# Local bot runtime

This workspace runs TotNet's Python bot, worker, and Beat scheduler as systemd user services. PostgreSQL runs in `totnet-db-1`; the isolated Redis instance is `totnet-local-redis` on `127.0.0.1:6385`. Native Python processes can use the host's loopback SOCKS proxy on port 2080. Credentials and connection URLs remain in the untracked `.env`.

```sh
systemctl --user status totnet-bot totnet-worker totnet-beat
journalctl --user -u totnet-bot -f
systemctl --user restart totnet-bot totnet-worker totnet-beat
```

Service definitions are under `~/.config/systemd/user/totnet-*.service`. Beat state is under the ignored `var/` directory. The PostgreSQL and Redis data live in Docker volumes. The bot's configured username is discovered with Telegram `getMe`, not hardcoded.

For an immediate catalog refresh:

```sh
.venv/bin/python manage.py setup_bot --sync-plans
```

For verification:

```sh
uv sync --locked
.venv/bin/python manage.py check
.venv/bin/python manage.py test --noinput
```

Automated purchase tests use synthetic Connectix responses. Live checks authenticate and read catalogs only; no test customer or charged account is created. Paid card transfers need a real `PaymentCard` and enabled `BrandPaymentMethod` with type `card_transfer` in Django admin. The bot provides a support response when payment details are absent.

For a full Docker application deployment, stop the native bot, worker, and Beat services first to avoid duplicate pollers and schedulers. Use a container-accessible database URL instead of the native loopback URL in `.env`. The proxy must also accept connections from Docker's host gateway; rewriting localhost to `host.docker.internal` cannot reach a proxy bound only to host loopback.
