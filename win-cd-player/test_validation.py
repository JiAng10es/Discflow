import sys
import os

sys.path.insert(0, os.path.abspath('win-cd-player'))
import main

html = main.APPLE_MUSIC_HTML
print(f"HTML length: {len(html)} characters")

required_ids = [
    'immersiveVinylStage', 'mdAmbientBg', 'mdPlaylistMorphBtn', 'immersiveCoverImg',
    'immersiveVinylDisc', 'immersiveTonearm', 'immersiveLabelImg',
    'mdLabelTrackTitle', 'mdLabelArtist', 'mdLyricsSheet', 'mdDockThumbImg',
    'immersiveTrackTitle', 'immersiveArtistName', 'mdDockPlayPauseBtn',
    'libraryGrid', 'viewLibrary', 'trackTitle', 'artworkImg', 'scrubberHit'
]

missing = [i for i in required_ids if f'id="{i}"' not in html]
if missing:
    print('Missing IDs:', missing)
    sys.exit(1)
else:
    print('All required IDs present in APPLE_MUSIC_HTML!')

# Also test library_service and cd_engine imports and functions
import cd_engine
import library_service

lib = library_service.DiscflowLibraryManager()
albums = lib.list_albums()
print(f"Found {len(albums)} albums via library_service.")
if albums:
    toc = lib.get_album_toc(albums[0].disc_id)
    print(f"Loaded TOC for album: {albums[0].title}, tracks: {len(toc.tracks) if toc else 0}")
    print(f"First track local_filepath: {toc.tracks[0].local_filepath if toc and toc.tracks else 'None'}")

print("Validation script completed successfully.")
