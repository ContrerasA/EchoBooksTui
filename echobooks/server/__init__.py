"""The optional self-hosted EchoBooks sync server.

Importing this package requires the ``server`` extra (``pip install
echobooks[server]``) — it pulls in FastAPI, a Postgres driver, Google OAuth and
JWT libraries. The offline client never imports anything under here.
"""
