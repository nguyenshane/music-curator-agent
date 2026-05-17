"""Test Spotify adapter with real credentials."""
import asyncio
import os
import sys
from datetime import datetime, timezone

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Set env vars before importing config
os.environ['SPOTIFY_CLIENT_ID'] = '11922da15f904c86ad354aad0a695944'
os.environ['SPOTIFY_CLIENT_SECRET'] = '6bccc7c635ae4f868f916eee21f345eb'

from backend.adapters.spotify import SpotifyAdapter
from backend.adapters.types import ListenEvent, ExternalTrackRef


async def test_spotify_adapter():
    """Test the Spotify adapter."""
    print("=" * 60)
    print("Testing Spotify Adapter")
    print("=" * 60)
    
    # Test 1: Create adapter
    print("\n1. Creating SpotifyAdapter...")
    adapter = SpotifyAdapter()
    print(f"   ✓ Provider name: {adapter.provider_name}")
    
    # Test 2: Get access token
    print("\n2. Testing token acquisition...")
    try:
        token = adapter._get_access_token()
        print(f"   ✓ Token acquired: {token[:20]}... (length: {len(token)})")
        print(f"   ✓ Token expires at: {adapter._token_expires_at}")
    except Exception as e:
        print(f"   ✗ Token acquisition failed: {e}")
        return False
    
    # Test 3: Fetch top tracks
    print("\n3. Testing top tracks fetch...")
    try:
        top_tracks = adapter._fetch_top_tracks(user_id="shane")
        print(f"   ✓ Fetched {len(top_tracks)} top tracks")
        
        if top_tracks:
            print("\n   Sample tracks:")
            for i, event in enumerate(top_tracks[:3]):
                print(f"   {i+1}. {event.track.title} - {event.track.artist}")
                print(f"      Track ID: {event.track.track_id}")
                print(f"      ISRC: {event.track.isrc or 'N/A'}")
                print(f"      Duration: {event.track.duration_ms}ms")
                print(f"      Album: {event.track.album or 'N/A'}")
    except Exception as e:
        print(f"   ✗ Top tracks fetch failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 4: Fetch recent playlists
    print("\n4. Testing playlist tracks fetch...")
    try:
        playlist_tracks = adapter._fetch_recent_playlists(user_id="shane")
        print(f"   ✓ Fetched {len(playlist_tracks)} tracks from playlists")
        
        if playlist_tracks:
            print("\n   Sample playlist tracks:")
            for i, event in enumerate(playlist_tracks[:3]):
                print(f"   {i+1}. {event.track.title} - {event.track.artist}")
                print(f"      Track ID: {event.track.track_id}")
    except Exception as e:
        print(f"   ✗ Playlist tracks fetch failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 5: Full fetch_listens
    print("\n5. Testing full fetch_listens()...")
    try:
        all_listens = adapter.fetch_listens(user_id="shane")
        print(f"   ✓ Total listens fetched: {len(all_listens)}")
        
        # Verify ListenEvent structure
        if all_listens:
            event = all_listens[0]
            print(f"\n   Sample ListenEvent:")
            print(f"   - provider: {event.provider}")
            print(f"   - user_id: {event.user_id}")
            print(f"   - played_at: {event.played_at}")
            print(f"   - track:")
            print(f"     - provider: {event.track.provider}")
            print(f"     - track_id: {event.track.track_id}")
            print(f"     - title: {event.track.title}")
            print(f"     - artist: {event.track.artist}")
            print(f"     - isrc: {event.track.isrc}")
            print(f"     - duration_ms: {event.track.duration_ms}")
            print(f"     - album: {event.track.album}")
    except Exception as e:
        print(f"   ✗ fetch_listens() failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test 6: Verify ListenEvent type
    print("\n6. Verifying ListenEvent type...")
    if all_listens:
        if isinstance(all_listens[0], ListenEvent):
            print("   ✓ ListenEvent is correct type")
        else:
            print(f"   ✗ ListenEvent is {type(all_listens[0])}, expected ListenEvent")
            return False
    
    # Test 7: Verify ExternalTrackRef type
    print("\n7. Verifying ExternalTrackRef type...")
    if all_listens:
        if isinstance(all_listens[0].track, ExternalTrackRef):
            print("   ✓ ExternalTrackRef is correct type")
        else:
            print(f"   ✗ Track is {type(all_listens[0].track)}, expected ExternalTrackRef")
            return False
    
    print("\n" + "=" * 60)
    print("All tests passed! ✓")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = asyncio.run(test_spotify_adapter())
    sys.exit(0 if success else 1)
