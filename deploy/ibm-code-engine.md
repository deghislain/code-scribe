# Deploying Code-Scribe on IBM Cloud (Free Tier)

## What you get for free

| Service | Free allowance |
|---|---|
| **IBM Cloud Code Engine** | 100 000 vCPU-seconds + 200 000 GB-seconds + 100 000 HTTP requests / month |
| **IBM Cloud Container Registry** | 512 MB storage + 5 GB pull traffic / month |

Both quotas reset monthly and are sufficient for personal / demo use.

---

## Prerequisites

- IBM Cloud account (free — [cloud.ibm.com](https://cloud.ibm.com))
- IBM Cloud CLI + Code Engine and Container Registry plugins
- Docker (to build and push the image)

### Install the CLI tools

```bash
# IBM Cloud CLI
curl -fsSL https://clis.cloud.ibm.com/install/linux | sh

# Required plugins
ibmcloud plugin install code-engine
ibmcloud plugin install container-registry

# Log in
ibmcloud login --sso          # follow the browser prompt
ibmcloud target --cf          # skip if you don't use Cloud Foundry
```

---

## Step 1 — Set up IBM Cloud Container Registry

```bash
# Target the registry region closest to you (us-south shown here)
ibmcloud cr region-set us-south

# Create a namespace for your images (must be globally unique)
ibmcloud cr namespace-add code-scribe

# Log Docker into the registry
ibmcloud cr login
```

---

## Step 2 — Build and push the container image

Run these commands from the project root (where `Dockerfile` lives):

```bash
IMAGE=us.icr.io/code-scribe/code-scribe:latest

docker build -t $IMAGE .
docker push $IMAGE
```

> **Tip:** The first build takes a few minutes because of the WeasyPrint system
> dependencies. Subsequent builds reuse the Docker layer cache.

---

## Step 3 — Create a Code Engine project

```bash
ibmcloud ce project create --name code-scribe
ibmcloud ce project select  --name code-scribe
```

---

## Step 4 — Store your secrets

Code-Scribe reads `GROQ_API_KEY` and optionally `GROQ_MODEL` from the environment.
Store them as a Code Engine secret (never put secrets in the image):

```bash
ibmcloud ce secret create \
  --name code-scribe-secrets \
  --from-literal GROQ_API_KEY=<your-groq-api-key> \
  --from-literal GROQ_MODEL=llama-3.3-70b-versatile
```

---

## Step 5 — Deploy the application

```bash
ibmcloud ce application create \
  --name code-scribe \
  --image us.icr.io/code-scribe/code-scribe:latest \
  --registry-secret ce-auto-icr-us-south \
  --port 8080 \
  --cpu 0.5 \
  --memory 2G \
  --min-scale 0 \
  --max-scale 1 \
  --env-from-secret code-scribe-secrets \
  --env OUTPUT_DIR=/data/outputs \
  --env WORKSPACE_DIR=/data/workspaces \
  --env DB_PATH=/data/code_scribe.db
```

**Key flags explained:**

| Flag | Why |
|---|---|
| `--min-scale 0` | App scales to zero when idle — no charges while unused |
| `--max-scale 1` | One instance cap keeps you inside the free tier |
| `--cpu 0.5` / `--memory 2G` | Valid free-tier combination; enough for WeasyPrint + Groq calls |
| `--registry-secret ce-auto-icr-us-south` | Auto-generated secret Code Engine creates when you use ICR in the same account |

After a few seconds the CLI prints a public HTTPS URL — open it in your browser.

```bash
# Check status
ibmcloud ce application get --name code-scribe

# Get the URL
ibmcloud ce application get --name code-scribe --output url
```

---

## Step 6 — Redeploy after code changes

```bash
docker build -t $IMAGE . && docker push $IMAGE

ibmcloud ce application update \
  --name code-scribe \
  --image us.icr.io/code-scribe/code-scribe:latest
```

---

## Step 7 — View logs

```bash
# Streaming logs
ibmcloud ce application logs --name code-scribe --follow
```

---

## ⚠️  Free-tier limitations to be aware of

### Ephemeral storage
Code Engine does **not** provide persistent volumes on the free tier. The
SQLite database (`code_scribe.db`), generated PDFs (`outputs/`), and cloned
repositories (`workspaces/`) live inside the container and are **lost when
the instance restarts or scales to zero**.

**Workaround options:**
- For demos/testing this is acceptable — just re-run a job after each restart.
- For persistence, provision an [IBM Cloud Object Storage](https://cloud.ibm.com/objectstorage/create)
  Lite bucket (also free), mount it via the `s3fs` sidecar pattern, or use
  the `ibmcloud ce app update --mount-configmap` approach once IBM COS
  integration with Code Engine is configured.

### Cold starts
With `--min-scale 0` the app stops when idle. The first request after a
period of inactivity triggers a cold start (~10–20 s). Set `--min-scale 1`
to avoid this, but note that keeping one instance alive will consume your
free vCPU-seconds faster.

### WebSocket support
Code Engine supports HTTP/2 and WebSocket connections. The log-streaming
WebSocket (`/ws/logs`) works as-is through the Code Engine HTTPS endpoint.

---

## Useful commands cheat-sheet

```bash
# List all apps in the project
ibmcloud ce application list

# Delete the app (stops billing immediately)
ibmcloud ce application delete --name code-scribe

# Delete the project
ibmcloud ce project delete --name code-scribe --force

# Check Container Registry usage vs free quota
ibmcloud cr quota
```
