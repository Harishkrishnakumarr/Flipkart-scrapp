# Flipkart Playwright Scraper — Free GitHub Actions Deployment

## Run remotely

1. Create a GitHub repository.
2. Upload this entire project.
3. Open **Actions** → **Flipkart Playwright Scraper**.
4. Click **Run workflow**.
5. Wait for the job to finish.
6. Open the completed workflow run and download the `flipkart-scraper-output` artifact.

The workflow installs Python, your requirements, Playwright Chromium, and the required Linux browser dependencies automatically.

## Scheduled run

The workflow is also configured to run daily at 09:00 IST (03:30 UTC). You can change the cron expression in:

`.github/workflows/flipkart-scraper.yml`

## Important

GitHub-hosted runners are temporary. Files generated during a run are preserved through the uploaded artifact, but changes made only inside the runner filesystem are not automatically committed back to the repository.

If your scraper requires a particular input Excel file, keep it inside the repository or modify the scraper to download/read it from the desired location.

## Entry point

The deployment workflow currently runs:

`python main.py`

If your actual scraper entrypoint is different, edit that one line in the workflow.
