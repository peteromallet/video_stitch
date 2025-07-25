#!/usr/bin/env python3
"""
Video Stitching Script
Efficiently concatenates multiple video files using ffmpeg.
Supports handling large numbers of videos (tested with 1900+ files).
"""

import os
import sys
import subprocess
import argparse
import glob
from pathlib import Path
import tempfile
import time

def find_video_files(directory, extensions=None):
    """Find all video files in the given directory."""
    if extensions is None:
        extensions = ['*.mp4', '*.avi', '*.mov', '*.mkv', '*.flv', '*.wmv', '*.m4v', '*.webm']
    
    video_files = []
    for ext in extensions:
        pattern = os.path.join(directory, '**', ext)
        video_files.extend(glob.glob(pattern, recursive=True))
    
    # Sort files naturally (handles numeric sequences properly)
    video_files.sort()
    return video_files

def check_ffmpeg():
    """Check if ffmpeg is installed and accessible."""
    try:
        subprocess.run(['ffmpeg', '-version'], 
                      stdout=subprocess.DEVNULL, 
                      stderr=subprocess.DEVNULL, 
                      check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False

def create_file_list(video_files, temp_dir):
    """Create a temporary file list for ffmpeg concat demuxer."""
    file_list_path = os.path.join(temp_dir, 'video_list.txt')
    
    with open(file_list_path, 'w') as f:
        for video_file in video_files:
            # Escape single quotes and use absolute paths
            abs_path = os.path.abspath(video_file)
            escaped_path = abs_path.replace("'", "'\"'\"'")
            f.write(f"file '{escaped_path}'\n")
    
    return file_list_path

def get_video_info(video_file):
    """Get basic info about a video file."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', video_file
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return True
    except subprocess.CalledProcessError:
        return False

def stitch_videos(input_dir, output_file, check_compatibility=True, numbered=False, target_resolution=None):
    """
    Stitch multiple videos together.
    
    Args:
        input_dir: Directory containing video files
        output_file: Output video file path
        check_compatibility: Whether to check video compatibility first
        numbered: Overlay the clip index in the bottom-left corner of every
                   video when True.
        target_resolution: Optional tuple (width, height) for 4:3 output
    """
    
    print("🎬 Video Stitching Script Started")
    print("=" * 50)
    
    # Check if ffmpeg is available
    if not check_ffmpeg():
        print("❌ Error: ffmpeg is not installed or not in PATH")
        print("Please install ffmpeg first:")
        print("  macOS: brew install ffmpeg")
        print("  Ubuntu: sudo apt install ffmpeg")
        print("  Windows: Download from https://ffmpeg.org/")
        return False
    
    print("✅ ffmpeg found")
    
    # Find video files
    print(f"🔍 Searching for video files in: {input_dir}")
    video_files = find_video_files(input_dir)
    
    if not video_files:
        print("❌ No video files found!")
        return False
    
    print(f"📁 Found {len(video_files)} video files")
    
    # Set default resolution if not provided
    if target_resolution is None:
        target_resolution = (1280, 960)
    target_w, target_h = target_resolution

    # Check compatibility if requested
    if check_compatibility:
        print("🔄 Checking video compatibility...")
        incompatible_files = []
        for i, video_file in enumerate(video_files[:10]):  # Check first 10 files as sample
            if not get_video_info(video_file):
                incompatible_files.append(video_file)
            if i % 100 == 0:
                print(f"  Checked {i+1}/{min(10, len(video_files))} files...")
        
        if incompatible_files:
            print(f"⚠️  Found {len(incompatible_files)} potentially incompatible files")
    
    # ------------------------------------------------------------------
    # NEW PIPELINE: We first pre-process each clip into a temporary 4:3
    #               version (with optional numbering overlay). After all clips
    #               are rendered we concatenate them via stream copy which is
    #               fast and avoids an additional full-length re-encode.
    # ------------------------------------------------------------------

    with tempfile.TemporaryDirectory() as temp_dir:
        processed_files = []

        print("🛠️  Re-encoding each clip to 4:3{}...".format(" with numbering" if numbered else ""))

        for idx, src in enumerate(video_files, start=1):
            dst = os.path.join(temp_dir, f"processed_{idx:05d}.mp4")

            # Build the mandatory 4:3 filter (scale -> crop -> pad)
            vf_parts = [
                f"scale=-1:{target_h}",
                f"crop='min(iw,{target_w})':'min(ih,{target_h})'",
                f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"
            ]

            # Optional numbering overlay
            if numbered:
                vf_parts.append(
                    f"drawtext=text={idx}:fontcolor=black:fontsize=48:box=1:boxcolor=white@1:boxborderw=20:x=20:y=20"
                )

            vf_filter = ",".join(vf_parts)

            cmd = [
                "ffmpeg", "-loglevel", "error", "-y", "-i", src,
                "-vf", vf_filter,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-preset", "medium",
                dst
            ]

            print(f"  ▶️  Processing clip {idx}/{len(video_files)}", end="\r")

            try:
                subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError:
                print(f"\n❌ Failed to process '{src}'. Skipping…")
                continue

            processed_files.append(dst)

        print("\n✅ Pre-processing complete. {}/{} clips processed.".format(len(processed_files), len(video_files)))

        if not processed_files:
            print("❌ No clips could be processed. Abort.")
            return False

        # Create concat list for processed files
        file_list_path = create_file_list(processed_files, temp_dir)

        # Final concatenation using stream copy (very fast)
        cmd = [
            'ffmpeg', '-f', 'concat', '-safe', '0', '-i', file_list_path,
            '-c', 'copy', '-y', output_file
        ]

        print("🚀 Concatenating processed clips…")
        
        start_time = time.time()
        
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )

            # Monitor concatenation progress (based on stderr lines)
            while True:
                output_line = process.stderr.readline()
                if output_line == '' and process.poll() is not None:
                    break
                if output_line and 'frame=' in output_line:
                    print(output_line.strip(), end='\r')

            process.wait()
            
            if process.returncode == 0:
                elapsed_time = time.time() - start_time
                print(f"\n✅ Success! Video stitching completed in {elapsed_time:.1f} seconds")
                print(f"📼 Output saved as: {output_file}")
                
                # Show output file size
                if os.path.exists(output_file):
                    size_mb = os.path.getsize(output_file) / (1024 * 1024)
                    print(f"📊 Output file size: {size_mb:.1f} MB")
                
                return True
            else:
                error_output = process.stderr.read()
                print(f"\n❌ Error during concatenation:")
                print(error_output)
                return False
                
        except KeyboardInterrupt:
            print("\n⏹️  Operation cancelled by user")
            return False
        except Exception as e:
            print(f"\n❌ Unexpected error: {e}")
            return False

def main():
    parser = argparse.ArgumentParser(description='Stitch multiple videos together')
    parser.add_argument('input_dir', nargs='?', default=None,
                       help='Directory containing video files (optional if --target is provided)')
    parser.add_argument('-t', '--target', dest='target_dir',
                       help='Target directory containing video files (overrides positional input_dir)')
    parser.add_argument('-o', '--output', 
                       default='stitched_video.mp4',
                       help='Output video file name (default: stitched_video.mp4)')
    parser.add_argument('--re-encode', 
                       action='store_true',
                       help='Re-encode videos for compatibility (slower but more reliable)')
    parser.add_argument('--no-check', 
                       action='store_true',
                       help='Skip compatibility check')
    # Always convert to 4:3 so no flag for this any more. The user can still
    # choose a target resolution provided it is 4:3.
    parser.add_argument('--resolution', default='1280x960',
                       help="Resolution for 4:3 output in WIDTHxHEIGHT format (default: 1280x960)")

    # Optional flag to overlay the clip number in the bottom-left corner of
    # each video.
    parser.add_argument('--numbered', action='store_true',
                       help='Overlay the clip index (starting at 1) in the bottom-left corner of every video')

    args = parser.parse_args()

    # Determine which directory to use
    input_dir = args.target_dir if args.target_dir else args.input_dir

    if input_dir is None:
        print("❌ Error: You must specify a directory of videos either as a positional argument or with --target")
        sys.exit(1)

    # Validate input directory
    if not os.path.isdir(input_dir):
        print(f"❌ Error: '{input_dir}' is not a valid directory")
        sys.exit(1)


    # Converting to 4:3 is now mandatory, therefore we must re-encode at least
    # once. In addition, numbering requires re-encoding as well.
    args.re_encode = True

    # Parse resolution string
    try:
        res_parts = args.resolution.lower().split('x')
        res_w, res_h = int(res_parts[0]), int(res_parts[1])
        if res_w * 3 != res_h * 4:
            print("⚠️  Warning: The provided resolution is not 4:3. Using 4:3 equivalent of height.")
            # Adjust res_w to maintain 4:3 relative to height
            res_w = int(res_h * 4 / 3)
    except Exception:
        print("❌ Invalid --resolution format. Use WIDTHxHEIGHT (e.g., 1280x960)")
        sys.exit(1)

    # Run the stitching process
    success = stitch_videos(
        input_dir=input_dir,
        output_file=args.output,
        check_compatibility=not args.no_check,
        numbered=args.numbered,
        target_resolution=(res_w, res_h)
    )
    
    if not success:
        sys.exit(1)

if __name__ == '__main__':
    main() 