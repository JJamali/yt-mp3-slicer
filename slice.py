#!/usr/bin/env python3
"""
YouTube Album Splitter GUI
A GUI tool to download YouTube videos and split them into individual MP3 tracks.
"""

import os
import re
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from typing import List, Optional
import threading
import tempfile
import pygame
import yt_dlp

class Track:
    def __init__(self, title: str, start_time: str, end_time: str = None):
        self.title = title.strip()
        self.start_time = start_time
        self.end_time = end_time
    
    def __str__(self):
        end = f" - {self.end_time}" if self.end_time else ""
        return f"{self.title}: {self.start_time}{end}"

class YouTubeAlbumSplitter:
    def __init__(self):
        self.video_info = None
        self.audio_file = None
        self.tracks = []
    
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
            return f"{minutes}:{secs:02d}"
    
    def extract_timestamps_from_description(self, description: str) -> List[Track]:
        """Extract track information from video description"""
        tracks = []
        
        patterns = [
            r'(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—]\s*(.+?)\s*[-–—]\s*(\d{1,2}:\d{2}(?::\d{2})?)',
            r'(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—]\s*(.+)',
            r'(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)',
            r'(.+?)\s*[-–—]\s*(\d{1,2}:\d{2}(?::\d{2})?)',
            r'(.+?):\s*(\d{1,2}:\d{2}(?::\d{2})?)',
        ]
        
        lines = description.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            if any(skip_word in line.lower() for skip_word in ['tracklist', 'track list', 'playlist', 'setlist']):
                continue
                
            for pattern in patterns:
                matches = re.findall(pattern, line, re.UNICODE)
                for match in matches:
                    if len(match) == 3:
                        start_time, title, end_time = match
                        title = title.strip()
                    elif pattern.startswith(r'(\d'):
                        timestamp, title = match[:2]
                        if ' - ' in line or ' – ' in line:
                            continue
                        start_time = timestamp
                        end_time = None
                    else:
                        title, timestamp = match[:2]
                        start_time = timestamp
                        end_time = None
                    
                    title = re.sub(r'^[\d\.\)\]\-–—\s]+', '', title).strip()
                    title = re.sub(r'[\[\(].*?[\]\)]', '', title).strip()
                    title = title.strip('「」『』""''')
                    title = title.strip()
                    
                    if not title or title.isdigit():
                        continue
                    
                    try:
                        self.parse_timestamp(start_time)
                        if end_time:
                            self.parse_timestamp(end_time)
                        
                        if not any(t.title == title and t.start_time == start_time for t in tracks):
                            tracks.append(Track(title, start_time, end_time))
                        break
                    except ValueError:
                        continue
        
        tracks.sort(key=lambda t: self.parse_timestamp(t.start_time))
        
        for i in range(len(tracks)):
            if not tracks[i].end_time and i < len(tracks) - 1:
                tracks[i].end_time = tracks[i + 1].start_time
        
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
        
    def split_audio(self, tracks: List[Track], output_dir: str = "output", progress_callback=None):
        """Split audio file into individual tracks"""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        for i, track in enumerate(tracks, 1):
            if progress_callback:
                progress_callback(f"Processing track {i}/{len(tracks)}: {track.title}")
            
            start_seconds = self.parse_timestamp(track.start_time)
            
            safe_title = re.sub(r'[<>:"/\\|?*]', '', track.title)
            safe_title = safe_title[:100]
            output_file = os.path.join(output_dir, f"{i:02d}. {safe_title}.mp3")
            
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
                subprocess.run(cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                raise Exception(f"Failed to create {track.title}: {e.stderr.decode()}")
    
    def cleanup(self):
        """Remove temporary files"""
        if self.audio_file and os.path.exists(self.audio_file):
            os.remove(self.audio_file)

class AudioPreview:
    def __init__(self):
        pygame.mixer.init()
        self.current_preview = None
        self.is_playing = False
    
    def create_preview(self, audio_file: str, start_time: int, duration: int = 30) -> str:
        """Create a preview file for the given time range"""
        preview_file = tempfile.mktemp(suffix='.mp3')
        
        cmd = [
            'ffmpeg', '-i', audio_file,
            '-ss', str(start_time),
            '-t', str(duration),
            '-acodec', 'mp3',
            '-ab', '128k',
            '-y', preview_file
        ]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return preview_file
        except subprocess.CalledProcessError:
            return None
    
    def play_preview(self, preview_file: str):
        """Play the preview file"""
        try:
            pygame.mixer.music.load(preview_file)
            pygame.mixer.music.play()
            self.is_playing = True
        except pygame.error:
            pass
    
    def stop_preview(self):
        """Stop the current preview"""
        pygame.mixer.music.stop()
        self.is_playing = False
        if self.current_preview and os.path.exists(self.current_preview):
            try:
                os.remove(self.current_preview)
            except OSError:
                pass
        self.current_preview = None

class YouTubeAlbumSplitterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Album Splitter")
        self.root.geometry("1000x700")
        
        self.splitter = YouTubeAlbumSplitter()
        self.preview = AudioPreview()
        self.tracks = []
        
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
        
        self.download_btn = ttk.Button(url_frame, text="Download & Analyze", command=self.download_and_analyze)
        self.download_btn.grid(row=0, column=1)
        
        url_frame.columnconfigure(0, weight=1)
        
        # Progress bar
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.status_var = tk.StringVar(value="Ready")
        status_label = ttk.Label(main_frame, textvariable=self.status_var)
        status_label.grid(row=2, column=0, columnspan=2, pady=(0, 10))
        
        # Tracks frame
        tracks_frame = ttk.LabelFrame(main_frame, text="Tracks", padding="5")
        tracks_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        
        # Treeview for tracks
        columns = ('Title', 'Start', 'End', 'Duration')
        self.tracks_tree = ttk.Treeview(tracks_frame, columns=columns, show='headings', height=15)
        
        for col in columns:
            self.tracks_tree.heading(col, text=col)
            self.tracks_tree.column(col, width=150)
        
        self.tracks_tree.column('Title', width=300)
        
        # Scrollbars for treeview
        v_scrollbar = ttk.Scrollbar(tracks_frame, orient=tk.VERTICAL, command=self.tracks_tree.yview)
        h_scrollbar = ttk.Scrollbar(tracks_frame, orient=tk.HORIZONTAL, command=self.tracks_tree.xview)
        self.tracks_tree.configure(yscrollcommand=v_scrollbar.set, xscrollcommand=h_scrollbar.set)
        
        self.tracks_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        v_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        h_scrollbar.grid(row=1, column=0, sticky=(tk.W, tk.E))
        
        tracks_frame.columnconfigure(0, weight=1)
        tracks_frame.rowconfigure(0, weight=1)
        
        # Buttons frame
        buttons_frame = ttk.Frame(tracks_frame)
        buttons_frame.grid(row=2, column=0, columnspan=2, pady=(10, 0))
        
        ttk.Button(buttons_frame, text="Add Track", command=self.add_track).grid(row=0, column=0, padx=(0, 5))
        ttk.Button(buttons_frame, text="Edit Track", command=self.edit_track).grid(row=0, column=1, padx=5)
        ttk.Button(buttons_frame, text="Delete Track", command=self.delete_track).grid(row=0, column=2, padx=5)
        ttk.Button(buttons_frame, text="Preview", command=self.preview_track).grid(row=0, column=3, padx=5)
        self.stop_preview_btn = ttk.Button(buttons_frame, text="Stop Preview", command=self.stop_preview)
        self.stop_preview_btn.grid(row=0, column=4, padx=5)
        
        # Output frame
        output_frame = ttk.LabelFrame(main_frame, text="Output", padding="5")
        output_frame.grid(row=4, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(0, 10))
        
        self.output_var = tk.StringVar(value="output")
        ttk.Label(output_frame, text="Output Directory:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(output_frame, textvariable=self.output_var, width=40).grid(row=0, column=1, padx=5, sticky=(tk.W, tk.E))
        ttk.Button(output_frame, text="Browse", command=self.browse_output).grid(row=0, column=2)
        
        output_frame.columnconfigure(1, weight=1)
        
        # Process button
        self.process_btn = ttk.Button(main_frame, text="Split Audio", command=self.split_audio, state='disabled')
        self.process_btn.grid(row=5, column=0, columnspan=2, pady=10)
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(3, weight=1)
    
    def download_and_analyze(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Error", "Please enter a YouTube URL")
            return
        
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
                track.start_time,
                track.end_time or "End",
                duration
            ))
    
    def add_track(self):
        dialog = TrackDialog(self.root, "Add Track")
        if dialog.result:
            title, start_time, end_time = dialog.result
            try:
                self.splitter.parse_timestamp(start_time)
                if end_time:
                    self.splitter.parse_timestamp(end_time)
                
                self.tracks.append(Track(title, start_time, end_time))
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
        
        dialog = TrackDialog(self.root, "Edit Track", track.title, track.start_time, track.end_time)
        if dialog.result:
            title, start_time, end_time = dialog.result
            try:
                self.splitter.parse_timestamp(start_time)
                if end_time:
                    self.splitter.parse_timestamp(end_time)
                
                track.title = title
                track.start_time = start_time
                track.end_time = end_time
                
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
    
    def preview_track(self):
        selection = self.tracks_tree.selection()
        if not selection:
            messagebox.showwarning("Warning", "Please select a track to preview")
            return
        
        if not self.splitter.audio_file:
            messagebox.showerror("Error", "No audio file available")
            return
        
        item = selection[0]
        index = self.tracks_tree.index(item)
        track = self.tracks[index]
        
        def preview_thread():
            try:
                start_sec = self.splitter.parse_timestamp(track.start_time)
                self.root.after(0, lambda: self.status_var.set("Creating preview..."))
                
                preview_file = self.preview.create_preview(self.splitter.audio_file, start_sec)
                if preview_file:
                    self.preview.current_preview = preview_file
                    self.preview.play_preview(preview_file)
                    self.root.after(0, lambda: self.status_var.set(f"Playing preview: {track.title}"))
                else:
                    self.root.after(0, lambda: messagebox.showerror("Error", "Failed to create preview"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", f"Preview error: {e}"))
        
        threading.Thread(target=preview_thread, daemon=True).start()
    
    def stop_preview(self):
        self.preview.stop_preview()
        self.status_var.set("Preview stopped")
    
    def browse_output(self):
        directory = filedialog.askdirectory()
        if directory:
            self.output_var.set(directory)
    
    def split_audio(self):
        if not self.tracks:
            messagebox.showwarning("Warning", "No tracks to process")
            return
        
        output_dir = self.output_var.get()
        if not output_dir:
            messagebox.showerror("Error", "Please specify output directory")
            return
        
        def split_thread():
            try:
                self.root.after(0, lambda: self.progress.start())
                self.root.after(0, lambda: self.process_btn.config(state='disabled'))
                
                def update_status(status):
                    self.root.after(0, lambda: self.status_var.set(status))
                
                self.splitter.split_audio(self.tracks, output_dir, update_status)
                
                self.root.after(0, lambda: self.progress.stop())
                self.root.after(0, lambda: self.process_btn.config(state='normal'))
                self.root.after(0, lambda: messagebox.showinfo("Success", f"Created {len(self.tracks)} tracks in {output_dir}"))
                update_status("Completed")
                
            except Exception as e:
                self.root.after(0, lambda: self.progress.stop())
                self.root.after(0, lambda: self.process_btn.config(state='normal'))
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
                self.root.after(0, lambda: self.status_var.set("Error"))
        
        threading.Thread(target=split_thread, daemon=True).start()

class TrackDialog:
    def __init__(self, parent, title, track_title="", start_time="0:00", end_time=""):
        self.result = None
        
        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("400x200")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        
        # Center the dialog
        self.dialog.update_idletasks()
        x = (self.dialog.winfo_screenwidth() // 2) - (400 // 2)
        y = (self.dialog.winfo_screenheight() // 2) - (200 // 2)
        self.dialog.geometry(f"400x200+{x}+{y}")
        
        frame = ttk.Frame(self.dialog, padding="20")
        frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Title
        ttk.Label(frame, text="Title:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.title_var = tk.StringVar(value=track_title)
        ttk.Entry(frame, textvariable=self.title_var, width=40).grid(row=0, column=1, sticky=(tk.W, tk.E), pady=5)
        
        # Start time
        ttk.Label(frame, text="Start Time:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.start_var = tk.StringVar(value=start_time)
        ttk.Entry(frame, textvariable=self.start_var, width=40).grid(row=1, column=1, sticky=(tk.W, tk.E), pady=5)
        
        # End time
        ttk.Label(frame, text="End Time:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.end_var = tk.StringVar(value=end_time)
        ttk.Entry(frame, textvariable=self.end_var, width=40).grid(row=2, column=1, sticky=(tk.W, tk.E), pady=5)
        
        # Buttons
        button_frame = ttk.Frame(frame)
        button_frame.grid(row=3, column=0, columnspan=2, pady=20)
        
        ttk.Button(button_frame, text="OK", command=self.ok_clicked).grid(row=0, column=0, padx=5)
        ttk.Button(button_frame, text="Cancel", command=self.cancel_clicked).grid(row=0, column=1, padx=5)
        
        frame.columnconfigure(1, weight=1)
        
        self.dialog.protocol("WM_DELETE_WINDOW", self.cancel_clicked)
        self.dialog.wait_window()
    
    def ok_clicked(self):
        title = self.title_var.get().strip()
        start_time = self.start_var.get().strip()
        end_time = self.end_var.get().strip()
        
        if not title or not start_time:
            messagebox.showerror("Error", "Title and start time are required")
            return
        
        self.result = (title, start_time, end_time if end_time else None)
        self.dialog.destroy()
    
    def cancel_clicked(self):
        self.dialog.destroy()

def main():
    # Check dependencies
    try:
        import yt_dlp
        import pygame
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("Install with: pip install yt-dlp pygame")
        sys.exit(1)
    
    # Check for ffmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ffmpeg is required. Please install ffmpeg and make sure it's in your PATH")
        sys.exit(1)
    
    root = tk.Tk()
    app = YouTubeAlbumSplitterGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()