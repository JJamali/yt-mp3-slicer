import os
import re
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Tuple, Optional
import threading
import tempfile
import pygame
import yt_dlp
import requests
from io import BytesIO
from PIL import Image, ImageTk
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TALB, TPE1


class Track:
    def __init__(self, title: str, start_time: str, end_time: str = None, artist: str = None):
        self.title = title.strip()
        self.start_time = start_time
        self.end_time = end_time
        self.artist = artist.strip() if artist else ""
    
    def __str__(self):
        end = f" - {self.end_time}" if self.end_time else ""
        artist_str = f" by {self.artist}" if self.artist else ""
        return f"{self.title}{artist_str}: {self.start_time}{end}"

class YouTubeAlbumSplitter:
    def __init__(self, root_gui): # Pass root_gui to access its attributes
        self.video_info = None
        self.audio_file = None
        self.tracks = []
        self.root_gui = root_gui # Store reference to the GUI instance
    
    def parse_timestamp(self, timestamp: str) -> int:
        """Convert timestamp string to seconds"""
        try:
            parts = timestamp.strip().split(':')
            if len(parts) == 2:  # mm:ss
                minutes, seconds = map(int, parts)
                return minutes * 60 + seconds
            elif len(parts) == 3:  # hh:mm:ss
                hours, minutes, seconds = map(int, parts)
                return hours * 3600 + minutes * 60 + seconds
            else:
                raise ValueError("Invalid timestamp format")
        except ValueError:
            raise ValueError(f"Invalid timestamp format: {timestamp}")
    
    def seconds_to_timestamp(self, seconds: int) -> str:
        """Convert seconds to mm:ss or hh:mm:ss format"""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"
    
    def extract_timestamps_from_description(self, description: str) -> List[Track]:
        """
        Extract track information from video description.
        Improved logic to handle various formats including timestamp before or after title,
        and Japanese characters/symbols.
        """
        tracks = []
        
        # Define patterns for different common formats
        # Pattern 1: Track number (optional), Title, Timestamp, optional trailing ~
        # Example: "１.愛のゆくえ 0:03〜", "1 - The Sea 00:00"
        # Group 1: Title, Group 2: Timestamp
        pattern_title_before_ts = re.compile(
            r'^(?:[０-９]+\.?\s*[-–—]?\s*)?'  # Optional leading track number (half/full-width), period, space, hyphen
            r'(.+?)'                          # Non-greedy capture of the title
            r'\s*(\d{1,2}:\d{2}(?::\d{2})?)'  # Capture the timestamp
            r'\s*[-–—~〜]?\s*$'               # Optional separators and whitespace at end
            , re.UNICODE
        )

        # Pattern 2: Timestamp, optional separators, Title
        # Example: "00:00 - The Sea", "05:31 Natsuno Yoru no Machi"
        # Group 1: Timestamp, Group 2: Title
        pattern_ts_before_title = re.compile(
            r'(\d{1,2}:\d{2}(?::\d{2})?)'  # Capture the timestamp
            r'\s*[-–—~〜]?\s*'             # Optional separators and whitespace
            r'(.+)'                        # Capture the rest of the line as title
            , re.UNICODE
        )
        
        lines = description.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip lines that are likely headers or footers for tracklists
            if any(skip_word in line.lower() for skip_word in ['tracklist', 'track list', 'playlist', 'setlist']):
                continue
            
            title = None
            start_time = None

            # Try Pattern 1 (Title before Timestamp) first
            match = pattern_title_before_ts.match(line)
            if match:
                title = match.group(1).strip()
                start_time = match.group(2)
            else:
                # If Pattern 1 doesn't match, try Pattern 2 (Timestamp before Title)
                match = pattern_ts_before_title.match(line)
                if match:
                    start_time = match.group(1)
                    title = match.group(2).strip()

            if title and start_time:
                # Apply general cleanup to the extracted title
                # Remove any text within parentheses or square brackets (e.g., [Official Video], (Live))
                title = re.sub(r'[\[\(].*?[\]\)]', '', title).strip() 
                # Remove leading/trailing quotes (single, double, Japanese)
                title = title.strip('「」『』""\'\'') 
                # Remove any trailing tilde or similar symbols
                title = title.rstrip('~〜')
                # NEW: More comprehensive cleanup for leading noise (numbers, punctuation, spaces, etc.)
                # This regex matches one or more occurrences of common leading junk characters
                # including hyphens, spaces, periods, digits (half-width and full-width Japanese),
                # and various bracket/quote characters at the beginning of the string.
                title = re.sub(r'^[-\s\.\d０-９\[\]\(\)「」『』"\'~〜]+', '', title, flags=re.UNICODE).strip()

                if not title or title.isdigit(): # Skip if title is empty or just numbers
                    continue
                
                try:
                    self.parse_timestamp(start_time) # Validate timestamp
                    
                    # Check for duplicates before adding to avoid redundant tracks
                    if not any(t.title == title and t.start_time == start_time for t in tracks):
                        tracks.append(Track(title, start_time)) # End time will be set in post-processing
                except ValueError:
                    # If timestamp is invalid, skip this line
                    continue
        
        # Sort tracks by their start time to ensure correct ordering
        tracks.sort(key=lambda t: self.parse_timestamp(t.start_time))
        
        # Assign end times based on the start time of the next track
        for i in range(len(tracks)):
            if i < len(tracks) - 1:
                tracks[i].end_time = tracks[i + 1].start_time
            # The last track's end_time remains None, which is handled by split_audio to go until the end of the audio.
        
        return tracks
    
    def download_audio(self, url: str, progress_callback=None) -> str:
        """Download audio from YouTube video"""
        def progress_hook(d):
            if progress_callback and d['status'] == 'downloading':
                if 'downloaded_bytes' in d and 'total_bytes' in d:
                    percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
                    progress_callback(f"Downloading: {percent:.1f}%")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'extractaudio': True,
            'audioformat': 'mp3',
            'outtmpl': 'temp_audio.%(ext)s',
            'quiet': True,
            'nooverwrites': True,
            'continuedl': True,
            'retries': 10,
            'fragment-retries': 10,
            'skip-unavailable-fragments': True,
            'extractor-args': 'youtube:player_client=android',
            'http-chunk-size': '1M',
            'progress_hooks': [progress_hook] if progress_callback else [],
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    self.video_info = ydl.extract_info(url, download=False)
                except Exception as e:
                    raise Exception(f"Error getting video info: {e}")
                
                try:
                    ydl.download([url])
                except yt_dlp.utils.DownloadError:
                    ydl_opts['extractor-args'] = 'youtube:player_client=web'
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_retry:
                        ydl_retry.download([url])
            
            for file in os.listdir('.'):
                if file.startswith('temp_audio'):
                    self.audio_file = file
                    break
            
            if not self.audio_file:
                raise Exception("Failed to download audio file")
            
            return self.audio_file
        except Exception as e:
            raise Exception(f"Failed to download audio: {e}")
        
    def split_audio(self, tracks: List[Track], output_dir: str = "output", cropped_thumbnail_data: bytes = None, progress_callback=None):
        """Split audio file into individual tracks with thumbnails"""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Use the provided cropped_thumbnail_data or download original if not provided
        final_thumbnail_data = cropped_thumbnail_data
        if not final_thumbnail_data and self.video_info and 'thumbnail' in self.video_info:
            try:
                response = requests.get(self.video_info['thumbnail'])
                final_thumbnail_data = response.content
            except Exception as e:
                print(f"Couldn't download original thumbnail: {e}")

        for i, track in enumerate(tracks, 1):
            if progress_callback:
                progress_callback(f"Processing track {i}/{len(tracks)}: {track.title}")

            start_seconds = self.parse_timestamp(track.start_time)
            
            safe_title = re.sub(r'[<>:"/\\|?*]', '', track.title)
            safe_title = safe_title[:100]
            output_file = os.path.join(output_dir, f"{i:02d}. {safe_title}.mp3")

            # Split audio (same as before)
            cmd = [
                'ffmpeg', '-i', self.audio_file,
                '-ss', str(start_seconds),
                '-y'
            ]
            
            if track.end_time:
                end_seconds = self.parse_timestamp(track.end_time)
                duration = end_seconds - start_seconds
                cmd.extend(['-t', str(duration)])
            
            cmd.extend([
                '-acodec', 'mp3',
                '-ab', '192k',
                output_file
            ])
            
            try:
                # Removed creationflags=subprocess.CREATE_NO_WINDOW
                subprocess.run(cmd, check=True, capture_output=True) 
                
                # Add metadata and thumbnail
                if final_thumbnail_data:
                    # Pass the track.artist to add_mp3_metadata
                    self.add_mp3_metadata(output_file, track.title, track.artist, final_thumbnail_data)
                    
            except subprocess.CalledProcessError as e:
                raise Exception(f"Failed to create {track.title}: {e.stderr.decode()}")
    
    def add_mp3_metadata(self, filepath: str, title: str, artist: str, thumbnail_data: bytes):
        """Add ID3 tags and thumbnail to MP3 file with optional cropping"""
        try:
            audio = MP3(filepath, ID3=ID3)
            
            # Add ID3 tag if it doesn't exist
            try:
                audio.add_tags()
            except:
                pass
            
            # Add thumbnail (album art)
            audio.tags.add(APIC(
                encoding=3,  # UTF-8
                mime='image/jpeg', # Assuming JPEG for thumbnails
                type=3,      # Cover image
                desc='Cover',
                data=thumbnail_data  # This will be the cropped version if user selected one
            ))
            
            # Add basic metadata
            audio.tags.add(TIT2(encoding=3, text=title))  # Title
            audio.tags.add(TALB(encoding=3, text="YouTube Album"))  # Album
            audio.tags.add(TPE1(encoding=3, text=artist))  # Artist (using the provided artist)
            
            audio.save()
        except Exception as e:
            print(f"Couldn't add metadata to {filepath}: {e}")

    def cleanup(self):
        """Remove temporary files"""
        if self.audio_file and os.path.exists(self.audio_file):
            os.remove(self.audio_file)

