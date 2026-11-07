# OpenRoles

A job search website that collects open roles from companies' **official** career sites every day and sends job seekers to the company's own site to apply.

- **Cost:** ₹0. Hosting is GitHub Pages, and the daily update runs on GitHub Actions (free for public repositories).
- **No servers, no database, no logins.** A Python script builds a data file each morning and the website reads it.
- **Nothing to install.** Everything runs on GitHub's computers.
- **Starts with 93 companies**, including Infosys, TCS, Accenture, Deloitte, EY, KPMG, PwC, JPMorgan, Goldman Sachs, Citi, HSBC, HDFC Bank, ICICI Bank, Google, Microsoft and Amazon. You add more by pasting a careers page link.

---

## Launch it (about 15 minutes, no coding)

### 1. Create a free GitHub account
Go to github.com and sign up. Your username becomes part of your website address.

### 2. Create a repository
1. Click **+** (top right) → **New repository**.
2. Give it a name (for example `openroles`).
3. Choose **Public**. This is required for free hosting and free daily updates.
4. Click **Create repository**.

### 3. Upload the files
1. On the new repository page, click **uploading an existing file**.
2. Unzip `openroles.zip` on your computer and open the `openroles` folder.
3. Select **`site`**, **`collector`**, **`companies.csv`** and **`README.md`**, and drag them into the browser window.
4. Click **Commit changes**.

### 4. Turn on GitHub Pages
1. Open **Settings** → **Pages** (left menu).
2. Under **Build and deployment → Source**, choose **GitHub Actions**.

### 5. Add the daily update
The automation file sits in a hidden folder, so it's easiest to create it by hand:
1. Go back to the **Code** tab → **Add file** → **Create new file**.
2. In the file name box, type exactly: `.github/workflows/update-jobs.yml`
3. Open `update-jobs.yml` from the zip (inside `.github/workflows/`) in Notepad, copy everything, and paste it in.
   *Can't see the `.github` folder? On Windows: View → Show → Hidden items. On Mac: press Cmd + Shift + . in Finder.*
4. Click **Commit changes**.

This starts the first run automatically. Open the **Actions** tab to watch it. With 93 companies, the first run takes roughly 30–90 minutes, because the collector reads big career sites slowly on purpose.

### 6. Open your website
When the run shows a green tick, your site is live at:

```
https://YOUR-USERNAME.github.io/REPOSITORY-NAME/
```

The address is case-sensitive, so use the repository name exactly as you typed it. From now on the site refreshes itself every day at 05:47 IST. To refresh immediately: **Actions** → **Update jobs and publish site** → **Run workflow**.

### 7. Put your name and email on it
Open `site/config.js` on GitHub, click the pencil icon, change the site name and contact email, and commit. The email is where companies send removal requests, so use one you check.

---

## Read the run report

Open **Actions** → the latest run → **build-and-deploy** → **Collect jobs**. You'll see one line per company:

```
Company                        Source            Jobs  Result
Infosys                        sitemap            214  ok   [sitemap: https://www.infosys.com/careers/]
Accenture                      workday           2980  ok   [workday: https://accenture.wd103.myworkdayjobs.com/AccentureCareers]
HDFC Bank                      auto                 0  failed: couldn't find a readable job source on this careers page...
```

(These lines are an illustration of the format, not real results.)

- **ok**: the jobs are on the site. The part in [brackets] shows the hiring system it found. You can copy that into `companies.csv` to make future runs faster.
- **failed: HTTP 404**: the link or code is wrong. Fix it in `companies.csv`.
- **failed: robots.txt does not allow…**: the company doesn't allow automated reading. Leave it out, or email them to ask for permission.
- **failed: couldn't find a readable job source**: see "When auto-detect fails" below.

If a company fails on a day when it worked before, the site keeps showing that company's jobs from the previous day.

---

## Adding companies

All companies live in **`companies.csv`**. Edit it on GitHub (pencil icon) and add one line per company. The easy way is to paste the company's official careers page with source `auto`:

```
name,source,token,industry,country
Tata Motors,auto,https://www.tatamotors.com/careers/,Manufacturing,India
```

The collector opens that page, finds which hiring system the company uses (Workday, Oracle, SAP SuccessFactors, Greenhouse, Lever, Ashby or SmartRecruiters), and reads the jobs from it. If the company uses none of these, it looks for job pages through the site's sitemap.

### When auto-detect fails
Many careers pages are built in a way the collector can't read directly. Then:
1. On the company's careers site, search for any job and open the job ad.
2. Copy the address of that job ad page.
3. Paste it as the token, still with source `auto`.

