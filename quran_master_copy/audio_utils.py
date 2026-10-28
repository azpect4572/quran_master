import os

RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recordings')


RECITERS = {
    "alafasy": ("Alafasy", "Alafasy_128kbps"),
    "minshawy_murattal": ("Al Minshawy Murattal", "Minshawy_Murattal_128kbps"),
    "minshawy_mujawwad": ("Al Minshawy Mujawwad", "Minshawy_Mujawwad_64kbps"),
    "maher": ("Maher Al Muaiqly", "MaherAlMuaiqly128kbps"),
    "ayoub": ("Muhammad Ayoub", "Muhammad_Ayyoub_128kbps"),
    "qatami": ("Nasser Al Qatami", "Nasser_Alqatami_128kbps"),
    "dussary": ("Yasser Ad-Dussary", "Yasser_Ad-Dussary_128kbps"),
    "budair": ("Salah Al Budair", "Salah_Al_Budair_128kbps"),
    "abdulkareem": ("Muhammad AbdulKareem", "Muhammad_AbdulKareem_128kbps"),
    "shuraym": ("Saood Ash-Shuraym", "Saood_ash-Shuraym_128kbps"),
}

def build_audio_url(surah_id, ayah_number, reciter_key):
    """Build the EveryAyah.com audio URL for a given Surah/Ayah/reciter.

    EveryAyah serves each Ayah as its own mp3, named "{surah}{ayah}.mp3"
    where both numbers are zero-padded to 3 digits (e.g. Surah 1 Ayah 1 ->
    "001001.mp3", Surah 114 Ayah 6 -> "114006.mp3"), inside a per-reciter
    folder: https://everyayah.com/data/{reciter_folder}/{surah:03d}{ayah:03d}.mp3
    """
    # look up the reciter's EveryAyah folder name directly by key
    _, folder = RECITERS.get(reciter_key, RECITERS["alafasy"])

    # :03d zero-pads each number to 3 digits, matching EveryAyah's naming scheme
    return (f"https://everyayah.com/data/{folder}/{surah_id:03d}{ayah_number:03d}.mp3")
