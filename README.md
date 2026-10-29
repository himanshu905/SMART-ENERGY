# Wattwise — Smart Energy Consumption

A browser-based energy website with separate customer and admin accounts. The Python server protects sign-in, customer records, and energy data with SQLite-backed role checks. It uses only Python's standard library; no `pip install` is needed.

The included energy readings are illustrative sample data, randomized for each new customer account, not readings from a live meter.

## Requirements

- Python 3.10 or later
- A web browser
- R (optional, only for the analysis script)

If `python` is not recognized in PowerShell, install Python 3 for Windows and select **Add python.exe to PATH** during setup, then reopen PowerShell.

## First-time setup

Double-click `run-website.bat`. It finds an installed Python runtime, starts the website, and opens it in your browser. The first time you visit, create both accounts and choose separate passwords of at least 12 characters in the browser. Account setup only accepts requests from your computer; passwords are stored as salted hashes.

Sign in with `divyanshusingh31113@gmail.com` for the admin workspace or `thehorrorofcentury@gmail.com` for the customer dashboard. Open [http://127.0.0.1:8001](http://127.0.0.1:8001) to return to the website. This separate port prevents VS Code's static preview on port 8000 from being mistaken for the actual website.

To start the service manually instead, open PowerShell in the project folder and run `python server.py`. Then open [http://127.0.0.1:8001](http://127.0.0.1:8001). If `python` is not recognized in the terminal, the `run-website.bat` launcher also checks the Python runtime installed with VS Code.

## Admin and customer access

- **Admin:** Add customer accounts, review their usage summaries and individual readings, reset customer passwords, remove customer accounts, and create additional admin accounts. Passwords must be at least 12 characters; share temporary passwords privately.
- **Customer:** Sign in with an admin-provided account to see only that account's own readings.
- There is no public registration or role selector. The server assigns the dashboard from the signed-in account's role.
- Creating a customer seeds that customer's separate account with randomized readings based on the included sample data. Share any temporary password securely.
- The appliance breakdown is clearly marked as an illustrative estimate. Actual individual-appliance readings require connected device meters.

The SQLite database is created at `instance/wattwise.sqlite3`. It contains account password hashes, sessions, and per-customer readings. Back up this file securely if you need to preserve accounts and changes.

## Running the R analysis (optional)

Run this command from the project folder if R is installed:

```powershell
Rscript energy_analysis.R
```

The script validates `data/energy_usage.csv` and writes daily and weekly summaries to the `analysis` folder.

## Deploy to Vercel and Render

The included deployment setup serves the static frontend from Vercel and proxies `/api/*` requests to a Python service on Render. The Render service uses SQLite on a persistent disk, so choose a plan that supports persistent disks; this can incur hosting charges. Do not deploy the local `instance/wattwise.sqlite3` file or commit account passwords.

1. Deploy `render.yaml` from the Render dashboard. It creates the `wattwise-api` web service, enables secure cookies, and stores the database at `/var/data/wattwise.sqlite3` on a persistent disk.
2. In the Render service Shell, create the initial admin privately with `python server.py create-admin --email you@example.com --name "Your Name"`. Enter a unique password when prompted. Then sign in and create a customer account from the admin workspace. Remote first-run setup is deliberately disabled.
3. Import this repository into Vercel and deploy it as a static site from the project root. In `vercel.json`, replace the example `wattwise-api.onrender.com` host with the actual Render service hostname if it differs.
4. Open the Vercel deployment URL and sign in with the admin account created in Render.

The Vercel rewrite keeps API requests and the secure session cookie on the same browser origin. The Render service must remain reachable over HTTPS. Back up its persistent disk and review the hosting provider's costs and access controls before using real customer data. The local launcher and local SQLite database continue to work independently of the hosted deployment.

## Project files

- `server.py` — Python web server, SQLite database, sign-in, and protected APIs
- `run-website.bat`, `run-website.ps1` — Windows launcher that sets up the requested accounts and starts the web server
- `login.html`, `login.js` — shared sign-in and secure first-run account setup; account role determines where the user goes
- `index.html`, `app.js` — customer dashboard and private energy summaries
- `admin.html`, `admin.js` — admin customer/account management
- `styles.css`, `auth.css` — responsive website styling
- `auth.js` — authenticated browser requests and sign-out
- `data/energy_usage.csv` — baseline sample readings used to generate readings for new customer accounts
- `energy_analysis.R` — optional R data analysis
- `tests/test_server.py` — authentication, role access, account setup, password reset, and session integration tests

## Run the tests

From a terminal with Python installed, run:

```powershell
python -m unittest discover -s tests -v
```
