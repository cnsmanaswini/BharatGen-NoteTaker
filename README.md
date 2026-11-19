# BharatGen Meet Bot (Milestone 1)

Launches Chrome via Playwright with a persistent profile so a dedicated Google
account stays signed in between runs. Joining a meeting arrives in Milestone 2.

## Setup (Windows 11 / PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium     # only needed if BROWSER_CHANNEL is empty
Copy-Item .env.example .env
```

## Run
```powershell
python -m app.main                                   # opens meet.google.com
python -m app.main https://meet.google.com/abc-defg-hij
```
First run: sign in manually in the browser window with the dedicated account.
The session is stored in `browser_profile/` (git-ignored). Second run should
skip login. Exit with Ctrl+C or by closing the window.

## Tests
```powershell
pytest
```
Unit tests cover config and URL validation only. Browser launch and persistent
auth must be verified manually on your machine.

## Security
Never commit `.env`, `browser_profile/` or `logs/`.