# The AudioPreview class is largely removed, its core functionality for creating temporary
# preview files is moved into AudioPlayerControl, and its playback logic directly uses pygame.mixer.
# This simplifies the architecture by removing a redundant abstraction layer for previews.

class AudioPlayerControl(ttk.Frame):
    def __init__(self, parent, root_gui_ref): # Renamed `preview_manager` to `root_gui_ref`
        super().__init__(parent)
        pygame.mixer.init() # Initialize mixer once here
        self.root_gui_ref = root_gui_ref # Reference to the main GUI instance
        self.is_playing = False
        self.current_position = 0
        self.duration = 0
        self.preview_file = None # To store the path of the current temporary preview file
        self.playback_start_offset = 0 # Added for accurate seek/playback position
        self.setup_ui()
        self.update_interval = 250  # ms
        self.after_id = None

    def setup_ui(self):
        # Playback controls
        # Unified Play/Pause button
        self.play_btn = ttk.Button(self, text="▶", width=3, command=self.toggle_playback)
        self.play_btn.grid(row=0, column=0, padx=5)
        
        self.stop_btn = ttk.Button(self, text="■", width=3, command=self.stop_playback)
        self.stop_btn.grid(row=0, column=1, padx=5)
        
        # Time display
        self.time_var = tk.StringVar(value="00:00 / 00:00")
        ttk.Label(self, textvariable=self.time_var).grid(row=0, column=2, padx=10)
        
        # Seek slider
        self.seek_var = tk.DoubleVar(value=0)
        self.seek_slider = ttk.Scale(
            self, 
            from_=0, 
            to=100, 
            variable=self.seek_var, 
            command=self.on_seek,
            length=300
        )
        self.seek_slider.grid(row=0, column=3, padx=10)
        
        # Volume control
        self.volume_var = tk.DoubleVar(value=70)
        self.volume_slider = ttk.Scale(
            self,
            from_=0,
            to=100,
            variable=self.volume_var,
            command=self.on_volume_change,
            length=100,
            orient=tk.HORIZONTAL
        )
        self.volume_slider.grid(row=0, column=4, padx=10)
        
        # Volume icon
        self.volume_icon = ttk.Label(self, text="🔊")
        self.volume_icon.grid(row=0, column=5, padx=5)
        
        # Set initial volume
        pygame.mixer.music.set_volume(self.volume_var.get() / 100)
        
        # Configure grid weights
        self.columnconfigure(3, weight=1)

    def load_track_for_playback(self, track: Track):
        """
        Loads a portion of the selected track for immediate playback control.
        Creates a temporary preview file for this purpose.
        """
        self.stop_playback() # Stop and clear any existing preview

        if not self.root_gui_ref.splitter.audio_file:
            self.set_duration(0)
            self.root_gui_ref.status_var.set("Please download audio first to preview tracks.")
            return

        # Start a new thread for ffmpeg processing to avoid blocking the GUI
        threading.Thread(target=self._load_track_in_thread, args=(track,), daemon=True).start()

    def _load_track_in_thread(self, track: Track):
        try:
            start_sec = self.root_gui_ref.splitter.parse_timestamp(track.start_time)
            
            # Determine the effective end time for the preview
            end_sec_for_preview = None
            if track.end_time:
                end_sec_for_preview = self.root_gui_ref.splitter.parse_timestamp(track.end_time)
            else:
                full_audio = MP3(self.root_gui_ref.splitter.audio_file)
                end_sec_for_preview = int(full_audio.info.length)

            if end_sec_for_preview is not None:
                preview_length_sec = end_sec_for_preview - start_sec
            else:
                full_audio = MP3(self.root_gui_ref.splitter.audio_file)
                preview_length_sec = int(full_audio.info.length) - start_sec

            if preview_length_sec <= 0:
                self.root_gui_ref.root.after(0, lambda: self.root_gui_ref.status_var.set("Track has zero or negative duration. Cannot preview."))
                return

            self.root_gui_ref.root.after(0, lambda: self.root_gui_ref.status_var.set(f"Creating preview for: {track.title}... (this may take a moment)"))
            
            # Create a temporary preview file
            self.preview_file = tempfile.mktemp(suffix='.mp3')
            cmd = [
                'ffmpeg', '-i', self.root_gui_ref.splitter.audio_file,
                '-ss', str(start_sec),
                '-t', str(preview_length_sec),
                '-acodec', 'libmp3lame', # Use libmp3lame for better quality/compatibility
                '-q:a', '4', # Variable bitrate, good quality
                '-y', self.preview_file
            ]
            # Removed creationflags=subprocess.CREATE_NO_WINDOW
            subprocess.run(cmd, check=True, capture_output=True) 
            
            # Load and prepare playback in the main thread
            self.root_gui_ref.root.after(0, lambda: self._finalize_playback_load(preview_length_sec, track.title))

        except subprocess.CalledProcessError as e:
            self.root_gui_ref.root.after(0, lambda: messagebox.showerror("Error", f"Failed to create preview: {e.stderr.decode()}"))
            self.root_gui_ref.root.after(0, self.reset)
            self.root_gui_ref.root.after(0, lambda: self.root_gui_ref.status_var.set("Error creating preview."))
        except Exception as e:
            self.root_gui_ref.root.after(0, lambda: messagebox.showerror("Error", f"Error loading track for playback: {e}"))
            self.root_gui_ref.root.after(0, self.reset)
            self.root_gui_ref.root.after(0, lambda: self.root_gui_ref.status_var.set("Error loading track."))

    def _finalize_playback_load(self, preview_length_sec: int, track_title: str):
        """Called in the main thread after ffmpeg completes in the background."""
        try:
            pygame.mixer.music.load(self.preview_file)
            self.set_duration(preview_length_sec) # Set duration for slider
            self.play_btn.config(text="▶") # Set to play symbol
            self.current_position = 0
            self.seek_var.set(0)
            self.update_time_display()
            self.root_gui_ref.status_var.set(f"Loaded for playback: {track_title}")
        except Exception as e:
            messagebox.showerror("Error", f"Error finalizing playback: {e}")
            self.reset()
            self.root_gui_ref.status_var.set("Error finalizing playback.")

    def toggle_playback(self):
        if self.preview_file is None:
            self.root_gui_ref.status_var.set("No track loaded. Select a track to play.")
            return

        if self.is_playing:
            self.pause_playback()
        else:
            self.start_playback()

    def start_playback(self):
        if self.preview_file is None: return # Should not happen if called after load_track_for_playback

        if not pygame.mixer.music.get_busy() or pygame.mixer.music.get_pos() == -1: # -1 means stopped or not playing
            pygame.mixer.music.play(start=self.current_position) # Start from current position
            self.playback_start_offset = self.current_position # Store the offset
        else:
            pygame.mixer.music.unpause()
        
        self.is_playing = True
        self.play_btn.config(text="❚❚")  # Pause symbol
        self.update_playback_position()
        self.root_gui_ref.status_var.set("Playing...")

    def pause_playback(self):
        if self.preview_file is None: return
        pygame.mixer.music.pause()
        self.is_playing = False
        self.play_btn.config(text="▶")
        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None
        self.root_gui_ref.status_var.set("Paused.")


    def stop_playback(self):
        pygame.mixer.music.stop()
        self.is_playing = False
        self.play_btn.config(text="▶")
        self.current_position = 0
        self.playback_start_offset = 0 # Reset offset on stop
        self.seek_var.set(0)
        self.update_time_display()
        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None
        
        # Clean up the temporary preview file
        if self.preview_file and os.path.exists(self.preview_file):
            try:
                os.remove(self.preview_file)
            except OSError:
                pass # File might still be in use by pygame for a moment, best effort
        self.preview_file = None
        self.root_gui_ref.status_var.set("Playback stopped.")


    def on_seek(self, value):
        if not self.preview_file: # Use self.preview_file to check if a track is loaded
            return
            
        seek_pos_percent = float(value)
        if self.duration > 0:
            # pygame.mixer.music.set_pos uses seconds relative to the loaded track
            new_pos_seconds = (seek_pos_percent / 100) * self.duration
            pygame.mixer.music.set_pos(new_pos_seconds)
            self.current_position = new_pos_seconds # Update our internal position tracker
            self.playback_start_offset = new_pos_seconds # Set offset to the new seek position
            self.update_time_display()

            if self.is_playing: # If playing, restart playback from new position
                pygame.mixer.music.play(start=new_pos_seconds) # Restart from new position
                self.update_playback_position() # Restart the update loop

    def on_volume_change(self, value):
        volume = float(value) / 100
        pygame.mixer.music.set_volume(volume)
        # Update volume icon based on level
        if volume == 0:
            self.volume_icon.config(text="🔇")
        elif volume < 0.6:
            self.volume_icon.config(text="🔈")
        else:
            self.volume_icon.config(text="🔊")

    def update_playback_position(self):
        # pygame.mixer.music.get_pos() returns milliseconds since playback started for the *current* play() call
        # It resets when play() is called, so we need to track overall position.
        if self.is_playing:
            mixer_pos_ms = pygame.mixer.music.get_pos()
            if mixer_pos_ms != -1: # if music is actively playing
                current_mixer_time_s = (mixer_pos_ms / 1000.0) 
                
                # Calculate actual current position by adding the playback_start_offset
                self.current_position = self.playback_start_offset + current_mixer_time_s
                
                # Check if playback has finished for the loaded preview file
                if self.current_position >= self.duration - 0.1: # Allow for slight floating point inaccuracies
                    self.stop_playback()
                    return # Exit recursion

                # Update seek slider
                if self.duration > 0:
                    self.seek_var.set((self.current_position / self.duration) * 100)
                
                self.update_time_display()
            else:
                # If get_pos() returns -1, it might mean playback finished or stopped unexpectedly
                self.stop_playback()
                return

            self.after_id = self.after(self.update_interval, self.update_playback_position)
        # else: if not is_playing, the loop is already cancelled by pause/stop_playback

    def update_time_display(self):
        current_str = self.format_time(self.current_position)
        duration_str = self.format_time(self.duration)
        self.time_var.set(f"{current_str} / {duration_str}")

    def format_time(self, seconds):
        minutes = int(seconds // 60)
        seconds = int(seconds % 60)
        return f"{minutes:02d}:{seconds:02d}"

    def set_duration(self, duration):
        self.duration = duration
        self.current_position = 0
        self.seek_var.set(0)
        self.update_time_display()

    def reset(self):
        self.stop_playback()
        self.duration = 0
        self.current_position = 0
        self.playback_start_offset = 0 # Reset offset
        self.seek_var.set(0)
        self.update_time_display()

class ThumbnailCropper(tk.Toplevel):
    def __init__(self, parent, image_data: bytes, initial_crop_coords: Optional[Tuple[int, int, int, int]] = None):
        """
        :param parent: The parent Tkinter window.
        :param image_data: The byte data of the original image to crop.
        :param initial_crop_coords: Optional. A tuple (x1, y1, x2, y2) representing the
                                    initial crop rectangle in the ORIGINAL IMAGE's coordinate system.
                                    If None, the largest possible square centered on the displayed image will be used.
        """
        super().__init__(parent)
        self.title("Crop Thumbnail")
        self.parent = parent
        self.image_data = image_data
        self.initial_crop_coords_original = initial_crop_coords # Store original coords
        self.cropped_image_data = None  # To store the result of the crop
        self.cropped_original_coords = None # To store the coords of the result in original image system
        
        self.original_image = Image.open(BytesIO(image_data))
        self.display_image = None # Will store the scaled image for display
        self.photo_image = None # Tkinter PhotoImage reference

        # Canvas and image scaling properties
        self.canvas_width = 600
        self.canvas_height = 600
        self.scale_factor_x = 1 # Ratio of original_width / displayed_width
        self.scale_factor_y = 1 # Ratio of original_height / displayed_height
        self.image_offset_x = 0 # X offset of displayed image from canvas left edge
        self.image_offset_y = 0 # Y offset of displayed image from canvas top edge

        # Crop rectangle coordinates (on canvas) - these are updated during dragging/resizing
        self.crop_x1 = 0
        self.crop_y1 = 0
        self.crop_x2 = 0
        self.crop_y2 = 0
        self.rect_id = None
        self.handle_ids = []
        self.HANDLE_SIZE = 8 # Size of square handles

        # State variables for dragging/resizing
        self.dragging_mode = None # 'move' or 'resize_corner_NE', 'resize_corner_NW', etc.
        self.drag_start_x = None
        self.drag_start_y = None
        
        # Store current crop coordinates for calculations during drag
        self.initial_drag_crop_x1 = 0
        self.initial_drag_crop_y1 = 0
        self.initial_drag_crop_x2 = 0
        self.initial_drag_crop_y2 = 0
        
        self.canvas = tk.Canvas(self, width=self.canvas_width, height=self.canvas_height, bg="grey")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)
        self.canvas.bind("<Motion>", self.on_mouse_move) # For cursor changes
        self.canvas.bind("<Configure>", self.on_canvas_resize) # Handle window resize

        # Buttons
        button_frame = ttk.Frame(self)
        button_frame.pack(pady=10)
        
        ttk.Button(button_frame, text="Crop", command=self.perform_crop).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.cancel_crop).pack(side=tk.LEFT, padx=5)

        # Initial drawing after canvas is packed and has dimensions
        # This will call draw_initial_crop_rectangle via on_canvas_resize
        self.update_canvas_image()
        
        self.protocol("WM_DELETE_WINDOW", self.cancel_crop)
        self.transient(parent) # Make it modal
        self.grab_set() # Grab all events for this window
        # self.parent.wait_window(self) # Removed: This will be called by the parent (YouTubeAlbumSplitterGUI)

    def on_canvas_resize(self, event):
        # Update canvas dimensions when window is resized
        self.canvas_width = event.width
        self.canvas_height = event.height
        self.update_canvas_image()
        # Ensure the crop rectangle is redrawn to fit new scaling/offsets
        self.draw_initial_crop_rectangle(use_current_if_exists=True) # Use existing if already set

    def update_canvas_image(self):
        self.canvas.delete("all")
        
        img_width, img_height = self.original_image.size
        
        # Calculate scale to fit image within canvas while maintaining aspect ratio
        scale = min(self.canvas_width / img_width, self.canvas_height / img_height)
        new_width = int(img_width * scale)
        new_height = int(img_height * scale)
        
        self.display_image = self.original_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        self.photo_image = ImageTk.PhotoImage(self.display_image)
        
        # Store scale factor for converting canvas coordinates to original image coordinates
        self.scale_factor_x = img_width / new_width
        self.scale_factor_y = img_height / new_height

        # Store image offset on canvas
        self.image_offset_x = (self.canvas_width - new_width) / 2
        self.image_offset_y = (self.canvas_height - new_height) / 2
        
        self.canvas.create_image(self.image_offset_x, self.image_offset_y, image=self.photo_image, anchor=tk.NW)
        
        # This is important: after updating the image, we must recalculate
        # the initial crop rectangle based on the potentially new scale and offset.
        # This will be handled by on_canvas_resize calling draw_initial_crop_rectangle.


    def draw_initial_crop_rectangle(self, use_current_if_exists=False):
        """
        Draws the initial crop rectangle.
        If initial_crop_coords_original is set, it uses that.
        Otherwise, it calculates the largest possible square centered on the displayed image.
        
        :param use_current_if_exists: If True, and crop_x1/y1/x2/y2 are already set (e.g., from a drag),
                                      it converts them to original, then back to new canvas coords,
                                      effectively preserving the relative position/size.
        """
        
        # First, convert current canvas crop coords to original image coords if needed
        # This ensures that if the window was resized, the crop box scales appropriately.
        if use_current_if_exists and self.rect_id:
            # Get current crop box coords on canvas
            current_canvas_x1, current_canvas_y1, current_canvas_x2, current_canvas_y2 = self.canvas.coords(self.rect_id)

            # Convert to original image coordinates
            crop_original_x1 = int((current_canvas_x1 - self.image_offset_x) * self.scale_factor_x)
            crop_original_y1 = int((current_canvas_y1 - self.image_offset_y) * self.scale_factor_y)
            crop_original_x2 = int((current_canvas_x2 - self.image_offset_x) * self.scale_factor_x)
            crop_original_y2 = int((current_canvas_y2 - self.image_offset_y) * self.scale_factor_y)
            
            # Store these as the "initial" for this redraw
            self.initial_crop_coords_original = (crop_original_x1, crop_original_y1, crop_original_x2, crop_original_y2)


        if self.initial_crop_coords_original:
            # If initial crop coordinates were provided (from previous session or last crop)
            # Convert them from original image coordinates to current canvas coordinates
            x1_orig, y1_orig, x2_orig, y2_orig = self.initial_crop_coords_original
            
            self.crop_x1 = self.image_offset_x + (x1_orig / self.scale_factor_x)
            self.crop_y1 = self.image_offset_y + (y1_orig / self.scale_factor_y)
            self.crop_x2 = self.image_offset_x + (x2_orig / self.scale_factor_x)
            self.crop_y2 = self.image_offset_y + (y2_orig / self.scale_factor_y)

            # Clamp to canvas image boundaries
            self.crop_x1 = max(self.crop_x1, self.image_offset_x)
            self.crop_y1 = max(self.crop_y1, self.image_offset_y)
            self.crop_x2 = min(self.crop_x2, self.image_offset_x + self.display_image.width)
            self.crop_y2 = min(self.crop_y2, self.image_offset_y + self.display_image.height)

        else:
            # Calculate the largest possible square that fits within the displayed image area on the canvas
            # Ensure display_image is ready
            if not self.display_image:
                self.update_canvas_image() # This shouldn't be needed here if called from on_canvas_resize

            displayed_img_width = self.display_image.width
            displayed_img_height = self.display_image.height
            
            # Determine the side length of the largest possible square
            square_side = min(displayed_img_width, displayed_img_height)
            
            # Calculate the top-left corner of this square, centered within the displayed image
            offset_x_within_image = (displayed_img_width - square_side) / 2
            offset_y_within_image = (displayed_img_height - square_side) / 2

            # Convert these relative coordinates to canvas coordinates by adding the image_offset
            self.crop_x1 = self.image_offset_x + offset_x_within_image
            self.crop_y1 = self.image_offset_y + offset_y_within_image
            
            self.crop_x2 = self.crop_x1 + square_side
            self.crop_y2 = self.crop_y1 + square_side
        
        self.draw_crop_rectangle()


    def draw_crop_rectangle(self):
        # Clear previous rectangle and handles
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        for handle_id in self.handle_ids:
            self.canvas.delete(handle_id)
        self.handle_ids.clear()

        # Ensure x1<x2, y1<y2 for drawing (important for drag logic too)
        # These are the actual drawing coordinates for the rectangle
        draw_x1, draw_y1 = min(self.crop_x1, self.crop_x2), min(self.crop_y1, self.crop_y2)
        draw_x2, draw_y2 = max(self.crop_x1, self.crop_x2), max(self.crop_y1, self.crop_y2)

        self.rect_id = self.canvas.create_rectangle(
            draw_x1, draw_y1, draw_x2, draw_y2, outline="red", width=2, tags="crop_box"
        )
        
        # Draw handles
        self.handle_ids.append(self.canvas.create_rectangle(draw_x1 - self.HANDLE_SIZE/2, draw_y1 - self.HANDLE_SIZE/2, draw_x1 + self.HANDLE_SIZE/2, draw_y1 + self.HANDLE_SIZE/2, fill="blue", tags="handle_NW"))
        self.handle_ids.append(self.canvas.create_rectangle(draw_x2 - self.HANDLE_SIZE/2, draw_y1 - self.HANDLE_SIZE/2, draw_x2 + self.HANDLE_SIZE/2, draw_y1 + self.HANDLE_SIZE/2, fill="blue", tags="handle_NE"))
        self.handle_ids.append(self.canvas.create_rectangle(draw_x1 - self.HANDLE_SIZE/2, draw_y2 - self.HANDLE_SIZE/2, draw_x1 + self.HANDLE_SIZE/2, draw_y2 + self.HANDLE_SIZE/2, fill="blue", tags="handle_SW"))
        self.handle_ids.append(self.canvas.create_rectangle(draw_x2 - self.HANDLE_SIZE/2, draw_y2 - self.HANDLE_SIZE/2, draw_x2 + self.HANDLE_SIZE/2, draw_y2 + self.HANDLE_SIZE/2, fill="blue", tags="handle_SE"))

    def get_handle_type(self, x, y):
        # Use the potentially unordered self.crop_x/y for getting the actual current bounds
        current_x1, current_y1 = min(self.crop_x1, self.crop_x2), min(self.crop_y1, self.crop_y2)
        current_x2, current_y2 = max(self.crop_x1, self.crop_x2), max(self.crop_y1, self.crop_y2)

        handle_tolerance = self.HANDLE_SIZE 
        
        if (current_x1 - handle_tolerance <= x <= current_x1 + handle_tolerance) and \
           (current_y1 - handle_tolerance <= y <= current_y1 + handle_tolerance):
            return 'resize_corner_NW'
        elif (current_x2 - handle_tolerance <= x <= current_x2 + handle_tolerance) and \
             (current_y1 - handle_tolerance <= y <= current_y1 + handle_tolerance):
            return 'resize_corner_NE'
        elif (current_x1 - handle_tolerance <= x <= current_x1 + handle_tolerance) and \
             (current_y2 - handle_tolerance <= y <= current_y2 + handle_tolerance):
            return 'resize_corner_SW'
        elif (current_x2 - handle_tolerance <= x <= current_x2 + handle_tolerance) and \
             (current_y2 - handle_tolerance <= y <= current_y2 + handle_tolerance):
            return 'resize_corner_SE'
        elif (current_x1 <= x <= current_x2) and (current_y1 <= y <= current_y2):
            return 'move' # Inside the crop box
        return None

    def on_mouse_move(self, event):
        mode = self.get_handle_type(event.x, event.y)
        if mode == 'move':
            self.canvas.config(cursor="fleur")
        elif mode and 'resize' in mode:
            # Change cursor based on corner for diagonal resize
            if mode in ['resize_corner_NW', 'resize_corner_SE']:
                self.canvas.config(cursor="sizing NW_SE")
            elif mode in ['resize_corner_NE', 'resize_corner_SW']:
                self.canvas.config(cursor="sizing NE_SW")
        else:
            self.canvas.config(cursor="arrow") # Default cursor

    def on_button_press(self, event):
        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.dragging_mode = self.get_handle_type(event.x, event.y)

        # Store current crop coordinates for calculations
        self.initial_drag_crop_x1 = self.crop_x1
        self.initial_drag_crop_y1 = self.crop_y1
        self.initial_drag_crop_x2 = self.crop_x2
        self.initial_drag_crop_y2 = self.crop_y2

    def on_mouse_drag(self, event):
        dx = event.x - self.drag_start_x
        dy = event.y - self.drag_start_y

        # Define image boundaries on canvas
        img_x1 = self.image_offset_x
        img_y1 = self.image_offset_y
        img_x2 = self.image_offset_x + self.display_image.width
        img_y2 = self.image_offset_y + self.display_image.height

        # Ensure current crop coordinates from initial_drag are ordered for calculations
        current_x1_ordered, current_y1_ordered = min(self.initial_drag_crop_x1, self.initial_drag_crop_x2), min(self.initial_drag_crop_y1, self.initial_drag_crop_y2)
        current_x2_ordered, current_y2_ordered = max(self.initial_drag_crop_x1, self.initial_drag_crop_x2), max(self.initial_drag_crop_y1, self.initial_drag_crop_y2)
        
        current_width = current_x2_ordered - current_x1_ordered
        current_height = current_y2_ordered - current_y1_ordered
        
        min_size = self.HANDLE_SIZE * 2 # Minimum side length for the crop box

        if self.dragging_mode == 'move':
            new_x1 = self.initial_drag_crop_x1 + dx
            new_y1 = self.initial_drag_crop_y1 + dy
            new_x2 = self.initial_drag_crop_x2 + dx
            new_y2 = self.initial_drag_crop_y2 + dy

            # Clamp movement to image boundaries
            width = self.initial_drag_crop_x2 - self.initial_drag_crop_x1
            height = self.initial_drag_crop_y2 - self.initial_drag_crop_y1

            if new_x1 < img_x1:
                new_x1 = img_x1
                new_x2 = new_x1 + width
            elif new_x2 > img_x2:
                new_x2 = img_x2
                new_x1 = new_x2 - width

            if new_y1 < img_y1:
                new_y1 = img_y1
                new_y2 = new_y1 + height
            elif new_y2 > img_y2:
                new_y2 = img_y2
                new_y1 = new_y2 - height

            self.crop_x1, self.crop_y1, self.crop_x2, self.crop_y2 = new_x1, new_y1, new_x2, new_y2
            self.draw_crop_rectangle()

        elif 'resize' in self.dragging_mode:
            # The goal is to maintain a square aspect ratio while resizing
            
            # Calculate proposed new dimensions based on drag
            if self.dragging_mode == 'resize_corner_NW':
                proposed_x1 = event.x
                proposed_y1 = event.y
                
                # Calculate new side based on distance from opposite corner (current_x2_ordered, current_y2_ordered)
                candidate_width = current_x2_ordered - proposed_x1
                candidate_height = current_y2_ordered - proposed_y1
                
                new_side = min(candidate_width, candidate_height)
                new_side = max(min_size, new_side) # Ensure min size

                # Calculate new (x1, y1) based on new_side and opposite corner
                new_x1 = current_x2_ordered - new_side
                new_y1 = current_y2_ordered - new_side

                # Clamp proposed x1, y1 to image bounds
                new_x1 = max(img_x1, new_x1)
                new_y1 = max(img_y1, new_y1)

                # Adjust new_side if clamping occurred, maintaining square aspect
                if new_x1 == img_x1:
                    new_side = current_x2_ordered - img_x1
                if new_y1 == img_y1:
                    new_side = current_y2_ordered - img_y1
                new_side = min(new_side, current_x2_ordered - img_x1, current_y2_ordered - img_y1)
                new_side = max(min_size, new_side)

                self.crop_x1 = current_x2_ordered - new_side
                self.crop_y1 = current_y2_ordered - new_side
                self.crop_x2 = current_x2_ordered
                self.crop_y2 = current_y2_ordered

            elif self.dragging_mode == 'resize_corner_NE':
                proposed_x2 = event.x
                proposed_y1 = event.y

                candidate_width = proposed_x2 - current_x1_ordered
                candidate_height = current_y2_ordered - proposed_y1
                
                new_side = min(candidate_width, candidate_height)
                new_side = max(min_size, new_side)

                # Clamp proposed x2, y1 to image bounds
                new_x2 = min(img_x2, proposed_x2)
                new_y1 = max(img_y1, proposed_y1)

                # Adjust new_side if clamping occurred
                if new_x2 == img_x2:
                    new_side = img_x2 - current_x1_ordered
                if new_y1 == img_y1:
                    new_side = current_y2_ordered - img_y1
                new_side = min(new_side, img_x2 - current_x1_ordered, current_y2_ordered - img_y1)
                new_side = max(min_size, new_side)

                self.crop_x1 = current_x1_ordered
                self.crop_y1 = current_y2_ordered - new_side
                self.crop_x2 = current_x1_ordered + new_side
                self.crop_y2 = current_y2_ordered

            elif self.dragging_mode == 'resize_corner_SW':
                proposed_x1 = event.x
                proposed_y2 = event.y

                candidate_width = current_x2_ordered - proposed_x1
                candidate_height = proposed_y2 - current_y1_ordered

                new_side = min(candidate_width, candidate_height)
                new_side = max(min_size, new_side)

                # Clamp proposed x1, y2 to image bounds
                new_x1 = max(img_x1, proposed_x1)
                new_y2 = min(img_y2, proposed_y2)

                # Adjust new_side if clamping occurred
                if new_x1 == img_x1:
                    new_side = current_x2_ordered - img_x1
                if new_y2 == img_y2:
                    new_side = img_y2 - current_y1_ordered
                new_side = min(new_side, current_x2_ordered - img_x1, img_y2 - current_y1_ordered)
                new_side = max(min_size, new_side)

                self.crop_x1 = current_x2_ordered - new_side
                self.crop_y1 = current_y1_ordered
                self.crop_x2 = current_x2_ordered
                self.crop_y2 = current_y1_ordered + new_side

            elif self.dragging_mode == 'resize_corner_SE':
                proposed_x2 = event.x
                proposed_y2 = event.y

                candidate_width = proposed_x2 - current_x1_ordered
                candidate_height = proposed_y2 - current_y1_ordered

                new_side = min(candidate_width, candidate_height)
                new_side = max(min_size, new_side)

                # Clamp proposed x2, y2 to image bounds
                new_x2 = min(img_x2, proposed_x2)
                new_y2 = min(img_y2, proposed_y2)

                # Adjust new_side if clamping occurred
                if new_x2 == img_x2:
                    new_side = img_x2 - current_x1_ordered
                if new_y2 == img_y2:
                    new_side = img_y2 - current_y1_ordered
                new_side = min(new_side, img_x2 - current_x1_ordered, img_y2 - current_y1_ordered)
                new_side = max(min_size, new_side)

                self.crop_x1 = current_x1_ordered
                self.crop_y1 = current_y1_ordered
                self.crop_x2 = current_x1_ordered + new_side
                self.crop_y2 = current_y1_ordered + new_side
            
            # After calculating the new_side and updating crop_x/y based on the new side,
            # we need to ensure the entire box remains within the image.
            # This handles cases where the calculated new_side might cause the box
            # to exceed bounds if the initial corner was already near the edge.

            # Ensure the top-left is not less than image bounds
            self.crop_x1 = max(img_x1, self.crop_x1)
            self.crop_y1 = max(img_y1, self.crop_y1)

            # Ensure the bottom-right is not greater than image bounds
            # If the right edge is out, pull it back and adjust left
            if self.crop_x2 > img_x2:
                self.crop_x2 = img_x2
                self.crop_x1 = self.crop_x2 - new_side # Maintain size
            # If the bottom edge is out, pull it back and adjust top
            if self.crop_y2 > img_y2:
                self.crop_y2 = img_y2
                self.crop_y1 = self.crop_y2 - new_side # Maintain size
            
            # Re-clamp top-left after potential bottom-right adjustment
            self.crop_x1 = max(img_x1, self.crop_x1)
            self.crop_y1 = max(img_y1, self.crop_y1)
            
            self.draw_crop_rectangle()

    def on_button_release(self, event):
        self.dragging_mode = None
        self.drag_start_x = None
        self.drag_start_y = None
        self.canvas.config(cursor="arrow") # Reset cursor

    def perform_crop(self):
        # Get the current coordinates of the rectangle object (these are already ordered by draw_crop_rectangle)
        x1_canvas, y1_canvas, x2_canvas, y2_canvas = self.canvas.coords(self.rect_id)

        # Adjust for image offset on canvas
        crop_original_x1 = int((x1_canvas - self.image_offset_x) * self.scale_factor_x)
        crop_original_y1 = int((y1_canvas - self.image_offset_y) * self.scale_factor_y)
        crop_original_x2 = int((x2_canvas - self.image_offset_x) * self.scale_factor_x)
        crop_original_y2 = int((y2_canvas - self.image_offset_y) * self.scale_factor_y)

        # Ensure coordinates are within original image bounds (should already be due to clamping)
        crop_original_x1 = max(0, crop_original_x1)
        crop_original_y1 = max(0, crop_original_y1)
        crop_original_x2 = min(self.original_image.width, crop_original_x2)
        crop_original_y2 = min(self.original_image.height, crop_original_y2)

        cropped_image = self.original_image.crop((crop_original_x1, crop_original_y1, crop_original_x2, crop_original_y2))
        
        # Convert to bytes
        byte_arr = BytesIO()
        cropped_image.save(byte_arr, format='JPEG') # Assuming JPEG for thumbnails
        self.cropped_image_data = byte_arr.getvalue()
        
        # Store the original image coordinates of the *final* crop for next time
        self.cropped_original_coords = (crop_original_x1, crop_original_y1, crop_original_x2, crop_original_y2)

        self.destroy()

    def cancel_crop(self):
        self.cropped_image_data = None
        self.cropped_original_coords = None # Indicate no crop was performed/saved
        self.destroy()

class YouTubeAlbumSplitterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Album Splitter")
        self.root.geometry("1000x750")  # Slightly taller for player controls
        
        self.splitter = YouTubeAlbumSplitter(self) # Pass self reference
        # self.preview = AudioPreview() # AudioPreview is no longer a separate instance here
        self.tracks = []
        
        # Pygame mixer initialization is now handled by AudioPlayerControl internally
        # pygame.mixer.init()
        
        self.thumbnail_label = None
        self.thumbnail_data = None # Store the initially fetched thumbnail data
        self.cropped_thumbnail_data = None # Store the final (potentially cropped) thumbnail data
        self.last_cropped_original_coords = None # Store the original image coordinates of the last crop
        
        self.setup_ui()

    def setup_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # URL input
        url_frame = ttk.LabelFrame(main_frame, text="YouTube URL", padding="5")
        url_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))

        self.url_var = tk.StringVar()
        url_entry = ttk.Entry(url_frame, textvariable=self.url_var, width=60)
        url_entry.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        url_entry.focus_set()

        # Bind Enter key to the button click
        url_entry.bind("<Return>", lambda event: self.download_btn.invoke())

        self.download_btn = ttk.Button(url_frame, text="Download & Analyse", command=self.download_and_analyse)
        self.download_btn.grid(row=0, column=1)

        url_frame.columnconfigure(0, weight=1)

        # Progress bar
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(main_frame, textvariable=self.status_var)
        status_label.grid(row=2, column=0, columnspan=2, pady=(0, 10))

        # Artist Name input
        artist_frame = ttk.LabelFrame(main_frame, text="Album Artist (Optional)", padding="5")
        artist_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))

        self.artist_var = tk.StringVar(value="") # Initialize with empty string
        artist_entry = ttk.Entry(artist_frame, textvariable=self.artist_var, width=60)
        artist_entry.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        artist_frame.columnconfigure(0, weight=1)

        # Thumbnail display area
        thumbnail_display_frame = ttk.LabelFrame(main_frame, text="Current Thumbnail", padding="5")
        thumbnail_display_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.thumbnail_label = ttk.Label(thumbnail_display_frame)
        self.thumbnail_label.pack(side=tk.LEFT, padx=5, pady=5)
        
        # Button to re-crop/change thumbnail
        ttk.Button(thumbnail_display_frame, text="Change/Crop Thumbnail", command=self.change_crop_thumbnail).pack(side=tk.LEFT, padx=10)


        # Tracks frame (shifted to row 5)
        tracks_frame = ttk.LabelFrame(main_frame, text="Tracks", padding="5")
        tracks_frame.grid(row=5, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        
        # Treeview for tracks
        # Updated columns to include 'Artist'
        columns = ('Title', 'Artist', 'Start', 'End', 'Duration')
        self.tracks_tree = ttk.Treeview(tracks_frame, columns=columns, show='headings', height=15)
        
        for col in columns:
            self.tracks_tree.heading(col, text=col)
            # Adjust column widths as needed
            if col == 'Title':
                self.tracks_tree.column(col, width=250)
            elif col == 'Artist':
                self.tracks_tree.column(col, width=150)
            else:
                self.tracks_tree.column(col, width=100)
        
        # Scrollbars for treeview
        v_scrollbar = ttk.Scrollbar(tracks_frame, orient=tk.VERTICAL, command=self.tracks_tree.yview)
        h_scrollbar = ttk.Scrollbar(tracks_frame, orient=tk.HORIZONTAL, command=self.tracks_tree.xview)
        self.tracks_tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        self.tracks_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        v_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        h_scrollbar.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        tracks_frame.columnconfigure(0, weight=1)
        tracks_frame.rowconfigure(0, weight=1)
        
        # Buttons frame (for add/edit/delete)
        buttons_frame = ttk.Frame(tracks_frame)
        buttons_frame.grid(row=2, column=0, columnspan=2, pady=(10, 0))
        
        ttk.Button(buttons_frame, text="Add Track", command=self.add_track).grid(row=0, column=0, padx=(0, 5))
        ttk.Button(buttons_frame, text="Edit Track", command=self.edit_track).grid(row=0, column=1, padx=5)
        ttk.Button(buttons_frame, text="Delete Track", command=self.delete_track).grid(row=0, column=2, padx=5)
        
        # Output frame (shifted to row 6)
        output_frame = ttk.LabelFrame(main_frame, text="Output", padding="5")
        output_frame.grid(row=6, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.output_var = tk.StringVar(value="output")
        ttk.Label(output_frame, text="Output Directory:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(output_frame, textvariable=self.output_var, width=40).grid(row=0, column=1, padx=5, sticky=(tk.W, tk.E))
        ttk.Button(output_frame, text="Browse", command=self.browse_output).grid(row=0, column=2)
        
        output_frame.columnconfigure(1, weight=1)
        
        # Process button (shifted to row 7)
        self.process_btn = ttk.Button(main_frame, text="Split Audio", command=self.split_audio, state='disabled')
        self.process_btn.grid(row=7, column=0, columnspan=2, pady=10)
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(5, weight=1) # Tracks frame is now row 5

        # AudioPlayerControl is now instantiated with a reference to self (YouTubeAlbumSplitterGUI)
        self.player_controls = AudioPlayerControl(main_frame, self)
        self.player_controls.grid(row=8, column=0, columnspan=2, pady=(10, 0), sticky=(tk.W, tk.E))

        # New: Bind track selection to the AudioPlayerControl
        self.tracks_tree.bind('<<TreeviewSelect>>', self.on_track_selection)

    def download_and_analyse(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Error", "Please enter a YouTube URL")
            return
        
        # Clear previous thumbnail if any
        self.thumbnail_data = None
        self.cropped_thumbnail_data = None
        self.last_cropped_original_coords = None # Reset crop history for new video
        self.thumbnail_label.config(image='')
        self.thumbnail_label.image = None

        def download_thread():
            try:
                self.root.after(0, lambda: self.progress.start())
                self.root.after(0, lambda: self.download_btn.config(state='disabled'))
                
                def update_status(status):
                    self.root.after(0, lambda: self.status_var.set(status))
                
                update_status("Downloading audio...")
                self.splitter.download_audio(url, update_status)
                
                update_status("Analyzing description...")
                description = self.splitter.video_info.get('description', '')
                auto_tracks = self.splitter.extract_timestamps_from_description(description)
                
                self.root.after(0, lambda: self.load_tracks(auto_tracks))
                self.root.after(0, lambda: self.progress.stop())
                self.root.after(0, lambda: self.download_btn.config(state='normal'))
                self.root.after(0, lambda: self.process_btn.config(state='normal'))
                update_status(f"Found {len(auto_tracks)} tracks")
                self.root.after(0, self.fetch_thumbnail) # Call to fetch thumbnail

            except Exception as e:
                self.root.after(0, lambda: self.progress.stop())
                self.root.after(0, lambda: self.download_btn.config(state='normal'))
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
                self.root.after(0, lambda: self.status_var.set("Error"))
        
        threading.Thread(target=download_thread, daemon=True).start()
    
    def load_tracks(self, tracks):
        self.tracks = tracks
        self.refresh_tracks_view()
    
    def refresh_tracks_view(self):
        # Clear existing items
        for item in self.tracks_tree.get_children():
            self.tracks_tree.delete(item)
        
        # Add tracks
        for i, track in enumerate(self.tracks):
            duration = ""
            if track.end_time:
                start_sec = self.splitter.parse_timestamp(track.start_time)
                end_sec = self.splitter.parse_timestamp(track.end_time)
                duration_sec = end_sec - start_sec
                duration = self.splitter.seconds_to_timestamp(duration_sec)
            
            self.tracks_tree.insert('', 'end', values=(
                track.title,
                track.artist, # New: Display artist
                track.start_time,
                track.end_time or "End",
                duration
            ))
    
    def add_track(self):
        # Pass the current album artist as initial_artist for new tracks
        dialog = TrackDialog(self.root, "Add Track", initial_artist=self.artist_var.get())
        if dialog.result:
            title, start_time, end_time, artist = dialog.result # New: Unpack artist
            try:
                self.splitter.parse_timestamp(start_time)
                if end_time:
                    self.splitter.parse_timestamp(end_time)
                
                self.tracks.append(Track(title, start_time, end_time, artist)) # New: Pass artist
                self.tracks.sort(key=lambda t: self.splitter.parse_timestamp(t.start_time))
                self.refresh_tracks_view()
            except ValueError as e:
                messagebox.showerror("Error", str(e))
    
    def edit_track(self):
        selection = self.tracks_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a track to edit")
            return
        
        item = selection[0]
        index = self.tracks_tree.index(item)
        track = self.tracks[index]
        
        # Pass existing track's artist to the dialog
        dialog = TrackDialog(self.root, "Edit Track", track.title, track.start_time, track.end_time, track.artist)
        if dialog.result:
            title, start_time, end_time, artist = dialog.result # New: Unpack artist
            try:
                self.splitter.parse_timestamp(start_time)
                if end_time:
                    self.splitter.parse_timestamp(end_time)
                
                track.title = title
                track.start_time = start_time
                track.end_time = end_time
                track.artist = artist # New: Update artist
                
                self.tracks.sort(key=lambda t: self.splitter.parse_timestamp(t.start_time))
                self.refresh_tracks_view()
            except ValueError as e:
                messagebox.showerror("Error", str(e))
    
    def delete_track(self):
        selection = self.tracks_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a track to delete")
            return
        
        if messagebox.askyesno("Confirm", "Delete selected track?"):
            item = selection[0]
            index = self.tracks_tree.index(item)
            del self.tracks[index]
            self.refresh_tracks_view()
    
    def on_track_selection(self, event):
        """Called when a track is selected in the Treeview."""
        selection = self.tracks_tree.selection()
        if selection:
            item = selection[0]
            index = self.tracks_tree.index(item)
            track = self.tracks[index]
            self.player_controls.load_track_for_playback(track)
            
    # The original preview_track and stop_preview methods are no longer needed
    # as their functionality is replaced by on_track_selection and player_controls.
    # def preview_track(self):
    #     pass # Removed/Replaced

    # def stop_preview(self):
    #     pass # Removed/Replaced
    
    def fetch_thumbnail(self):
        if not self.splitter.video_info:
            return

        try:
            # Get the highest resolution thumbnail
            thumbnail_url = self.splitter.video_info.get('thumbnail')
            if not thumbnail_url:
                return

            # Download thumbnail
            response = requests.get(thumbnail_url, stream=True)
            response.raise_for_status()
            self.thumbnail_data = response.content # Store the original downloaded thumbnail
            self.cropped_thumbnail_data = self.thumbnail_data # Initially, cropped is same as original
            
            # Show thumbnail and ask if user wants to crop
            self.show_thumbnail_with_crop_option()
            
        except Exception as e:
            print(f"Error loading thumbnail: {e}")
            messagebox.showwarning("Thumbnail Error", f"Could not fetch thumbnail: {e}")
    
    def show_thumbnail_with_crop_option(self):
        """Display thumbnail and let user choose to crop"""
        # Create a dialog with the thumbnail
        crop_dialog = tk.Toplevel(self.root)
        crop_dialog.title("Thumbnail Options")
        
        # Display thumbnail
        img = Image.open(BytesIO(self.thumbnail_data))
        img.thumbnail((200, 200)) # Smaller for this dialog
        photo = ImageTk.PhotoImage(img)
        
        label = ttk.Label(crop_dialog, image=photo)
        label.image = photo  # Keep reference
        label.pack(pady=10)
        
        # Ask user if they want to crop
        ttk.Label(crop_dialog, text="Would you like to crop this thumbnail?").pack()
        
        button_frame = ttk.Frame(crop_dialog)
        button_frame.pack(pady=10)
        
        ttk.Button(button_frame, text="Crop", command=lambda: self.start_cropping(dialog=crop_dialog, force_new_crop=True)).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Use As Is", command=lambda: self.use_thumbnail_as_is(crop_dialog)).pack(side=tk.LEFT, padx=5)
        
        crop_dialog.transient(self.root)
        crop_dialog.grab_set()
        self.root.wait_window(crop_dialog) # Make sure this dialog blocks until closed

    def start_cropping(self, dialog=None, force_new_crop=False):
        """
        Starts the ThumbnailCropper window.
        :param dialog: The current dialog (e.g., show_thumbnail_with_crop_option) to destroy.
        :param force_new_crop: If True, it will ignore last_cropped_original_coords and force a default initial crop.
        """
        if dialog:
            dialog.destroy()
        
        initial_coords_to_pass = None
        if not force_new_crop and self.last_cropped_original_coords:
            initial_coords_to_pass = self.last_cropped_original_coords

        cropper = ThumbnailCropper(self.root, self.thumbnail_data, initial_crop_coords=initial_coords_to_pass)
        # Check if the cropper window is still alive before waiting on it
        # This addresses the "bad window path name" error if the cropper dialog is closed prematurely by the user.
        if cropper.winfo_exists():
            self.root.wait_window(cropper) # Wait for cropper window to close
        
        # The result attributes are set on the cropper instance itself before it's destroyed.
        # We need to check if the attributes exist before trying to access them,
        # in case the window was closed without explicit crop/cancel buttons being pressed.
        if hasattr(cropper, 'cropped_image_data') and cropper.cropped_image_data is not None:
            self.cropped_thumbnail_data = cropper.cropped_image_data
            self.last_cropped_original_coords = cropper.cropped_original_coords # Store the new original coords
            self.display_final_thumbnail()
        else:
            # If cropping was canceled or failed, revert to the *last known* good thumbnail data
            # If it's the very first time and canceled, it will be the original full thumbnail
            if self.last_cropped_original_coords is None:
                # If no crop was ever set, and cancel, then use the original full thumbnail
                self.cropped_thumbnail_data = self.thumbnail_data
            # Else, self.cropped_thumbnail_data already holds the previous cropped state or original.
            self.display_final_thumbnail()
    
    def use_thumbnail_as_is(self, dialog):
        dialog.destroy()
        
        # Open the original thumbnail data
        original_img = Image.open(BytesIO(self.thumbnail_data))
        
        # Calculate the largest possible square that fits within the original image
        img_width, img_height = original_img.size
        square_side = min(img_width, img_height)
        
        left = (img_width - square_side) // 2
        top = (img_height - square_side) // 2
        right = left + square_side
        bottom = top + square_side
        
        # Crop the original image to this square
        cropped_square_img = original_img.crop((left, top, right, bottom))
        
        # Convert the cropped square image to bytes
        byte_arr = BytesIO()
        cropped_square_img.save(byte_arr, format='JPEG') # Assuming JPEG for thumbnails
        self.cropped_thumbnail_data = byte_arr.getvalue()
        
        # Store the original image coordinates of this new square crop
        self.last_cropped_original_coords = (left, top, right, bottom)
        self.display_final_thumbnail()
    
    def display_final_thumbnail(self):
        """Display the final (cropped or original) thumbnail on the main UI."""
        if self.cropped_thumbnail_data:
            try:
                img = Image.open(BytesIO(self.cropped_thumbnail_data))
                img.thumbnail((150, 150))  # Display size on main UI
                
                photo = ImageTk.PhotoImage(img)
                
                self.thumbnail_label.config(image=photo)
                self.thumbnail_label.image = photo  # Keep reference
                
            except Exception as e:
                print(f"Error displaying final thumbnail: {e}")
                self.thumbnail_label.config(image='')
                self.thumbnail_label.image = None
        else:
            self.thumbnail_label.config(image='')
            self.thumbnail_label.image = None

    def browse_output(self):
        directory = filedialog.askdirectory()
        if directory:
            self.output_var.set(directory)

    def split_audio(self):
        if not self.tracks:
            messagebox.showwarning("Warning", "No tracks to split.")
            return
        
        if not self.splitter.audio_file:
            messagebox.showerror("Error", "No audio file downloaded.")
            return

        output_dir = self.output_var.get().strip()
        if not output_dir:
            messagebox.showerror("Error", "Please select an output directory.")
            return
        
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)

        def split_thread():
            try:
                self.root.after(0, lambda: self.progress.start())
                self.root.after(0, lambda: self.process_btn.config(state='disabled'))
                self.root.after(0, lambda: self.download_btn.config(state='disabled'))
                
                def update_status(status):
                    self.root.after(0, lambda: self.status_var.set(status))
                
                # Pass the cropped_thumbnail_data to the splitter
                self.splitter.split_audio(self.tracks, output_dir, self.cropped_thumbnail_data, update_status)
                
                self.root.after(0, lambda: self.progress.stop())
                self.root.after(0, lambda: self.process_btn.config(state='normal'))
                self.root.after(0, lambda: self.download_btn.config(state='normal'))
                self.root.after(0, lambda: self.status_var.set(f"Successfully split {len(self.tracks)} tracks."))
                messagebox.showinfo("Success", f"Successfully split {len(self.tracks)} tracks to {output_dir}")

            except Exception as e:
                self.root.after(0, lambda: self.progress.stop())
                self.root.after(0, lambda: self.process_btn.config(state='normal'))
                self.root.after(0, lambda: self.download_btn.config(state='normal'))
                messagebox.showerror("Error", f"Splitting failed: {e}")
                self.root.after(0, lambda: self.status_var.set("Splitting failed"))
            finally:
                self.splitter.cleanup()
        
        threading.Thread(target=split_thread, daemon=True).start()

    def change_crop_thumbnail(self):
        """Allows user to re-crop or re-select the thumbnail."""
        if not self.thumbnail_data:
            messagebox.showwarning("No Thumbnail", "Please download a video first to get a thumbnail.")
            return
        
        # Call start_cropping, passing the last known cropped coordinates if available.
        # This will ensure the cropper starts with the previous bounds.
        self.start_cropping(dialog=None, force_new_crop=False)


    def on_closing(self):
        if messagebox.askokcancel("Quit", "Do you want to quit?"):
            self.player_controls.stop_playback() # Ensure pygame mixer is stopped and temp files are cleaned
            self.splitter.cleanup()
            self.root.destroy()

class TrackDialog(tk.Toplevel):
    # New: Added initial_artist parameter
    def __init__(self, parent, title, initial_title="", initial_start="", initial_end="", initial_artist=""):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.grab_set()
        self.result = None
        
        self.initial_title = initial_title
        self.initial_start = initial_start
        self.initial_end = initial_end
        self.initial_artist = initial_artist # New: Store initial artist
        
        self.setup_ui()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.parent.wait_window(self)
        
    def setup_ui(self):
        form_frame = ttk.Frame(self, padding="10")
        form_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Label(form_frame, text="Title:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.title_var = tk.StringVar(value=self.initial_title)
        ttk.Entry(form_frame, textvariable=self.title_var, width=40).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5)
        
        # New: Artist field
        ttk.Label(form_frame, text="Artist (optional):").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.artist_var = tk.StringVar(value=self.initial_artist)
        ttk.Entry(form_frame, textvariable=self.artist_var, width=40).grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5)

        ttk.Label(form_frame, text="Start Time (mm:ss or hh:mm:ss):").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.start_var = tk.StringVar(value=self.initial_start)
        ttk.Entry(form_frame, textvariable=self.start_var, width=20).grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Label(form_frame, text="End Time (optional):").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.end_var = tk.StringVar(value=self.initial_end)
        ttk.Entry(form_frame, textvariable=self.end_var, width=20).grid(row=3, column=1, sticky=(tk.W, tk.E), pady=5)
        
        button_frame = ttk.Frame(self, padding="10")
        button_frame.pack(fill=tk.X)
        
        ttk.Button(button_frame, text="OK", command=self.ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.cancel).pack(side=tk.LEFT, padx=5)
        
        form_frame.columnconfigure(1, weight=1)

    def ok(self):
        title = self.title_var.get().strip()
        artist = self.artist_var.get().strip() # New: Get artist
        start_time = self.start_var.get().strip()
        end_time = self.end_var.get().strip()
        
        if not title or not start_time:
            messagebox.showwarning("Input Error", "Title and Start Time are required.")
            return
        
        self.result = (title, start_time, end_time if end_time else None, artist if artist else None)
        self.destroy()

    def cancel(self):
        self.result = None
        self.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = YouTubeAlbumSplitterGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()