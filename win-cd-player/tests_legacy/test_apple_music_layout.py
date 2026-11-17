import sys
import os

sys.path.insert(0, os.path.abspath('win-cd-player'))
import main

html = main.APPLE_MUSIC_HTML
print(f"HTML length: {len(html)} characters")

apple_music_ids = [
    'viewCd', 'viewLibrary', 'viewLyrics',
    'dockArtworkImg', 'dockTrackTitle', 'dockArtist',
    'dockPlayPauseBtn', 'dockPlayIcon', 'dockPauseIcon',
    'dockElapsedTime', 'dockScrubberHit', 'dockScrubberFill', 'dockRemainingTime',
    'dockModeIcon', 'dockVolHit', 'dockVolFill',
    'libraryGrid', 'immersiveVinylStage', 'mdAmbientBg', 'mdPlaylistMorphBtn'
]

missing = [i for i in apple_music_ids if f'id="{i}"' not in html]
if missing:
    print('Missing Apple Music IDs:', missing)
    sys.exit(1)
else:
    print('All Apple Music layout IDs successfully verified!')

# Verify core functions
for fn in ['switchMainView', 'toggleImmersiveVinylMode', 'renderLibraryCards', 'playLocalAlbumWithAnimation', 'toggleFluidPlaylistMorph']:
    if fn not in html:
        print(f"Missing JS function: {fn}")
        sys.exit(1)
    else:
        print(f"Found JS function: {fn}")

print("Apple Music Layout Integration Test Passed!")
