# Platter

Platter is a Flask restaurant operations app. Development and production use
the same application code; `PLATTER_ENV` selects the runtime configuration.

## Local development

Install dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`, then configure `DATABASE_URL` if you are not
using the default local SQLite database. Start the app:

```powershell
.\.venv\Scripts\python.exe run.py
```

Development mode listens on `127.0.0.1:5000` and uses Flask's development
server with debug enabled. The development-only default account is
`admin` / `admin12345`; change it before using any production database.

## Production mode on one machine or a trusted LAN

Use the same project folder and set these environment variables in `.env`:

- `PLATTER_ENV=production`
- `SECRET_KEY`: a unique random value of at least 32 characters
- `DATABASE_URL`: the database connection URL
- For an empty database, `ADMIN_USERNAME`, `ADMIN_FULL_NAME`, and a unique
  `ADMIN_PASSWORD` of at least 16 characters
- For a database that still has the development default admin password, set
  `ADMIN_PASSWORD` to rotate it before startup

Generate a secret with PowerShell:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Then run the same entry point:

```powershell
.\.venv\Scripts\python.exe run.py
```

Production mode uses Waitress, disables debug mode, and listens on
`0.0.0.0:5000` by default so another machine on the LAN can connect. Open
`http://<host-machine-LAN-IP>:5000` on the second machine. Allow the port only
on the trusted private network in the host firewall.

Secure session cookies are enabled by default in production and should remain
enabled when serving over HTTPS. For an isolated local network served over
plain HTTP only, set `SESSION_COOKIE_SECURE=false` explicitly; HTTP does not
encrypt traffic, so do not use that setting on public or untrusted networks.

The `.env` file contains secrets and is ignored by Git. Do not commit it. Set
M-Pesa credentials and callback URL only in the environment where that
integration is configured. A public HTTPS callback URL is required for
automatic M-Pesa STK callbacks.
