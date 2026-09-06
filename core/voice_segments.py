"""
Voice Segments Generator - Creates individual voice segments from script blocks.
Handles TTS generation for each scene/segment of the video recap.
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import edge_tts
import re
import subprocess
import math

# ──────────────────────────────────────────────────────────────────
# FFmpeg: dùng FFmpegUtils (tự tìm từ thư mục app, env var, PATH)
# ──────────────────────────────────────────────────────────────────

from utils.helpers import FFmpegUtils
FFmpegUtils.bootstrap_environment()


def _subprocess_hidden_kwargs() -> dict:
    return FFmpegUtils.subprocess_kwargs()


def _ffmpeg_executable() -> Optional[str]:
    return FFmpegUtils.optional_ffmpeg_executable()


# ──────────────────────────────────────────────────────────────────
# Import pydub sau khi ffmpeg đã có trong PATH
# ──────────────────────────────────────────────────────────────────

try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except Exception:
    PYDUB_AVAILABLE = False
    AudioSegment = None


def _probe_audio_duration(path: str) -> float:
    """Probe audio duration bằng ffprobe, không hiện cửa sổ console."""
    try:
        result = subprocess.run(
            [
                FFmpegUtils.ffprobe_executable(),
                "-v", "quiet", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            **_subprocess_hidden_kwargs(),
        )
        if result.returncode == 0:
            return max(0.0, float((result.stdout or "0").strip() or 0.0))
    except Exception:
        pass
    return 0.0


def _concat_wavs_ffmpeg(chunk_files: List[str], output_path: str) -> None:
    """Nối nhiều file WAV bằng ffmpeg concat demuxer."""
    ffmpeg = _ffmpeg_executable()
    if not ffmpeg:
        if PYDUB_AVAILABLE and AudioSegment is not None:
            combined = AudioSegment.empty()
            for f in chunk_files:
                combined += AudioSegment.from_wav(f)
            combined.export(output_path, format="wav")
            return
        raise RuntimeError(
            "Cannot concatenate audio: ffmpeg not found.\n"
            "Place ffmpeg.exe in the app folder (data/tools/) or set FFMPEG_HOME."
        )

    # Use ffmpeg concat demuxer
    concat_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    try:
        for cf in chunk_files:
            norm = cf.replace('\\', '/')
            concat_file.write(f"file '{norm}'\n")
        concat_file.close()

        cmd = [
            ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
            '-f', 'concat', '-safe', '0',
            '-i', concat_file.name,
            '-c', 'copy', output_path,
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_subprocess_hidden_kwargs())
        _, stderr = proc.communicate()
        if proc.returncode != 0:
            # fallback: re-encode nếu WAV params không đồng nhất
            cmd2 = [
                ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
                '-f', 'concat', '-safe', '0',
                '-i', concat_file.name,
                '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '1',
                output_path,
            ]
            proc2 = subprocess.Popen(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_subprocess_hidden_kwargs())
            _, stderr2 = proc2.communicate()
            if proc2.returncode != 0:
                raise RuntimeError(
                    f"ffmpeg concat failed:\n{stderr.decode('utf-8', errors='ignore')}\n"
                    f"{stderr2.decode('utf-8', errors='ignore')}"
                )
    finally:
        try:
            os.unlink(concat_file.name)
        except Exception:
            pass


class VoiceSegment:
    """Represents a single voice segment with timing and metadata."""
    
    def __init__(self, segment_id: int, text: str, start_time: float = 0.0, 
                 end_time: float = 0.0, voice: str = "vi-VN-HoaiMyNeural", 
                 pace: str = "normal"):
        self.segment_id = segment_id
        self.text = text
        self.start_time = start_time
        self.end_time = end_time
        self.duration = end_time - start_time if end_time > start_time else 0.0
        self.voice = voice
        self.pace = pace  # normal, fast, urgent
        self.audio_path: Optional[str] = None
        self.audio_duration: float = 0.0
    
    def to_dict(self) -> Dict:
        """Convert segment to dictionary."""
        return {
            'id': self.segment_id,
            'text': self.text,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'duration': self.duration,
            'voice': self.voice,
            'pace': self.pace,
            'audio_path': self.audio_path,
            'audio_duration': self.audio_duration,
        }


class VoiceSegmentsGenerator:
    """Generates individual voice segments for video recap."""
    
    def __init__(self, output_dir: Optional[str] = None, voice: str = "vi-VN-HoaiMyNeural"):
        self.output_dir = output_dir or tempfile.mkdtemp(prefix="voice_segments_")
        self.voice = voice
        self.segments: List[VoiceSegment] = []
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def parse_script_for_segments(script: str) -> List[Dict]:
        """
        Parse script to extract segments with markers.
        Format: [KEEP] or [CUT] markers, optional [PACE] markers
        """
        segments = []
        segment_id = 0
        
        # Split by newlines and process
        lines = script.split('\n')
        current_text = []
        current_marker = None
        current_pace = "normal"
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Check for markers
            keep_match = re.search(r'\[KEEP\]', line, re.IGNORECASE)
            cut_match = re.search(r'\[CUT\]', line, re.IGNORECASE)
            pace_match = re.search(r'\[PACE:(\w+)\]', line, re.IGNORECASE)
            
            # Extract text without markers
            clean_text = re.sub(r'\[(KEEP|CUT|PACE:\w+)\]', '', line, flags=re.IGNORECASE).strip()
            
            if keep_match or cut_match:
                # Save previous segment if any
                if current_text:
                    segments.append({
                        'id': segment_id,
                        'text': ' '.join(current_text),
                        'marker': current_marker or 'KEEP',
                        'pace': current_pace,
                    })
                    segment_id += 1
                    current_text = []
                
                # Start new segment
                current_marker = 'KEEP' if keep_match else 'CUT'
                current_pace = 'normal'
                
                if pace_match:
                    current_pace = pace_match.group(1).lower()
                
                if clean_text:
                    current_text.append(clean_text)
            elif clean_text:
                current_text.append(clean_text)
        
        # Save last segment
        if current_text:
            segments.append({
                'id': segment_id,
                'text': ' '.join(current_text),
                'marker': current_marker or 'KEEP',
                'pace': current_pace,
            })
        
        return segments
    
    @staticmethod
    def _sanitize_tts_text(text: str) -> str:
        """
        Clean text for edge-tts: normalize whitespace, remove dangerous chars,
        and clip consecutive punctuation that may confuse the TTS engine.
        """
        # Remove filesystem-illegal characters
        cleaned = re.sub(r'[\\/*?:"<>|]', '', text)
        # Normalize whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        # Replace multiple consecutive punctuation (e.g. ... or !!!) with a single
        # but keep legitimate ellipsis (...) and sentence-ending punctuation.
        cleaned = re.sub(r'([.!?]){3,}', r'\1\1\1', cleaned)
        cleaned = re.sub(r'[,;:]{2,}', lambda m: m.group(0)[0], cleaned)
        # Remove leading/trailing spaces again
        cleaned = cleaned.strip()
        return cleaned

    async def generate_segment_audio(self, text: str, output_path: str, 
                                     voice: Optional[str] = None, 
                                     rate: int = 0, 
                                     max_retries: int = 3) -> float:
        """
        Generate audio for a single segment using Edge TTS.
        Returns duration in seconds.
        
        Args:
            text: Text to convert to speech
            output_path: Path to save the audio file
            voice: Voice ID (e.g., 'vi-VN-HoaiMyNeural')
            rate: Speed adjustment (-50 to 50)
            max_retries: Number of retry attempts
            
        Returns:
            Duration in seconds
        """
        voice = voice or self.voice
        
        # Validate inputs
        if not text or text.strip() == "":
            raise ValueError(f"Text cannot be empty or only whitespace: '{text}'")
        
        if len(text.strip()) < 2:
            # Very short text may cause edge-tts issues, add padding
            text = text.strip() + " ..."
        
        # Sanitize text for TTS engine
        clean_text = self._sanitize_tts_text(text)
        
        # If sanitized text becomes empty, pad it
        if len(clean_text) < 2:
            clean_text = clean_text + " ..."
        
        # CRITICAL: edge-tts has a limit of ~3000 chars per request.
        # Vietnamese text with complex characters can fail around 1500-2000.
        # Use conservative 1000 chars to avoid "No audio received" errors.
        MAX_CHARS = 1000  # Conservative safe limit for Vietnamese text
        
        if len(clean_text) > MAX_CHARS:
            return await self._generate_long_text_audio(clean_text, output_path, voice, rate, max_retries)
        
        # Validate voice format
        if not re.match(r'^[a-zA-Z-]+-[A-Z]{2}-[a-zA-Z-]+Neural$', voice):
            print(f"⚠️ Warning: Voice ID '{voice}' may not be standard edge-tts format")
        
        rate_str = f"{rate:+d}%" if rate != 0 else "+0%"
        
        # Retry loop with exponential backoff
        for attempt in range(max_retries):
            try:
                communicate = edge_tts.Communicate(clean_text, voice=voice, rate=rate_str)
                await communicate.save(output_path)
                
                # Verify file was created and has content
                if not os.path.exists(output_path) or os.path.getsize(output_path) < 100:
                    raise Exception("Generated audio file is too small or empty")
                
                # Probe directly so pydub cannot flash an FFmpeg console per segment.
                duration = _probe_audio_duration(output_path)
                if duration >= 0.1:
                    return duration
                return len(clean_text.split()) / 4.5

                # Legacy fallback kept unreachable for compatibility with old builds.
                try:
                    from pydub import AudioSegment
                    duration = len(AudioSegment.from_file(output_path)) / 1000.0
                    
                    # Validate duration is reasonable (not 0)
                    if duration < 0.1:
                        print(f"⚠️ Warning: Generated audio very short ({duration:.2f}s) for text: '{clean_text[:50]}...'")
                        duration = len(clean_text.split()) / 2.5  # Fallback estimate
                    
                    return duration
                except ImportError:
                    # Fallback: estimate based on word count
                    words = len(clean_text.split())
                    duration = words / 2.5  # ~2.5 words per second
                    return duration
                    
            except Exception as e:
                error_msg = str(e)
                if attempt < max_retries - 1:
                    backoff = 3.0 * (attempt + 1)  # longer backoff: 3s, 6s, 9s
                    print(f"⚠️ edge-tts attempt {attempt + 1}/{max_retries} failed: {error_msg}")
                    print(f"   Chars: {len(clean_text)} | Text preview: '{clean_text[:100]}...'")
                    print(f"   Voice: {voice}, Rate: {rate_str}")
                    print(f"   Retrying in {backoff:.1f}s...")
                    await asyncio.sleep(backoff)
                    continue
                else:
                    # Final attempt failed
                    if "No audio was received" in error_msg:
                        additional_diagnostics = (
                            f"\n--- DIAGNOSTICS ---"
                            f"\nText length: {len(clean_text)} chars"
                            f"\nFirst 200 chars: '{clean_text[:200]}'"
                            f"\nVoice: {voice}"
                            f"\nRate: {rate_str}"
                            f"\nText contains only ASCII: {clean_text.isascii()}"
                            f"\nNumber of Vietnamese chars: {sum(1 for c in clean_text if ord(c) > 127)}"
                        )
                        error_msg = (
                            f"No audio was received from edge-tts. Please verify:\n"
                            f"1. Text is valid: '{clean_text[:100]}...'\n"
                            f"2. Voice ID '{voice}' is correct\n"
                            f"3. Internet connection is working\n"
                            f"4. Text length: {len(clean_text)} chars (reduce if > 3000)\n"
                            f"{additional_diagnostics}"
                        )
                    raise Exception(f"Error generating audio after {max_retries} attempts: {error_msg}")
    
    async def _generate_long_text_audio(self, text: str, output_path: str, 
                                        voice: str, rate: int, max_retries: int) -> float:
        """
        Handle text longer than 2500 chars by splitting into chunks.
        Generates audio for each chunk and concatenates them.
        Uses subprocess+ffmpeg for concatenation (no pydub dependency).
        """
        MAX_CHARS = 800  # Small safe limit for sub-chunks (Vietnamese text)
        chunks = []
        total_duration = 0.0
        
        # Split text into logical pieces: by sentence boundary first, then by char limit
        sentences = re.split(r'(?<=[.!?])\s+', text)
        current_chunk = ""
        
        print(f"📝 Text is {len(text)} chars, splitting into chunks (max {MAX_CHARS} each)...")
        
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            # If a single sentence exceeds MAX_CHARS, break it by character limit
            if len(sentence) > MAX_CHARS:
                # Flush any current chunk first
                if current_chunk:
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                # Split long sentence manually into smaller pieces
                for i in range(0, len(sentence), MAX_CHARS):
                    chunks.append(sentence[i:i+MAX_CHARS])
                continue
            
            if len(current_chunk) + len(sentence) <= MAX_CHARS:
                current_chunk += sentence + " "
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = sentence + " "
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        print(f"🔀 Split into {len(chunks)} chunks")
        
        # Generate audio for each chunk
        chunk_files = []
        try:
            for i, chunk in enumerate(chunks):
                chunk_path = output_path.replace('.wav', f'_chunk_{i:02d}.wav')
                chunk_files.append(chunk_path)
                
                try:
                    duration = await self.generate_segment_audio(chunk, chunk_path, voice, rate, max_retries)
                    total_duration += duration
                    print(f"✅ Chunk {i+1}/{len(chunks)}: {duration:.2f}s")
                    # Small delay between chunks to avoid rate limiting
                    if i < len(chunks) - 1:
                        await asyncio.sleep(1.0)
                except Exception as e:
                    print(f"❌ Chunk {i+1} failed: {str(e)}")
                    # If the chunk is still large, try splitting it further as fallback
                    if len(chunk) > MAX_CHARS * 0.8:
                        print("   Chunk is large; attempting to split into smaller sub-chunks...")
                        sub_chunks = []
                        for j in range(0, len(chunk), MAX_CHARS // 2):
                            sub_chunks.append(chunk[j:j + MAX_CHARS // 2])
                        sub_chunks_ok = True
                        sub_files = []
                        total_sub_dur = 0.0
                        for si, sc in enumerate(sub_chunks):
                            sc_path = output_path.replace('.wav', f'_sub_{i:02d}_{si:02d}.wav')
                            sub_files.append(sc_path)
                            try:
                                sc_dur = await self.generate_segment_audio(
                                    sc, sc_path, voice, rate, max_retries
                                )
                                total_sub_dur += sc_dur
                                print(f"   ✅ Sub-chunk {si+1}/{len(sub_chunks)}: {sc_dur:.2f}s")
                            except Exception as se:
                                print(f"   ❌ Sub-chunk {si+1} also failed: {str(se)}")
                                sub_chunks_ok = False
                                break
                        if sub_chunks_ok and len(sub_files) > 0:
                            # Append sub-files instead of original chunk file
                            chunk_files.pop()  # remove the original failed chunk path
                            chunk_files.extend(sub_files)
                            total_duration += total_sub_dur
                            print(f"   ✅ Sub-chunk concat successful: {total_sub_dur:.2f}s total")
                            continue  # skip normal cleanup, sub-files will be cleaned later
                    # If we get here, chunk generation truly failed
                    raise Exception(f"Failed to generate chunk {i}: {str(e)}")
            
            # Concatenate all chunk/sub-chunk files using subprocess ffmpeg
            try:
                _concat_wavs_ffmpeg(chunk_files, output_path)
                print(f"✅ Concatenated {len(chunk_files)} pieces into {output_path}")
            except Exception as e:
                print(f"❌ ffmpeg concatenation failed: {str(e)}")
                raise Exception(f"Failed to concatenate audio chunks: {str(e)}")
        
        finally:
            # Clean up chunk files (including any sub-chunks)
            for chunk_file in chunk_files:
                if os.path.exists(chunk_file):
                    try:
                        os.remove(chunk_file)
                    except:
                        pass
        
        return total_duration
    
    async def generate_all_segments(self, parsed_segments: List[Dict]) -> List[VoiceSegment]:
        """
        Generate audio for all segments asynchronously.
        """
        self.segments = []
        tasks = []
        
        for segment_data in parsed_segments:
            segment_id = segment_data['id']
            text = segment_data['text']
            pace = segment_data['pace']
            
            # Map pace to rate adjustment
            rate_map = {
                'normal': 0,
                'fast': 20,
                'urgent': 30,
                'slow': -20,
            }
            rate = rate_map.get(pace, 0)
            
            # Create output path
            output_path = os.path.join(
                self.output_dir, 
                f"segment_{segment_id:03d}_{pace}.wav"
            )
            
            # Create segment object
            segment = VoiceSegment(
                segment_id=segment_id,
                text=text,
                voice=self.voice,
                pace=pace
            )
            
            # Create async task
            async def _generate(seg, path, txt, v, r):
                try:
                    duration = await self.generate_segment_audio(txt, path, v, r)
                    seg.audio_path = path
                    seg.audio_duration = duration
                    return seg
                except Exception as e:
                    raise Exception(f"Segment {seg.segment_id} failed: {str(e)}")
            
            tasks.append(_generate(segment, output_path, text, self.voice, rate))
            self.segments.append(segment)
        
        # Run generation tasks SEQUENTIALLY to avoid rate limiting
        # (parallel requests to edge-tts often cause "No audio received" errors)
        if tasks:
            for i, task in enumerate(tasks):
                try:
                    result = await task
                    print(f"🎤 Segment {i+1}/{len(tasks)} done")
                    # Small delay between segments to avoid rate limiting
                    if i < len(tasks) - 1:
                        await asyncio.sleep(0.5)
                except Exception as e:
                    raise e
        
        return self.segments
    
    def generate_segments_sync(self, script: str, voice: Optional[str] = None) -> List[VoiceSegment]:
        """
        Synchronous wrapper for segment generation.
        """
        voice = voice or self.voice
        self.voice = voice
        
        # Parse script into segments
        parsed = self.parse_script_for_segments(script)
        
        # Generate audio using asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        segments = loop.run_until_complete(self.generate_all_segments(parsed))
        return segments
    
    def get_segments_info(self) -> List[Dict]:
        """Get metadata about all segments."""
        return [seg.to_dict() for seg in self.segments]
    
    def export_segments_metadata(self, output_file: str):
        """Export segment metadata to JSON file."""
        import json
        metadata = {
            'total_segments': len(self.segments),
            'total_duration': sum(seg.audio_duration for seg in self.segments),
            'segments': self.get_segments_info(),
        }
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
