# DataFlow

A Flask web service for CSV upload, user-configurable processing, and results display.

## Features
- Login / logout with session management
- Role-based access: **admin** and **user**
- Admin panel: create, edit, delete, activate/deactivate users
- CSV upload (private per user, or shared by admins)
- Configurable processing choices before running
- Results displayed on screen
- Placeholder processing module — **fill in your logic in `processing.py`**

---

## Running on Windows (quickest way)

1. Extract this folder somewhere **without spaces in the path** (e.g. `C:\projects\myapp`)
2. Double-click **`start.bat`**
3. Open http://localhost:5000

> ⚠️ Avoid paths like `C:\My Projects\` — spaces in folder names break the virtual environment.

---

## Running manually

```bash
# 1. Create a virtual environment
python -m venv venv

# 2. Activate it
venv\Scripts\activate.bat        # Windows CMD
# or
venv\Scripts\activate            # Windows PowerShell (may need: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser)
# or
source venv/bin/activate         # Mac / Linux

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python app.py
```

Open http://localhost:5000

**Default admin credentials:** `admin` / `admin123`  
→ Change this immediately after first login via the Admin panel.

---

## Adding your processing logic

Open `processing.py`. There are two functions:

### `get_choice_options(df) → list`
Returns the list of form controls shown to the user after uploading.
Edit or add entries to define your dropdowns, checkboxes, etc.

### `process(df, choices) → dict`
Receives the CSV as a pandas DataFrame and the user's choices.
Replace the placeholder code with your logic.
Must return a dict with:
- `summary` (str) — short description shown at top of results
- `table` (list of dicts) — rows for the data table
- `columns` (list) — column names for the table
- `stats` (dict) — optional numeric stats

---

## Deploying to Render.com (free)

1. Push this folder to a GitHub repo
2. Go to https://render.com → New → Web Service
3. Connect your repo
4. Render auto-detects `render.yaml` — click **Deploy**
5. Done. Your app is live at `https://dataflow-xxxx.onrender.com`

> **Note:** Render's free tier uses ephemeral storage — uploaded files and the SQLite DB reset on redeploy.  
> For persistent storage, upgrade to a paid tier or switch to PostgreSQL + cloud file storage.

---

## Project structure

```
myapp/
├── app.py           — Flask app factory, DB init, admin seed
├── extensions.py    — db and login_manager singletons
├── models.py        — User, Upload, Result models
├── auth.py          — login/logout routes
├── admin.py         — user management routes
├── main.py          — upload, choices, results routes
├── processing.py    — ← YOUR LOGIC GOES HERE
├── start.bat        — Windows one-click launcher
├── templates/
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── upload.html
│   ├── choices.html
│   ├── results.html
│   └── admin/
│       ├── users.html
│       └── user_form.html
├── uploads/         — stored CSV files (auto-created)
├── requirements.txt
└── render.yaml
```
