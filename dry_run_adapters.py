"""Dry run: test all provider adapters with current .env config."""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Load .env
dotenv_path = os.path.join(os.path.dirname(__file__), 'backend', '.env')
if os.path.exists(dotenv_path):
    with open(dotenv_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                # Remove surrounding quotes
                val = val.strip("'\"")
                os.environ[key] = val

from backend.config import get_settings
from backend.adapters.spotify import SpotifyAdapter
from backend.adapters.lastfm import LastFMAdapter
from backend.adapters.musicbrainz import MusicBrainzAdapter
from backend.adapters.tidal import TidalAdapter
from backend.adapters.ytmusic import YTMusicAdapter

settings = get_settings()

print("=" * 70)
print("  PROVIDER ADAPTER DRY RUN")
print("=" * 70)

# Check which providers are enabled
print("\n[CONFIGURATION]")
print(f"  SPOTIFY enabled:  {settings.is_provider_enabled('spotify')}")
print(f"  LASTFM enabled:   {settings.is_provider_enabled('lastfm')}")
print(f"  TIDAL enabled:    {settings.is_provider_enabled('tidal')}")
print(f"  YTMUSIC enabled:  {settings.is_provider_enabled('ytmusic')}")
print(f"  MUSICBRAINZ enabled: {settings.is_provider_enabled('musicbrainz')}")

providers = [
    ("spotify", SpotifyAdapter),
    ("lastfm", LastFMAdapter),
    ("musicbrainz", MusicBrainzAdapter),
    ("tidal", TidalAdapter),
    ("ytmusic", YTMusicAdapter),
]

total_events = 0
results = []

for name, AdapterClass in providers:
    print(f"\n[{name.upper()}] {AdapterClass.__name__}")
    try:
        adapter = AdapterClass()
        events = adapter.fetch_listens(user_id="shane")
        total_events += len(events)
        status = "OK" if len(events) > 0 else "EMPTY (no data)"
        results.append((name, status, len(events)))
        print(f"  Events fetched: {len(events)}")
        if events:
            for i, e in enumerate(events[:3]):
                print(f"    {i+1}. {e.track.title} - {e.track.artist}")
                print(f"       ID: {e.track.track_id} | ISRC: {e.track.isrc or 'N/A'}")
            if len(events) > 3:
                print(f"    ... and {len(events) - 3} more")
        else:
            print(f"  Status: No data returned")
    except Exception as e:
        results.append((name, f"ERROR: {e}", 0))
        print(f"  ✗ Error: {e}")

print("\n" + "=" * 70)
print(f"  SUMMARY: {total_events} total events across all providers")
print("=" * 70)
for name, status, count in results:
    icon = "✓" if count > 0 else "○"
    print(f"  {icon} {name:12s} → {status} ({count} events)")
print("=" * 70)
