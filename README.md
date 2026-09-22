# HACKDAYS22

This repo is set up as one project with two apps:

- `frontend/` - Next.js UI
- `backend/` - FastAPI API server

Keep frontend and backend code in their own folders, but commit them together in this root repo.

## Run Locally

Install frontend dependencies:

```powershell
cd frontend
npm install
```

Install backend dependencies:

```powershell
cd ../backend
pip install -r requirements.txt
```

From the repo root, run both apps:

```powershell
npm run dev
```

Frontend: `http://localhost:3000`

Backend: `http://127.0.0.1:8000`

## Calling Backend From Frontend

Frontend code should call backend routes with relative URLs:

```ts
const response = await fetch("/api/carbon");
const data = await response.json();
```

Next.js proxies `/api/*` requests to the FastAPI backend while developing.
