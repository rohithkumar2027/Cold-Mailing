# ⚡ AutoRecruit: Recruiter Lead Discovery & Cold Mailing Automation

An automated lead generation and cold mailing pipeline designed for students and new grads targeting **Software Engineering (SWE)**, **AI / Machine Learning (ML)**, and **Data Science** internships.

It automatically finds relevant University and Technical Recruiters from LinkedIn for target companies, looks for each recruiter's **real, published email** (never a guess), and lets you preview and download the results as a 4-column sheet (CSV), plus tailored cold email drafts.

---

## 🌟 Key Features

- **Safe Lead Sourcing**: Searches public LinkedIn profiles via dorking (`site:linkedin.com/in`) without logging into LinkedIn or risking account flags or CAPTCHAs.
- **Target Role Filtering**:
  - **SWE**: University Recruiters, Technical Recruiters, Early Career / Campus Talent.
  - **AI / ML**: AI/ML Technical Recruiters, Machine Learning Research Recruiters.
  - **Data Science**: Data Science & Analytics Talent Acquisition.
- **Real emails only (no API keys)**:
  - Press **Find emails** and the app searches the public web for an address the recruiter actually published (LinkedIn snippet, public pages) that matches their name and company domain.
  - If no real address is found the Email cell stays **blank**. Addresses are **never guessed** from name patterns.
  - **Optional paid lookups with several keys**: put your ContactOut (and/or Apollo) keys in a `.env` file, comma-separated:
    ```text
    CONTACTOUT_API_KEYS=key_one,key_two,key_three
    ```
    You can also add keys **one at a time in the app** (sidebar → *ContactOut keys* → paste → **Add**). They are saved only on your computer in `config/api_keys.json`.
    Free plan maths: each key has 100 credits and one email costs 10, so each key gives 10 emails (5 keys = 50). A key is used until its credits are gone, then the next one is used; a lookup that finds no email costs nothing. The panel shows credits left per key (↺ resets a key after your plan renews). Change the numbers with `CONTACTOUT_CREDITS_PER_KEY` / `CONTACTOUT_CREDITS_PER_EMAIL` in `.env` if your plan differs.
    Keys are used in order. When one runs out of credits, is invalid or is rate-limited, the next key is used automatically. Restart the app after editing `.env`. Real results from these services are labelled **ContactOut** / **Apollo** on the card. Never commit your `.env` file.
- **Sheet preview & CSV**:
  - **Preview sheet** shows exactly what the sheet will contain; **Download CSV** saves it (also kept in `data/leads_master.csv`, de-duplicated by name + company).
  - Optional: `python main.py` can still sync to Google Sheets if `config/credentials.json` exists.
- **Modern Web Dashboard & Interactive CLI**:
  - Clean sidebar UI with recruiter cards, Verified/Likely match badges, sheet preview and CSV download.
  - Fast, scriptable CLI for batch automation.
- **Personalized Cold Email Generator**:
  - High-converting cold email drafts tailored for SWE, AI/ML, and Data Science internships with 1-click "Open in Mail Client".

---

## 📁 Project Structure

```text
c:\Projects\Cold_mailing\
├── config/
│   ├── settings.py           # Configuration (keywords, company patterns, domains)
│   └── credentials.json      # Google Cloud Service Account credentials (user provided)
├── data/
│   └── leads_master.csv      # Local master lead database
├── modules/
│   ├── scraper.py            # Recruiter finder via search dorking
│   ├── email_finder.py       # Real-email lookup, ContactOut/Apollo key pool with credit tracking
│   ├── sheets_manager.py     # Google Sheets sync & deduplication
│   └── email_templates.py    # Tailored cold email drafts for SWE, AI/ML, and Data Science
├── tests/
│   ├── test_scraper.py       # Recruiter search/verification tests
│   ├── test_email_finder.py  # Email, key-rotation & sheet tests
│   └── test_automation.py    # Original unit tests
├── web/
│   └── index.html            # Modern glassmorphism web dashboard
├── app.py                    # FastAPI web server backend
├── main.py                   # Interactive & batch CLI tool
├── requirements.txt          # Dependencies
└── .env.example              # Sample environment variables
```

---

## 🚀 Quick Start

### 1. Launch the Web Dashboard

```bash
python app.py
```
Open your browser and navigate to: **`http://127.0.0.1:8000`**

1. Enter a company name (e.g., `Google`, `Meta`, `Stripe`, `Databricks`) or click one of the quick pills.
2. Select your internship focus: **Software Engineering (SWE)**, **AI / Machine Learning**, or **Data Science**.
3. Click **Find recruiters** (use *Deep* search for the most complete list).
4. Click **Find emails** to look for real published emails.
5. Click **Preview sheet** to see the sheet, then **Download CSV**.

---

### 2. Run via Command Line (CLI)

#### Interactive Mode:
```bash
python main.py
```
Follow the interactive prompts to choose companies, role, and recruiter count.

#### Batch / One-liner Mode:
```bash
# Search SWE recruiters at Google
python main.py --company "Google" --role "swe" --limit 5

# Search AI/ML recruiters at Meta and OpenAI
python main.py --companies "Meta,OpenAI" --role "ai_ml" --limit 5

# Search Data Science recruiters at Databricks
python main.py --company "Databricks" --role "data_science" --limit 5
```

---

## 📊 Google Sheets Setup Guide

To automatically append leads directly to your Google Sheet:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project (e.g. `Recruiter-Leads`).
3. Enable the **Google Sheets API** and **Google Drive API** under **APIs & Services > Library**.
4. Go to **APIs & Services > Credentials** > **Create Credentials** > **Service Account**.
5. Click on the created service account > **Keys** tab > **Add Key** > **Create new key (JSON)**.
6. Save the downloaded JSON file as:
   ```text
   config/credentials.json
   ```
7. Open the JSON file and copy the `client_email` address (e.g., `recruiter-bot@project.iam.gserviceaccount.com`).
8. Open your Google Sheet, click **Share**, and paste the service account email with **Editor** access.
9. (Optional) In `.env`, set `GOOGLE_SHEET_NAME` to the exact title of your Google Sheet (default: `Recruiter_Leads_Internships`).

> **Note:** Even without `credentials.json`, the tool will always save all discovered leads to `data/leads_master.csv` so you never lose any data!

---

## 📋 Sheet Columns

| Column | Header | Description |
| :--- | :--- | :--- |
| **A** | Recruiter Name | Full name of the recruiter |
| **B** | Email | Real published email, or blank if none was found |
| **C** | Company | Company they currently work at |
| **D** | About the Recruiter | Headline, location, education and earlier employers taken from their public profile snippet |

---

## 🧪 Running Unit Tests

To run the automated test suite:

```bash
python -m pytest tests -q
```
