"""
FastAPI Mini App server. Runs in the same Python process (and same asyncio
event loop) as the Telegram bot — see bot.py::main() for wiring.

Serves:
  - /api/*   — auth, admin store CRUD, "me" endpoints (JSON REST)
  - /       — static Mini App (login/signup for customers)
  - /admin  — static Mini App (store management for admins)
"""