Job ad addresses usually reveal the hiring system, for example `something.wd3.myworkdayjobs.com/...` (Workday), `....oraclecloud.com/hcmUI/CandidateExperience/...` (Oracle), or `jobs.lever.co/...` (Lever).

### Sources you can set directly

| source | token | Example |
|---|---|---|
| `auto` | Any official careers page or job ad address | `https://www.infosys.com/careers/` |
| `workday` | Career site address | `https://acme.wd5.myworkdayjobs.com/External` |
| `oracle` | Career site address | `https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1` |
| `successfactors` | Career site address | `https://careers.ey.com/` |
| `greenhouse` | Board name from `job-boards.greenhouse.io/NAME` | `stripe` |
| `lever` | Name from `jobs.lever.co/NAME` | `palantir` |
| `ashby` | Name from `jobs.ashbyhq.com/NAME` | `notion` |
| `smartrecruiters` | Name from `jobs.smartrecruiters.com/NAME` (case matters) | `Visa` |
| `sitemap` | Company website | `https://careers.acme.com/` |

**Industry** is free text. Keep using the same spellings (Finance, IT, Consulting, Manufacturing, Consumer Goods, Energy…) so the filter stays tidy.

### How far can this grow?
- **Free GitHub Actions** gives each run up to about 5½ hours. Each company with a few thousand jobs takes 10–60 minutes, but 8 companies run at once, so a few hundred large companies fit in a day's run. Small companies take seconds.
- **The website** stays fast up to roughly 20,000–30,000 jobs. Beyond that, the next step is a real database and search, such as Supabase's free tier.
- For very large employers, the collector reads up to 3,000 newest jobs each and opens full descriptions for the newest 150 on Workday sites. These limits are at the top of `collector/collect.py`.

---

## Staying legal and ethical

What the code already does:
- Reads only **official sources**: the company's own career site or the public job feeds companies publish (Greenhouse, Lever, Ashby, SmartRecruiters).
- For every company career site, it **checks robots.txt first** and skips anything the site doesn't allow.
- Sends **at most one request per second to any website**, identifies itself as `OpenRolesBot`, and reads each source once a day.
- Shows a **short summary** and links to the original. Every "Apply" button goes to the company's own page.
- Drops closed jobs, postings past their closing date and postings older than 120 days.
- Collects **no personal data**. Saved jobs stay in the visitor's browser.

What you need to do:
- **Don't add other job portals** (LinkedIn, Naukri, Indeed, Foundit, Glassdoor). Their terms forbid it, and the listings are theirs, not the companies'.
- **Read the terms of use** of company career sites you add. Robots.txt is the machine-readable rule; some companies also write restrictions into their terms. If a company forbids automated access, leave it out or ask them for permission.
- **Remove a company within 48 hours if they ask.** Delete its line from `companies.csv`.
- **Before you start earning money:** GitHub Pages doesn't allow sites that are mainly a commercial business. A free job search site is fine. When you add paid plans or sponsored listings, move hosting to Cloudflare Pages or Netlify (both have free tiers that allow commercial use). The `site` folder works there unchanged.
- **Before you add logins, resume uploads or email alerts:** write a proper privacy policy and consent flow that meets India's Digital Personal Data Protection Act, 2023.

---

## How it fits together

```
companies.csv ──► collector/collect.py ──► site/data/jobs.json ──► website (site/)
                  (runs daily on GitHub)     site/data/companies.json
```

| File | What it is |
|---|---|
| `companies.csv` | The list of companies to collect from |
| `collector/collect.py` | Finds each company's hiring system, fetches jobs, removes duplicates and expired jobs, estimates experience and level, detects skills |
| `site/` | The website: `index.html`, `app.js`, `styles.css`, `about.html`, `privacy.html`, `config.js` |
| `.github/workflows/update-jobs.yml` | The daily automation |
| `collector/test_fixtures/` | Saved sample responses (fictional companies) for testing without internet |

**Test on your own computer (optional, needs Python 3):**
```
python collector/collect.py --companies collector/test_companies.csv --fixtures collector/test_fixtures
cd site && python -m http.server 8000
```
Then open http://localhost:8000. Delete `site/data` afterwards so test data isn't uploaded.

To try one real company: `python collector/collect.py --only "Infosys"`

## Limits of this version
- Not every company can be read automatically. Some build their careers sites in ways no collector can read without a full browser, or forbid automated access. The run report tells you which.
- Experience and job level are **estimated** from the job text. The site labels them as estimates.
- Salary appears only when the company publishes it.
- Job alerts and user accounts are not included yet. They need a backend and a DPDP-compliant privacy setup.
