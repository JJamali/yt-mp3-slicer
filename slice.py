#!/usr/bin/env python3
"""
YouTube Album Splitter
A tool to download YouTube videos and split them into individual MP3 tracks.
"""

import os
import re
import sys
import subprocess
from typing import List, Tuple, Optional
import yt_dlp
from datetime import datetime, timedelta

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
            # Handle formats like "1:23", "12:34", "1:23:45"
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
        
        # Common patterns for timestamps in descriptions
        patterns = [
            r'(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–—]\s*(.+)',  # 1:23 - Song Title
            r'(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)',          # 1:23 Song Title
            r'(.+?)\s*[-–—]\s*(\d{1,2}:\d{2}(?::\d{2})?)', # Song Title - 1:23
        ]
        
        lines = description.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            for pattern in patterns:
                matches = re.findall(pattern, line)
                for match in matches:
                    if pattern.endswith(r'(\d{1,2}:\d{2}(?::\d{2})?)'):
                        # Title comes first
                        title, timestamp = match
                    else:
                        # Timestamp comes first
                        timestamp, title = match
                    
                    # Clean up the title
                    title = re.sub(r'^[\d\.\)\]\-–—\s]+', '', title).strip()
                    title = re.sub(r'[\[\(].*?[\]\)]', '', title).strip()
                    
                    if title and len(title) > 2:  # Reasonable title length
                        try:
                            self.parse_timestamp(timestamp)  # Validate timestamp
                            tracks.append(Track(title, timestamp))
                        except ValueError:
                            continue
        
        # Sort tracks by timestamp
        tracks.sort(key=lambda t: self.parse_timestamp(t.start_time))
        
        # Add end times
        for i in range(len(tracks) - 1):
            tracks[i].end_time = tracks[i + 1].start_time
        
        return tracks
    
    def download_audio(self, url: str) -> str:
        """Download audio from YouTube video"""
        print("Downloading audio from YouTube...")
        
        ydl_opts = {
            'format': 'bestaudio/best',
            'extractaudio': True,
            'audioformat': 'mp3',
            'outtmpl': 'temp_audio.%(ext)s',
            'quiet': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Get video info
            self.video_info = ydl.extract_info(url, download=False)
            
            # Download audio
            ydl.download([url])
        
        # Find the downloaded file
        for file in os.listdir('.'):
            if file.startswith('temp_audio'):
                self.audio_file = file
                break
        
        if not self.audio_file:
            raise Exception("Failed to download audio file")
        
        print(f"Audio downloaded: {self.audio_file}")
        return self.audio_file
    
    def split_audio(self, tracks: List[Track], output_dir: str = "output"):
        """Split audio file into individual tracks"""
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        print(f"Splitting audio into {len(tracks)} tracks...")
        
        for i, track in enumerate(tracks, 1):
            start_seconds = self.parse_timestamp(track.start_time)
            
            # Sanitize filename
            safe_title = re.sub(r'[<>:"/\\|?*]', '', track.title)
            safe_title = safe_title[:100]  # Limit length
            output_file = os.path.join(output_dir, f"{i:02d}. {safe_title}.mp3")
            
            # Build ffmpeg command
            cmd = [
                'ffmpeg', '-i', self.audio_file,
                '-ss', str(start_seconds),
                '-y'  # Overwrite without asking
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
                print(f"✓ Created: {os.path.basename(output_file)}")
            except subprocess.CalledProcessError as e:
                print(f"✗ Failed to create: {os.path.basename(output_file)}")
                print(f"Error: {e.stderr.decode()}")
    
    def cleanup(self):
        """Remove temporary files"""
        if self.audio_file and os.path.exists(self.audio_file):
            os.remove(self.audio_file)
            print("Cleaned up temporary files")
    
    def manual_track_input(self) -> List[Track]:
        """Allow user to manually input track information"""
        tracks = []
        print("\nEnter track information manually:")
        print("Format: Title | Start Time | End Time (optional)")
        print("Time format: mm:ss or hh:mm:ss")
        print("Enter empty line when done\n")
        
        while True:
            user_input = input(f"Track {len(tracks) + 1}: ").strip()
            if not user_input:
                break
            
            parts = [p.strip() for p in user_input.split('|')]
            if len(parts) < 2:
                print("Invalid format. Use: Title | Start Time | End Time (optional)")
                continue
            
            title = parts[0]
            start_time = parts[1]
            end_time = parts[2] if len(parts) > 2 and parts[2] else None
            
            try:
                self.parse_timestamp(start_time)  # Validate
                if end_time:
                    self.parse_timestamp(end_time)  # Validate
                
                tracks.append(Track(title, start_time, end_time))
                print(f"Added: {tracks[-1]}")
            except ValueError as e:
                print(f"Error: {e}")
        
        return tracks

def main():
    splitter = YouTubeAlbumSplitter()
    
    try:
        # Get YouTube URL
        url = input("Enter YouTube video URL: ").strip()
        if not url:
            print("No URL provided")
            return
        
        # Download audio
        audio_file = splitter.download_audio(url)
        
        # Try to extract tracks from description
        description = splitter.video_info.get('description', '')
        auto_tracks = splitter.extract_timestamps_from_description(description)
        
        if auto_tracks:
            print(f"\nFound {len(auto_tracks)} tracks in description:")
            for i, track in enumerate(auto_tracks, 1):
                print(f"{i:2d}. {track}")
            
            choice = input("\nUse these tracks? (y/n/m for manual): ").lower()
            
            if choice == 'y':
                tracks = auto_tracks
            elif choice == 'm':
                tracks = splitter.manual_track_input()
            else:
                tracks = []
        else:
            print("No tracks found in description.")
            choice = input("Enter tracks manually? (y/n): ").lower()
            if choice == 'y':
                tracks = splitter.manual_track_input()
            else:
                tracks = []
        
        if not tracks:
            print("No tracks to process.")
            return
        
        # Split audio
        output_dir = input("Output directory (default: output): ").strip() or "output"
        splitter.split_audio(tracks, output_dir)
        
        print(f"\nCompleted! {len(tracks)} tracks saved to '{output_dir}' directory")
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        splitter.cleanup()

if __name__ == "__main__":
    # Check dependencies
    try:
        import yt_dlp
    except ImportError:
        print("yt-dlp is required. Install with: pip install yt-dlp")
        sys.exit(1)
    
    # Check for ffmpeg
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ffmpeg is required. Please install ffmpeg and make sure it's in your PATH")
        sys.exit(1)
    
    main()