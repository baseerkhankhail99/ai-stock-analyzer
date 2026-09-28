# AI Stock Analyzer - Deployment & CI/CD Guide

## GitHub Actions CI/CD Pipeline

This guide provides a comprehensive CI/CD workflow configuration for automated testing, building, and deployment.

### Pipeline Overview

The recommended GitHub Actions workflow includes the following jobs:

#### 1. **Test & Lint Job**
Runs on every push and pull request to main/develop branches.

```yaml
- Python 3.11 setup with dependency caching
- Flake8 linting (syntax and complexity checks)
- Black code formatting verification
- isort import sorting verification
- pytest with coverage reporting
- Codecov integration for coverage tracking
```

**Services:**
- PostgreSQL 15-alpine (for database tests)
- Redis 7-alpine (for caching tests)

#### 2. **Security Scan Job**
Performs security audits on code and dependencies.

```yaml
- Bandit: scans for common security issues
- Safety: checks dependencies for known vulnerabilities
```

#### 3. **Build Job**
Builds and pushes Docker image to GitHub Container Registry.

```yaml
- Triggers on push to main/develop (after tests pass)
- Builds multi-platform Docker image
- Pushes to ghcr.io with semantic versioning
- Tags: branch, semver, and SHA
```

#### 4. **Deploy to Staging Job**
Deploys to staging when code is pushed to develop branch.

```yaml
- Runs after successful Docker build
- Example: kubectl apply -f k8s/staging/
- Or: docker compose -f docker-compose.staging.yml up -d
```

#### 5. **Deploy to Production Job**
Deploys to production when code is pushed to main branch.

```yaml
- Runs after successful Docker build
- Requires environment approval
- Uses production secrets (DEPLOY_KEY)
- Example: kubectl apply -f k8s/production/
```

#### 6. **Notify Job**
Sends notifications (e.g., Slack) on pipeline completion.

### Setup Instructions

#### Step 1: Create Workflow File

Create `.github/workflows/ci-cd.yml` in your repository with the pipeline configuration provided in this guide.

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

#### Step 4: Update Deployment Commands

Edit the `deploy-staging` and `deploy-production` jobs with your actual deployment commands:

**Kubernetes Example:**
```bash
kubectl apply -f k8s/staging/ --kubeconfig=${{ secrets.KUBE_CONFIG }}
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
bandit -r .
safety check
```

### Docker Build & Push

To manually build and push the Docker image:

```bash
# Build image
docker build -t ghcr.io/baseerkhankhail99/ai-stock-analyzer:latest .

# Login to GitHub Container Registry
echo ${{ secrets.GITHUB_TOKEN }} | docker login ghcr.io -u ${{ github.actor }} --password-stdin

# Push image
docker push ghcr.io/baseerkhankhail99/ai-stock-analyzer:latest
```

### Environment Variables for CI/CD

Ensure these environment variables are set in your CI/CD platform:

| Variable | Value |
| --- | --- |
| `FLASK_ENV` | `testing` (for tests), `production` (for deploy) |
| `DATABASE_URL` | PostgreSQL test database connection string |
| `REDIS_URL` | Redis test instance connection string |
| `LOG_LEVEL` | `DEBUG` (for tests), `INFO` (for production) |

### Monitoring Pipeline

View workflow runs in GitHub:

1. Navigate to **Actions** tab in your repository
2. Click on the workflow run
3. Review job logs and status
4. Check code coverage in Codecov integration

### Troubleshooting

**Tests fail locally but pass in CI:**
- Ensure PostgreSQL and Redis are running locally
- Check environment variables are set correctly
- Run tests with: `pytest -v`

**Docker build fails:**
- Verify all files are committed (Dockerfile, requirements.txt, etc.)
- Check registry credentials with: `docker login ghcr.io`
- Review Docker build logs in GitHub Actions

**Deployment fails:**
- Verify deployment secrets are set correctly
- Check deployment target is accessible
- Review deployment logs in GitHub Actions UI
- Ensure kubeconfig or cloud credentials are valid

**Coverage reports not uploading:**
- Install codecov: `pip install codecov`
- Verify coverage.xml is generated: `pytest --cov-report=xml`
- Check Codecov token in repository settings

### Best Practices

1. **Always use branch protection rules:**
   - Require status checks to pass before merging
   - Require code reviews
   - Dismiss stale reviews on push

2. **Keep dependencies updated:**
   - Use Dependabot for automated dependency PRs
   - Review and test updated dependencies before merging

3. **Monitor security:**
   - Review Bandit and Safety reports regularly
   - Fix security issues before production deployment

4. **Optimize build times:**
   - Use Docker layer caching
   - Cache pip dependencies
   - Use parallel job execution where possible

5. **Document deployment process:**
   - Keep this guide updated
   - Document all secrets required
   - Create runbooks for common issues

### Additional Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Docker Push Action](https://github.com/docker/build-push-action)
- [Codecov Action](https://github.com/codecov/codecov-action)
- [Slack Notifications](https://github.com/8398a7/action-slack)
