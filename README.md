# Chicago Job Matches

A small website that pulls fresh job postings from Chicago-area employers, keeps the ones that fit the
candidate's resume (psychology B.A., client-facing work, scheduling, confidentiality, bilingual), and scores
each one from 0 to 100. It's built for someone living in Pilsen who wants an HR-style corporate role first,
with front desk, patient services, intake and psychology-related jobs as good second choices.

It runs two ways:

- **Online (GitHub Pages):** GitHub refreshes the list every morning and afternoon and publishes it as a
  website that works on a phone. See [Online version](#online-version-github).
- **On a Windows PC:** double-click `Start Job Finder.bat`, kept in the folder above this one. The site opens
  at <http://127.0.0.1:8765>. Keep the black window open while you use it.
  - The list refreshes itself at startup when it's over 12 hours old, then every 12 hours while open.
    **Refresh now** updates it straight away. A full refresh takes a few minutes.
  - **Save**, **Mark applied** and **Hide** are stored in `data/jobs.db`.

Both need Python 3.10 or newer. No extra packages.

## The match score

| Part | Points | What raises it |
|---|---|---|
| Role fit (job title) | 40 | HR / recruiting / onboarding (best), then front desk & office, patient services & intake, psychology-related (behavioral health tech, research assistant, case aide), then client service and other entry-level jobs. Senior, manager and "partner" titles are scaled down. |
| Requirements | 20 | Points come off for years of experience asked (3+ or 5+), a required master's degree or licence, required languages she doesn't speak (often Spanish), or needing a car. An entry-level friendly posting adds points. |
| Skills overlap | 10 | How many of her resume skills the description mentions (scheduling, confidentiality/HIPAA, front desk, Microsoft Office, etc.). |
| Commute | 15 | Estimated transit time from Pilsen (18th St Pink Line). Remote jobs get full points. Hybrid jobs lose less. |
| Recency | 15 | Posted in the last 3 days gets full points. Older than 30 days gets almost none. The site shows the last 14 days by default. |

When the title is only a partial fit, the other four parts are scaled down too, so a short commute can't
lift an unrelated job. Click **Why this score** on any job to see the breakdown.

Commute times are rough estimates from a lookup table of neighborhoods and suburbs, not routed trips.
"Chicago (site not listed)" means the posting only says "Chicago", and the time assumes downtown.

**Tuning:** edit `profile.json` to change title keywords, points, commute limits or weights.
- On the PC, click **Re-score** under "How the score works".
- Online, the change applies on the next refresh.

## Where the jobs come from (`config.json`)

Public job feeds from about 70 employers with downtown, Medical District or Hyde Park offices:

- **Healthcare & universities:** Northwestern Medicine, UChicago Medicine, Lurie Children's, Sinai Chicago,
  Advocate, University of Chicago, DePaul, Loyola, LifeStance (behavioral health)
- **Corporate:** McDonald's, Kraft Heinz, JLL, Northern Trust, BMO, Aon, Exelon/ComEd, Blue Cross Blue Shield
  of IL, Allstate, CNA, Morningstar, Motorola Solutions, Salesforce, Amazon, Groupon, Sprout Social, and more

To add a company, copy a line in `config.json`. Supported types: `workday`, `greenhouse`, `lever`, `ashby`,
`smartrecruiters`, `oracle`, `icims`, `successfactors`, `atom`, `amazon`.

Some large employers block automated access or have no public feed: Rush, UI Health/UIC, Cook County
Health, Northwestern University, United Airlines, Hyatt and the big law firms. Check their career sites by
hand now and then.

### Optional: two free key-based sources

Company feeds miss small clinics, therapy practices and offices. Two free services fill that gap:

- **Adzuna** collects postings from many Chicago job boards: <https://developer.adzuna.com>
- **USAJobs** covers federal jobs, e.g. Medical Support Assistant at the Jesse Brown VA in the Medical
  District: <https://developer.usajobs.gov>

**Never put keys in `config.json`.** It is public on GitHub. Put them in one of these places instead:

- **On the PC:** a file named `secrets.json` next to `config.json`. Git ignores this file.
  ```json
  {"adzuna": {"app_id": "...", "app_key": "..."}, "usajobs": {"email": "...", "api_key": "..."}}
  ```
- **Online:** in the GitHub repository, go to *Settings → Secrets and variables → Actions*. Add
  `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `USAJOBS_EMAIL` and `USAJOBS_API_KEY`.

A source turns itself on once its keys are present.

## Online version (GitHub)

`.github/workflows/refresh.yml` runs at about 6 am and 1 pm Chicago time. The **Actions** tab also has a
**Run workflow** button. Each run:

1. Restores the previous job history from the Actions cache. That's how the site knows which jobs are new,
   and why it doesn't re-download descriptions it already has.
2. Runs `python server.py --fetch-once --db state/jobs.db`.
3. Runs `python build_static.py --db state/jobs.db --out _site`, which builds the website.
4. Publishes `_site` to GitHub Pages.

On the online site, Save / Applied / Hidden are stored in each browser. A phone and a laptop keep their own
lists. The page asks search engines not to list it, but anyone with the link can open it.

To set up a new copy, go to *Settings → Pages* in the repository and set *Source* to *GitHub Actions*.
Then run the workflow once.

GitHub pauses scheduled workflows in public repositories after 60 days with no commits. The workflow makes
an empty "keep active" commit every 45 days to prevent this. If the schedule stops anyway, re-enable it
from the Actions tab.
