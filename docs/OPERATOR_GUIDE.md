# Operator guide (vm1)

For the vetting operator. Install first (`packaging/INSTALL.txt`), then:

## Before the first scan

1. The tool's web page lives on vm1 only: `http://127.0.0.1:8770/`.
   The backend never leaves vm1; only the database port is contacted.
2. Reachability (from vm1, per target):
   `Test-NetConnection <target_ip> -Port <3306|5432|1521>`
   Must say `TcpTestSucceeded: True`. If not, the scan cannot work:
   fix the listener, the firewall, or the address first.
3. The VM's own address is not `localhost` inside the tool form.
   `localhost` on vm1 means vm1 itself. Use the target's IP on the
   shared (host-only) network, e.g. `192.168.182.1`.

## Database accounts (one per target, created once)

The tool never stores credentials and only needs to read. Create a
least-privilege account on each target; never use root/admin remotely.

MySQL (as root, on the target):

```sql
CREATE USER 'vetter'@'<vm1-ip>' IDENTIFIED BY '<strong-password>';
GRANT SELECT ON <db>.* TO 'vetter'@'<vm1-ip>';
FLUSH PRIVILEGES;
```

(`<vm1-ip>` may be `'%'` if the vetting VM has no fixed address.
The server must listen beyond localhost: `bind-address=0.0.0.0`,
plus a Windows Firewall rule for TCP 3306.)

PostgreSQL (as `postgres`):

```sql
CREATE ROLE vetter LOGIN PASSWORD '<strong-password>';
GRANT CONNECT ON DATABASE <db> TO vetter;
GRANT USAGE ON SCHEMA public TO vetter;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO vetter;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO vetter;
```

Plus `listen_addresses='*'`, a `pg_hba.conf` line
`host all all <vm1-ip>/32 scram-sha-256`, restart, and a firewall
rule for TCP 5432.

Oracle: a user with `CREATE SESSION` and `SELECT` on the application
schema; connect with the service name (e.g. `FREEPDB1`), pick the
schema in the tool. Firewall rule for TCP 1521.

Rule of thumb: if MySQL Workbench / pgAdmin / SQL Developer on vm1
connects with some host, user and password, the tool connects with
the same values typed into its form.

## Reading a result

- Headline first: `N of M tables scanned: X faulted, Y suspected, …`.
- Full cards are confirmed findings (strong evidence). Dimmed cards
  are suspected, not confirmed — treat them as leads.
- `Confidence Score` is not the probability the rule is real. It says
  how unlikely the data would look like this if no dependency existed.
  Always read the evidence (counts, examples) before acting.
- `possibly unused` / EOL notices, if enabled, are heuristic guesses.

## If something fails

- Every UI error carries a code and a scan id. On vm1 open
  `backend\logs\dbnorm.log` and search for the scan id; the log line
  names the technical cause. Codes are listed in `docs/DESIGN.md`.
- Never paste the log's surroundings with row data elsewhere: the log
  holds names and counts, but cards and exports contain real example
  values — handle PDFs accordingly.
- "Live progress connection was lost": the scan keeps running; the
  page re-syncs itself. If the backend was restarted, start again.
