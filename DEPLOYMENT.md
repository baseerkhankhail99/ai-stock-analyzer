# CI/CD Pipeline Deployment Guide

This guide explains the GitHub Actions CI/CD pipeline, how to set it up, and how to configure deployments for your AI Stock Analyzer application.

## Overview

The CI/CD pipeline automates testing, security scanning, Docker builds, and multi-environment deployments. It consists of 6 core jobs:

### Job Flow

```
┌─────────┐
│  Test   │  Runs on: All pushes & PRs
└────┬────┘
     │
     ├──────────────────┬─────────────────┐
     │                  │                 │
  ┌──▼──────┐    ┌─────▼────┐     ┌─────▼──────┐
  │Security │    │   Build  │     │  Notify    │
  │  Scan   │    │  Docker  │     │   (PR)     │
  └─────────┘    └──┬───┬───┘     └────────────┘
                    │   │
             ┌──────┘   └──────┐
             │                 │
          ┌──▼──┐          ┌───▼──┐
          │Stage│          │ Prod │
          │  ing│          │      │
          └─────┘          └──────┘
```

#### 1. **Test Job**
Runs on all pushes and pull requests.

```yaml
- Linting: flake8, black, isort
- Unit tests: pytest with coverage
- Services: PostgreSQL 15, Redis 7
```

#### 2. **Security Scan Job**
Performs security audits on code and dependencies.

```yaml
- Bandit: scans for common security issues
- pip-audit: checks runtime dependencies for known vulnerabilities
```

#### 3. **Build Job**
Builds a Docker image after test and security scans pass.

```yaml
- Triggered: Only on pushes to main/develop
- Requires: test + security-scan to pass
- Image registry: GitHub Container Registry (ghcr.io)
- Publishing: Opt-in via ENABLE_IMAGE_PUSH variable
```

#### 4. **Deploy to Staging**
Deploys to staging environment after successful build.

```yaml
- Triggered: Pushes to develop branch
- Requires: ENABLE_IMAGE_PUSH=true + ENABLE_STAGING_DEPLOY=true
- Customize: Update deployment commands for your infrastructure
```

#### 5. **Deploy to Production**
Deploys to production environment after successful build.

```yaml
- Triggered: Pushes to main branch
- Requires: ENABLE_IMAGE_PUSH=true + ENABLE_PRODUCTION_DEPLOY=true
- Environment protection: Requires manual review/approval (optional)
- Customize: Update deployment commands for your infrastructure
```

#### 6. **Notify Job**
Sends notifications (e.g., Slack) on pipeline completion.

```yaml
- notify-pr: runs for pull requests after test and security scan jobs
- notify-push: runs for pushes after test and security scan jobs
```

### Setup Instructions

#### Step 1: Create Workflow File

This pull request adds the reference workflow as `ci-cd-workflow.yml`. After merging, move it to `.github/workflows/ci-cd.yml` to activate it.

#### Step 2: Configure GitHub Secrets

Navigate to **Settings > Secrets and variables > Actions** and add:

| Secret | Purpose |
| --- | --- |
| `DEPLOY_KEY` | SSH or API key for production deployment |
| `SLACK_WEBHOOK` | Slack webhook URL for notifications (optional) |

#### Step 3: Configure Environments (Optional)

For production deployments, create an environment:

1. Go to **Settings > Environments**
2. Create a new environment named `production`
3. Set required reviewers (recommended)
4. Add environment-specific secrets

To keep placeholder deploy jobs disabled until your real deployment commands are ready, add repository variables under **Settings > Secrets and variables > Actions > Variables**:

| Variable | Suggested value |
| --- | --- |
| `ENABLE_IMAGE_PUSH` | `false` until registry publishing is configured |
| `ENABLE_STAGING_DEPLOY` | `false` until staging automation is configured; requires `ENABLE_IMAGE_PUSH=true` |
| `ENABLE_PRODUCTION_DEPLOY` | `false` until production automation is configured; requires `ENABLE_IMAGE_PUSH=true` |

