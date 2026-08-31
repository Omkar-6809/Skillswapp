# SkillSwap — Vercel + PostgreSQL build

This version is configured for **PostgreSQL only** (for example, Neon PostgreSQL).

## Database

Set this Vercel Environment Variable:

`DATABASE_URL`

Use the complete Neon PostgreSQL connection string. A pooled connection is recommended.

There is **no SQLite fallback** and this project does not use `database.db`.

The application creates these PostgreSQL tables automatically when it starts:

- users
- skills
- requests
- ratings
- teams
- team_members
- conversations
- messages
- message_reports

## Deploy

1. Push the complete project to GitHub.
2. Import the repository into Vercel.
3. Add `DATABASE_URL` in Vercel Environment Variables.
4. Add a strong `SECRET_KEY` in Vercel Environment Variables.
5. Deploy.

## Important

Vercel's local filesystem is not persistent. Profile image uploads are placed in `/tmp` on Vercel, so uploaded images can disappear when a serverless instance is recycled. Persistent image storage should use object storage if profile photos are required long-term.
