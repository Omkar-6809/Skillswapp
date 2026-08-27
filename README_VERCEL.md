# SkillSwap — Vercel-ready build

This build keeps SQLite for local/PythonAnywhere use and adds PostgreSQL support for Vercel.

## Important
Vercel's runtime filesystem is not a persistent database. For a real deployment, create a hosted PostgreSQL database and set `DATABASE_URL` in Vercel Environment Variables.

## Existing users
Do NOT delete your current `database.db`. The current SQLite users are not automatically copied to PostgreSQL by this package. Export/import them first, then deploy with the PostgreSQL `DATABASE_URL`.

## Deploy
1. Put this project in a Git repository or import it into Vercel.
2. Set `DATABASE_URL` to your hosted PostgreSQL connection string.
3. Set a strong `SECRET_KEY` in Vercel Environment Variables.
4. Deploy.

Vercel officially supports Flask through its Python runtime/serverless functions.
