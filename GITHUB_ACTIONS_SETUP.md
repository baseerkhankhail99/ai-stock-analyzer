# GitHub Actions CI/CD Setup Instructions

This document provides step-by-step instructions to activate the CI/CD workflow and configure repository variables.

## Step 1: Activate the GitHub Actions Workflow

⚠️ **Important:** The repository permissions prevent direct creation of workflow files. You must manually move the workflow file:

1. Go to your repository: https://github.com/baseerkhankhail99/ai-stock-analyzer
2. Create the directory `.github/workflows/` if it doesn't exist
3. Copy or rename `ci-cd-workflow.yml` → `.github/workflows/ci-cd.yml`
4. Commit and push this change

Alternatively, use GitHub's web interface:
- Click "Add file" > "Create new file"
- Type the path: `.github/workflows/ci-cd.yml`
- Copy the contents from `ci-cd-workflow.yml`
- Commit with message: "Activate CI/CD workflow"

## Step 2: Configure Repository Variables

Repository variables are used to safely gate deployment features. Set these in **Settings → Secrets and variables → Actions → Variables**:

| Variable Name | Value | Purpose |
|---|---|---|
| `ENABLE_IMAGE_PUSH` | `false` | Container image building/pushing is disabled by default |
| `ENABLE_STAGING_DEPLOY` | `false` | Staging deployment is disabled by default |
| `ENABLE_PRODUCTION_DEPLOY` | `false` | Production deployment is disabled by default |

### To add variables:
1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository variable**
3. Add each variable from the table above
4. Set the value and click **Add variable**

## Step 3: Configure Repository Secrets (When Ready)

When you're ready to enable deployments, add these secrets in **Settings → Secrets and variables → Actions → Secrets**:

| Secret Name | Purpose | Example |
|---|---|---|
| `DEPLOY_KEY` | SSH key or API token for production deployment | Your deployment credentials |
| `SLACK_WEBHOOK` | Slack webhook URL for build notifications | `https://hooks.slack.com/services/YOUR/WEBHOOK/URL` |

### To add secrets:
1. Go to **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Add the secret name and value
4. Click **Add secret**

## Step 4: Enable Features Gradually

When you're ready to test CI/CD features:

### Test image building locally:
```bash
# With ENABLE_IMAGE_PUSH = false (default)
git push origin develop
# Workflow will build image locally but not push
```

### Enable staging deployment:
1. Update `ENABLE_IMAGE_PUSH` to `true`
2. Update `ENABLE_STAGING_DEPLOY` to `true`
3. Add `DEPLOY_KEY` secret if needed
4. Push to `develop` branch to trigger staging deployment

### Enable production deployment:
1. Ensure image pushing is working via staging
2. Update `ENABLE_PRODUCTION_DEPLOY` to `true`
3. Push to `main` branch to trigger production deployment

## Workflow Jobs Overview

| Job | Triggers | Requires | Outputs |
|---|---|---|---|
| **test** | All PRs & pushes | Python 3.11, PostgreSQL, Redis | Coverage reports |
| **security-scan** | All PRs & pushes | Python 3.11 | Bandit & pip-audit reports |
| **build** | Pushes to main/develop | test + security-scan pass | Docker image |
| **deploy-staging** | Pushes to develop | ENABLE_IMAGE_PUSH=true, ENABLE_STAGING_DEPLOY=true | Staging deployment |
| **deploy-production** | Pushes to main | ENABLE_IMAGE_PUSH=true, ENABLE_PRODUCTION_DEPLOY=true | Production deployment |
| **notify-pr** | PRs only | test + security-scan | Slack notification |
| **notify-push** | Pushes only | test + security-scan | Slack notification |

## Troubleshooting

### Workflow doesn't appear in Actions tab
- Ensure the workflow file is at `.github/workflows/ci-cd.yml` (not elsewhere)
- Wait 1-2 minutes after pushing the file
- Refresh the page

### Tests fail with database connection errors
- Docker services (PostgreSQL, Redis) should auto-start
- Check that you don't have local services running on ports 5432 or 6379
- Review the test job logs for specific errors

### Image push fails
- Ensure `ENABLE_IMAGE_PUSH` is set to `true`
- Verify you have push permissions to GitHub Container Registry
- Check that `GITHUB_TOKEN` secret is available (it's automatically provided)

### Deployment fails
- Check that deployment secrets (`DEPLOY_KEY`) are properly configured
- Verify deployment commands in the workflow match your infrastructure
- Review the deploy job logs for error details

## Next Steps

1. ✅ Merge this PR
2. ⚠️ **Manually** move `ci-cd-workflow.yml` to `.github/workflows/ci-cd.yml`
3. ✅ Configure repository variables (see Step 2)
4. ✅ Test with a push to `develop` branch
5. 🔑 Add deployment secrets when ready
6. 🚀 Enable production deployment variables
