# BLUFF Online

Mobile-friendly four-player Bluff using three standard decks. Enter any three-digit room code (000–999). The first join creates a room with a shared ten-second countdown; four seats maximum, bots fill vacancies when countdown reaches zero. Rooms expire 25 minutes after creation, including active matches and rematches; the same code can then create a new game. Started rooms cannot accept new players, but existing player tokens can reconnect before expiry or disconnect timeout. Codes are convenience identifiers, not passwords. Game state is in memory, so deployments and restarts clear rooms.

Run locally: `python3 app.py` (port 8000). On Render use `gunicorn --workers 1 --threads 8 --bind 0.0.0.0:$PORT app:application` and install requirements.txt. A single worker is required for shared in-memory state.

Run room lifecycle tests: `python3 -m unittest test_rooms`.
