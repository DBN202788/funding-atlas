# Taking the atlas public

Everything in the project is ready, but these steps need your own account. Nothing is public until you do them.

## 1. Create a GitHub account and an empty repository

1. Sign up at github.com if you don't have an account. Using an organization account for Dream Builders Network keeps ownership with the business.
2. Create a new **public** repository named `funding-atlas`. Don't add any starter files.

GitHub's free scheduled jobs and website hosting apply to public repositories. The code and the extracted data would be visible to anyone. Filed forms stay out because `.gitignore` excludes them.

## 2. Upload the project

Run these from the project folder, replacing `YOUR-ACCOUNT`:

```bash
git init -b main
```

```bash
git add .
```

```bash
git commit -m "First version of the Legislative Funding Atlas"
```

```bash
git remote add origin https://github.com/YOUR-ACCOUNT/funding-atlas.git
```

```bash
git push -u origin main
```

Before pushing, confirm that no form PDFs are included. This command should print nothing:

```bash
git ls-files data/raw/florida_forms
```

## 3. Turn on the website and the schedule

1. In the repository, open **Settings > Pages** and set **Source** to **GitHub Actions**.
2. Open **Settings > Actions > General** and set **Workflow permissions** to **Read and write**.
3. Open the **Actions** tab, choose **Update funding data**, and click **Run workflow**.

The first run takes a few minutes or more. When it finishes, the site address appears on the run's summary. It will look like `https://YOUR-ACCOUNT.github.io/funding-atlas/`.

## 4. Optional: use your own web address

Buy a domain from any registrar, then add it under **Settings > Pages > Custom domain** and follow GitHub's instructions.

## After launch

- **Automatic:** a check every Monday for new applications, new budgets and new Delaware acts. Updates go live only if their checks pass.
- **Your review list:** an issue named **Atlas review list** is updated after each run.
- **Failed updates:** GitHub emails the owner, and the site keeps showing the last good data.
- **By hand, about once a year:** process each new Florida veto letter with `pipeline/ocr_windows.ps1`, and update checklists when a rules page changes.

## Before you announce it

- **Legal review:** have someone qualified review the disclaimer, your use of public records, and any paid services.
- **The name:** "Legislative Funding Atlas" is a working name. Check that it's available before promoting it.
- **Advisor:** it doesn't run on the public site, because it depends on a viewer's Claude account. A public version needs a small server and an AI account paid per use.
- **Source sites:** the job identifies itself and follows each site's crawl delay. If a site asks you to stop or limit access, respect that.
