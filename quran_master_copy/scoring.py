# Relative weights applied to duration, amplitude, and pause factors when computing the overall score
DURATION_WEIGHT = 0.30
AMPLITUDE_WEIGHT = 0.40
PAUSE_WEIGHT = 0.30

# Minimum scores required to qualify for each rating band
GOOD_THRESHOLD = 80
SATISFACTORY_THRESHOLD = 41

# Minimum surah score required (alongside every Ayah having been attempted)  for a surah's overall status to count as 'Completed' 
COMPLETED_SURAH_THRESHOLD = 70

# RMS amplitude below which a frame counts as a pause/silence - starting
# value only, will likely need tuning once tested against real recordings
SILENCE_THRESHOLD = 0.02

# Max acceptable difference (in seconds) between reference and user pause duration
PAUSE_TOLERANCE_SECONDS = 3.0

import io
import os
import subprocess

import librosa
import numpy as np
import requests

from audio_utils import RECORDINGS_DIR, build_audio_url

# Audio loading
# Downloads the reference recitation and loads it into a waveform straight from memory
def load_reference_audio(surah_id, ayah_number, reciter_key='alafasy'):
    url = build_audio_url(surah_id, ayah_number, reciter_key)

    try:
        response = requests.get(url, timeout=10)
    except requests.RequestException as exc:
        # Covers connection failures, timeouts, DNS errors etc.
        raise RuntimeError(f'Failed to download reference audio from {url}: {exc}') from exc

    if response.status_code != 200:
        raise RuntimeError(
            f'Failed to download reference audio from {url}: HTTP {response.status_code}'
        )

    if not response.content:
        # Covers a 200 response with no actual audio data in it
        raise RuntimeError(f'Downloaded reference audio from {url} was empty.')

    # librosa can load directly from an in-memory no need to write the download to disk first
    y, sr = librosa.load(io.BytesIO(response.content), sr=None)
    return y, sr




# Locates the user's submitted recording on disk and loads it into a waveform
def load_user_recording(
    surah_id,
    ayah_number,
    timestamp,
    extension='.webm'
):
    # Build path to original browser recording
    filename = f'{surah_id}_{ayah_number}_{timestamp}{extension}'
    file_path = os.path.join(RECORDINGS_DIR, filename)

    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f'No submitted recording found at {file_path}.'
        )

    # Create a Wav version for librosa to analyse
    wav_filename = f'{surah_id}_{ayah_number}_{timestamp}.wav'
    wav_path = os.path.join(RECORDINGS_DIR, wav_filename)

    try:
        subprocess.run(
            [
                'ffmpeg',
                '-y',
                '-i', file_path,
                '-vn',
                '-acodec', 'pcm_s16le',
                wav_path
            ],
            check=True,
            capture_output=True,
            text=True
        )

    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f'Failed to convert recording to WAV: {exc.stderr}'
        ) from exc

    # WAV can now be loaded for feature extraction
    y, sr = librosa.load(
        wav_path,
        sr=None
    )

    return y, sr



# Extracts the three raw audio features needed for scoring. Works on both reference + user recordings
def extract_features(y, sr):
    # Duration in seconds 
    duration = len(y) / sr

    # Frame-based RMS (root mean square) energy across the whole recording.
    # This gives one loudness value per short frame rather than per raw sample, which pause detection and average amplitude are both based on.
    rms = librosa.feature.rms(y=y)[0]
    hop_length = 512  # default hop length librosa.feature.rms uses internally

    # Count how many RMS frames fall below the silence threshold, then convert that frame count back into seconds using the hop length
    pause_frames = np.sum(rms < SILENCE_THRESHOLD)
    pause_duration = pause_frames * hop_length / sr

    # Average loudness across the whole recording
    amplitude = float(np.mean(rms)) 

    return {
        'duration': duration,
        'pause_duration': pause_duration,
        'amplitude': amplitude
    }


# Comparison / scoring
# Converts a single raw feature difference into a similarity percentage.
def compare_feature(reference_value, user_value):
    similarity = max(
        0,
        100 - abs(reference_value - user_value) / reference_value * 100
    )

    return similarity


def compare_pause(reference_pause, user_pause, tolerance_seconds=PAUSE_TOLERANCE_SECONDS):
    similarity = max(
        0,
        100 - abs(reference_pause - user_pause) / tolerance_seconds * 100
    )

    return similarity


def calculate_weighted_score(reference_features, user_features):
    # Compare reference vs user duration, returns a similarity percentage
    duration_score = compare_feature(
        reference_features['duration'],
        user_features['duration']
    )

    # Compare reference vs user pause duration - uses compare_pause()
    pause_score = compare_pause(
        reference_features['pause_duration'],
        user_features['pause_duration']
    )

    # Compare reference vs user amplitude 
    amplitude_score = compare_feature(
        reference_features['amplitude'],
        user_features['amplitude']
    )

    # Combine the similarity percentages into one final score, each weighted according to the constants defined earlier
    weighted_score = (
        duration_score * DURATION_WEIGHT
        + pause_score * PAUSE_WEIGHT
        + amplitude_score * AMPLITUDE_WEIGHT
    )

    return weighted_score


#  Score rating
# Converts a final weighted score into a rating using the thresholds from before. 
def get_score_rating(score):
    if score >= GOOD_THRESHOLD:
        return 'Good'
    elif score >= SATISFACTORY_THRESHOLD:
        return 'Satisfactory'
    else:
        return 'Weak'


if __name__ == '__main__':
    #manual check that both audio sources actually load 
    surah_id = 1
    ayah_number = 1
    session_timestamp = '20260101120000'  # replace with a real recording's timestamp


    ref_y, ref_sr = load_reference_audio(surah_id, ayah_number)
    print(f'Reference audio loaded: sample_rate={ref_sr}, samples={len(ref_y)}')


    user_y, user_sr = load_user_recording(surah_id, ayah_number, session_timestamp)
    print(f'User recording loaded: sample_rate={user_sr}, samples={len(user_y)}')

    # just the raw extracted numbers for both recordings - no comparison or
    # scoring yet
    ref_features = extract_features(ref_y, ref_sr)
    print(
        f"Reference — Duration: {ref_features['duration']:.1f}s, "
        f"Pauses: {ref_features['pause_duration']:.1f}s, "
        f"Amplitude: {ref_features['amplitude']:.2f}"
    )

    user_features = extract_features(user_y, user_sr)
    print(
        f"User — Duration: {user_features['duration']:.1f}s, "
        f"Pauses: {user_features['pause_duration']:.1f}s, "
        f"Amplitude: {user_features['amplitude']:.2f}"
    )
