TimeClock

Simple local time-tracking app using Tkinter.

To create and push a GitHub repo using GitHub CLI (`gh`):

```powershell
cd C:\MyProjects\TimeTracking
git init
git add .
git commit -m "Initial commit"
# Requires gh authenticated (run `gh auth login` if needed)
gh repo create TimeTracking --public --source=. --push --confirm
```

If you don't have `gh` or prefer to create the repo on github.com manually, create an empty repo there and then run:

```powershell
git remote add origin https://github.com/<your-username>/TimeTracking.git
git push -u origin main
```