#### Step 4: Update Deployment Commands

Edit the `deploy-staging` and `deploy-production` jobs with your actual deployment commands:

**Kubernetes Example:**
```bash
kubectl apply -f k8s/staging/ --kubeconfig="$KUBE_CONFIG_PATH"
```

**Docker Compose Example:**
```bash
docker compose -f docker-compose.staging.yml up -d
```

**Cloud Provider (AWS, GCP, Azure) Example:**
```bash
aws deploy create-deployment --application-name ai-stock-analyzer ...
```

### Local Testing Before Push

Test the pipeline locally before committing:

```bash
# Run tests
pytest --cov=.

# Run linting
flake8 .
black --check .
isort --check-only .

# Run security scans
pip install pipx
python -m pipx run --spec bandit==1.9.4 bandit -r . --severity-level low --confidence-level low
python -m pipx run --spec pip-audit==2.10.1 pip-audit -r requirements.txt
```

### Docker Build & Push

To manually build and push the Docker image:

```bash
# Set your image coordinates
export REGISTRY=ghcr.io
export IMAGE_NAME=OWNER/REPOSITORY
export IMAGE_TAG=latest

# Build image
docker build -t "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG" .

# Login to GitHub Container Registry
echo "$GHCR_TOKEN" | docker login "$REGISTRY" -u "$GHCR_USERNAME" --password-stdin

# Push image
docker push "$REGISTRY/$IMAGE_NAME:$IMAGE_TAG"
```

### Environment Variables for CI/CD

The reference workflow already sets test-only values for `FLASK_ENV`, `DATABASE_URL`, and `REDIS_URL` inline for the pytest step. Configure external CI/CD variables only for values your environment must supply, such as production logging or deployment-specific settings:

| Variable | Value |
| --- | --- |
| `LOG_LEVEL` | `INFO` or your preferred production log level |
| `DEPLOY_KEY` | Deployment credential for the production job |
| `SLACK_WEBHOOK` | Optional Slack webhook for notifications |

## Troubleshooting

### Workflow doesn't run
- Check that `.github/workflows/ci-cd.yml` exists (not `ci-cd-workflow.yml`)
- Wait 1-2 minutes after pushing
- Refresh the GitHub Actions page

### Tests fail locally but pass in CI
- Ensure PostgreSQL and Redis are running: `docker run -d -p 5432:5432 postgres:15-alpine`
- Check environment variables match CI configuration
- Review test logs for database connection issues

### Docker build fails
- Verify `Dockerfile` exists in repository root
- Check that all build dependencies are in `requirements.txt`
- Review build logs for specific errors

### Deployment fails
- Verify `DEPLOY_KEY` secret is set correctly
- Ensure deployment commands are updated for your infrastructure
- Check CloudFormation/Kubernetes manifests are correct
- Review deployment logs for error details

### Security scan fails
- Fix Bandit issues: update code or add `# nosec` comments
- Fix pip-audit issues: update vulnerable dependencies
- Review security reports in workflow logs

## Best Practices

1. **Always test locally:**
   - Run linting and tests before pushing
   - Fix issues early to avoid CI failures

2. **Keep dependencies updated:**
   - Review and test updated dependencies before merging
   - Use Dependabot for automated updates (optional)

3. **Monitor security:**
   - Review Bandit and pip-audit reports regularly
   - Fix security issues before production deployment

4. **Optimize build times:**
   - Use Docker layer caching
   - Cache pip dependencies in CI
   - Parallelize tests when possible

5. **Use feature branches:**
   - Create branches for new features
   - Run full CI/CD on pull requests
   - Review and merge to develop first

6. **Plan deployments:**
   - Test on staging before production
   - Use gradual rollouts for large changes
   - Keep deployment scripts version-controlled
